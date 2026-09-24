from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.cases.schemas import (
    ApprovalRequestedEvent,
    ApprovalResolvedEvent,
    ApprovalTimedOutEvent,
    CaseEventVariant,
)
from tracecat.cases.service import (
    CaseEventsService,
    CasesService,
    get_case_ids_for_execution,
)
from tracecat.db.models import ApprovalVote, Group, GroupMember, Interaction
from tracecat.identifiers.workflow import WorkflowExecutionID
from tracecat.interactions.enums import InteractionStatus, InteractionType
from tracecat.interactions.schemas import InteractionInput, InteractionResult
from tracecat.interactions.types import InteractionState
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService

if TYPE_CHECKING:
    from tracecat.dsl.workflow import DSLWorkflow


# EE-specific models that aren't shared with core
class InteractionRead(BaseModel):
    """Model for reading an interaction."""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    type: InteractionType
    status: InteractionStatus
    request_payload: dict[str, Any] | None
    response_payload: dict[str, Any] | None
    expires_at: datetime | None = None
    # Where this came from
    wf_exec_id: WorkflowExecutionID
    actor: str | None
    action_ref: str
    action_type: str
    current_approvals: int | None = None
    """Count of distinct `approve` votes recorded so far, while the
    interaction is still `PENDING`. Callers that don't need this (e.g. the
    interaction list embedded on `WorkflowExecutionRead`) leave it `None`
    rather than paying for the extra query — populated explicitly by
    `list_pending_approvals` (`tracecat/cases/router.py`), the one place
    that currently surfaces it. Not meaningful once resolved: at that point
    the real per-voter breakdown lives in `response_payload["votes"]`."""


class InteractionCreate(BaseModel):
    """Model for creating a new interaction."""

    type: InteractionType
    status: InteractionStatus
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None
    expires_at: datetime | None = None
    actor: str | None = None
    wf_exec_id: WorkflowExecutionID
    action_ref: str
    action_type: str


class ApprovalVoteResult(BaseModel):
    """Outcome of recording one vote on an approval interaction."""

    resolution: str | None
    """"approved" or "rejected" if this vote resolved it, else None (still pending)."""
    approve_count: int
    required_approvers: int


class InteractionUpdate(BaseModel):
    """Model for updating an interaction."""

    status: InteractionStatus | None = None
    response_payload: dict[str, Any] | None = None
    actor: str | None = None


class CreateInteractionActivityInputs(BaseModel):
    """Inputs for the create interaction activity."""

    role: Role
    params: InteractionCreate


class UpdateInteractionActivityInputs(BaseModel):
    """Inputs for the update interaction activity."""

    role: Role
    interaction_id: uuid.UUID
    params: InteractionUpdate


class ResolveApproverGroupMembersActivityInputs(BaseModel):
    """Inputs for resolving approver group names to a member snapshot."""

    role: Role
    group_names: list[str]


async def _mirror_approval_event_to_cases(
    *,
    session: AsyncSession,
    role: Role,
    wf_exec_id: WorkflowExecutionID,
    event: CaseEventVariant,
) -> None:
    """Write an approval-gate lifecycle event onto every case this
    execution is linked to, so it shows up in the case Activity tab
    alongside everything else — no second, approval-specific timeline.

    Best-effort in the fullest sense: this must never be the reason a vote,
    an approval request, or a timeout fails to go through. No linked case
    is expected, not an error (approval gates work on any execution,
    case-linked or not) — but any other failure here (a missing
    organization_id on a service-role call, a transient DB error) is
    logged and swallowed too, rather than propagated. Non-committing
    (matches `CaseEventsService.create_event`'s own contract) — callers
    commit once, at whatever point in their own transaction they already
    do.
    """
    try:
        if role.workspace_id is None or role.organization_id is None:
            return
        case_ids = await get_case_ids_for_execution(
            session, role.workspace_id, wf_exec_id
        )
        if not case_ids:
            return
        cases_service = CasesService(session=session, role=role)
        events_service = CaseEventsService(session=session, role=role)
        for case_id in case_ids:
            case = await cases_service.get_case(case_id)
            if case is None:
                continue
            await events_service.create_event(case, event, publish_case_trigger=False)
    except Exception:
        logger.warning(
            "Failed to mirror approval event to linked cases",
            wf_exec_id=wf_exec_id,
            event_type=event.type,
            exc_info=True,
        )


class InteractionManager:
    """Manages interactions for a workflow."""

    def __init__(self, workflow: DSLWorkflow) -> None:
        self.wf = workflow
        # DB interaction states are the source of truth, but we still need to track
        # the state of the interaction in the workflow
        self.states: dict[uuid.UUID, InteractionState] = {}

    def validate_interaction(self, input: InteractionInput) -> None:
        """Validate that a received interaction matches its expected state.

        Args:
            input: The interaction handler input to validate

        Raises:
            ValueError: If the interaction state cannot be found or is invalid
        """
        if input.interaction_id not in self.states:
            raise ValueError(
                "Workflow interaction handler could not find interaction state"
            )
        if self.wf.wf_exec_id != input.execution_id:
            raise ValueError(
                "Workflow interaction handler received invalid execution ID"
            )

    def handle_interaction(self, input: InteractionInput) -> InteractionResult:
        """Process a received interaction in the workflow.

        Args:
            input: The interaction handler input to process

        Returns:
            The interaction handler result containing the processed data

        Raises:
            ApplicationError: If the interaction is unknown
        """
        self.wf.logger.info(
            "Received interaction", id=input.interaction_id, action_ref=input.action_ref
        )
        if input.interaction_id not in self.states:
            self.wf.logger.warning(
                "Received interaction for unknown action",
                interaction_id=input.interaction_id,
            )
            raise ApplicationError(
                "Received interaction for unknown action", non_retryable=True
            )

        self.states[input.interaction_id].data = input.data
        self.states[input.interaction_id].status = InteractionStatus.COMPLETED
        return InteractionResult(message="success", detail=input.data)

    async def prepare_interaction(
        self,
        action_ref: str,
        action_type: str,
        interaction_type: InteractionType,
        request_payload: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        # Create an interaction record in the database
        # Create an idle interaction state if it doesn't exist
        interaction_id = await workflow.execute_activity(
            InteractionService.create_interaction_activity,
            arg=CreateInteractionActivityInputs(
                role=self.wf.role,
                params=InteractionCreate(
                    wf_exec_id=self.wf.wf_exec_id,
                    action_ref=action_ref,
                    action_type=action_type,
                    type=interaction_type,
                    status=InteractionStatus.IDLE,
                    request_payload=request_payload,
                ),
            ),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        self.wf.logger.warning(
            "Created interaction",
            id=interaction_id,
            action_ref=action_ref,
            action_type=action_type,
            type=interaction_type,
        )
        self.states[interaction_id] = InteractionState(
            type=interaction_type,
            action_ref=action_ref,
            status=InteractionStatus.IDLE,
        )
        return interaction_id

    """Actions"""

    async def wait_for_response(
        self, interaction_id: uuid.UUID, timeout: float | None = None
    ) -> dict[str, Any]:
        """Handle a wait response action within the workflow.

        Args:
            task: The action statement containing wait response parameters

        Returns:
            The interaction response data

        Raises:
            ApplicationError: If the interaction times out or encounters an error
        """

        self.wf.logger.info("Waiting for response", interaction_id=interaction_id)
        try:
            self.states[interaction_id].status = InteractionStatus.PENDING
            await self._update_interaction(
                interaction_id=interaction_id,
                params=InteractionUpdate(status=InteractionStatus.PENDING),
            )
            await workflow.wait_condition(
                # This state needs to be locally tracked
                lambda: self.states[interaction_id].is_activated(),
                timeout=timeout,
            )
            # Complete the interaction
            await self._update_interaction(
                interaction_id=interaction_id,
                params=InteractionUpdate(
                    status=InteractionStatus.COMPLETED,
                    response_payload=self.states[interaction_id].data,
                ),
            )
            self.wf.logger.info("Received response", interaction_id=interaction_id)
            return self.states[interaction_id].data
        except TimeoutError as e:
            await self._update_interaction(
                interaction_id=interaction_id,
                params=InteractionUpdate(status=InteractionStatus.TIMED_OUT),
            )
            self.wf.logger.error(
                "Timeout waiting for response",
                interaction_id=interaction_id,
                exc=e,
            )
            raise ApplicationError(
                "Timeout waiting for response", non_retryable=True
            ) from e
        except Exception as e:
            await self._update_interaction(
                interaction_id=interaction_id,
                params=InteractionUpdate(status=InteractionStatus.ERROR),
            )
            self.wf.logger.error(
                "Error waiting for response", interaction_id=interaction_id, exc=e
            )
            raise e

    async def _update_interaction(
        self, interaction_id: uuid.UUID, params: InteractionUpdate
    ) -> uuid.UUID:
        return await workflow.execute_activity(
            InteractionService.update_interaction_activity,
            arg=UpdateInteractionActivityInputs(
                role=self.wf.role, interaction_id=interaction_id, params=params
            ),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


class InteractionService(BaseWorkspaceService):
    service_name = "interactions"

    async def create_interaction(self, params: InteractionCreate) -> Interaction:
        """Create a new interaction record in the database.

        Args:
            params: Parameters for creating the interaction

        Returns:
            The created interaction
        """
        interaction = Interaction(
            wf_exec_id=params.wf_exec_id,
            action_ref=params.action_ref,
            action_type=params.action_type,
            type=params.type,
            status=params.status,
            request_payload=params.request_payload,
            response_payload=params.response_payload,
            expires_at=params.expires_at,
            actor=params.actor,
            workspace_id=self.workspace_id,
        )
        self.session.add(interaction)
        await self.session.commit()
        await self.session.refresh(interaction)
        return interaction

    async def get_interaction(self, interaction_id: uuid.UUID) -> Interaction | None:
        """Get an interaction by ID.

        Args:
            interaction_id: UUID of the interaction to retrieve

        Returns:
            The interaction if found, None otherwise
        """
        statement = select(Interaction).where(
            Interaction.workspace_id == self.workspace_id,
            Interaction.id == interaction_id,
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def update_interaction(
        self, interaction: Interaction, params: InteractionUpdate
    ) -> Interaction:
        """Update an existing interaction.

        Args:
            interaction_id: UUID of the interaction to update
            params: Update parameters

        Returns:
            Updated interaction if found, None otherwise
        """
        update_data = params.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(interaction, key, value)

        self.session.add(interaction)
        await self.session.commit()
        await self.session.refresh(interaction)
        return interaction

    async def list_interactions(
        self, *, wf_exec_id: str | None = None
    ) -> Sequence[Interaction]:
        """List all interactions for a workflow execution.

        Args:
            wf_exec_id: Workflow execution ID to filter by

        Returns:
            Sequence of interactions for the workflow
        """
        statement = select(Interaction).where(
            Interaction.workspace_id == self.workspace_id
        )
        if wf_exec_id:
            statement = statement.where(Interaction.wf_exec_id == wf_exec_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def delete_interaction(self, interaction: Interaction) -> None:
        """Delete an interaction by ID.

        Args:
            interaction: The interaction to delete
        """
        await self.session.delete(interaction)
        await self.session.commit()

    async def resolve_group_member_ids(self, group_names: list[str]) -> list[str]:
        """Resolve group names to a snapshot of current member user IDs.

        Called once when an approval interaction is created (see
        `prepare_interaction` / `resolve_approver_group_members_activity`).
        The result is frozen into the interaction's `request_payload` —
        subsequent authorization checks in `record_vote` use that snapshot,
        not live group membership. See plan decision on req 6
        (approver group snapshot semantics): deliberately NOT dynamic, so
        that adding someone to a group after the fact can't retroactively
        let them approve an already-created request, and removing someone
        can't retroactively lock out an approval they were legitimately
        eligible for when it was requested.
        """
        if not group_names:
            return []
        result = await self.session.execute(
            select(GroupMember.user_id)
            .join(Group, Group.id == GroupMember.group_id)
            .where(
                Group.organization_id == self.organization_id,
                Group.name.in_(group_names),
            )
            .distinct()
        )
        return [str(uid) for uid in result.scalars().all()]

    async def record_vote(
        self,
        interaction: Interaction,
        *,
        user_id: uuid.UUID,
        decision: str,
        comment: str | None = None,
    ) -> ApprovalVoteResult:
        """Record one approver's decision and resolve the interaction if warranted.

        A single `reject` vote vetoes the interaction immediately. Otherwise it
        resolves once distinct `approve` votes reach `required_approvers`
        (read back from the request_payload set by `prepare_interaction`).

        Concurrency-safe: the interaction row is locked (`SELECT ... FOR
        UPDATE`) for the duration of the read-count-decide-write sequence, so
        two approvers voting at the same moment can't both decide to resolve
        it. The PENDING -> {APPROVED,REJECTED,TIMED_OUT} transition happens
        at most once; a call that finds the interaction already resolved
        (by another vote, or by a timeout that raced this call) is a no-op
        that reports the existing outcome rather than raising or re-deciding.
        """
        from tracecat.exceptions import TracecatValidationError

        if decision not in ("approve", "reject"):
            raise TracecatValidationError(f"Invalid decision: {decision!r}")

        audit = AuditService(session=self.session, role=self.role)

        # Lock the interaction row for the rest of this call. Any concurrent
        # `record_vote` on the same interaction blocks here until this
        # transaction commits, which is what makes the PENDING -> resolved
        # transition below happen exactly once.
        locked = await self.session.execute(
            select(Interaction)
            .where(
                Interaction.workspace_id == self.workspace_id,
                Interaction.id == interaction.id,
            )
            .with_for_update()
        )
        interaction = locked.scalar_one()

        if interaction.type != InteractionType.APPROVAL:
            raise TracecatValidationError(
                "This interaction is not an approval interaction."
            )

        request_payload = interaction.request_payload or {}
        required_approvers: int = request_payload.get("required_approvers", 1)
        eligible_approver_ids: list[str] | None = request_payload.get(
            "eligible_approver_ids"
        )
        requester_id = request_payload.get("requester_id")

        if interaction.status != InteractionStatus.PENDING:
            # Already resolved (by another vote) or timed out. Report the
            # existing outcome instead of re-deciding — this is the
            # idempotency guarantee for double-clicks/retries/duplicate
            # requests arriving after resolution.
            await audit.create_event(
                resource_type="approval_interaction",
                action="approve" if decision == "approve" else "reject",
                resource_id=interaction.id,
                status=AuditEventStatus.FAILURE,
                data={
                    "wf_exec_id": interaction.wf_exec_id,
                    "action_ref": interaction.action_ref,
                    "reason": "duplicate_or_late_vote",
                    "interaction_status": str(interaction.status),
                },
            )
            votes_result = await self.session.execute(
                select(ApprovalVote).where(
                    ApprovalVote.interaction_id == interaction.id
                )
            )
            approve_count = sum(
                1 for v in votes_result.scalars().all() if v.decision == "approve"
            )
            existing_resolution = (interaction.response_payload or {}).get("decision")
            if interaction.status == InteractionStatus.TIMED_OUT:
                existing_resolution = existing_resolution or "timed_out"
            return ApprovalVoteResult(
                resolution=existing_resolution,
                approve_count=approve_count,
                required_approvers=required_approvers,
            )

        # Separation of duties: the identity that triggered the workflow
        # execution may not also approve/reject it, when the interaction
        # was configured with `separation_of_duties: true`.
        if request_payload.get("separation_of_duties") and requester_id is not None:
            if str(user_id) == str(requester_id):
                await audit.create_event(
                    resource_type="approval_interaction",
                    action="deny",
                    resource_id=interaction.id,
                    status=AuditEventStatus.FAILURE,
                    data={
                        "wf_exec_id": interaction.wf_exec_id,
                        "action_ref": interaction.action_ref,
                        "reason": "separation_of_duties",
                    },
                )
                raise TracecatValidationError(
                    "Separation of duties: you requested this action and "
                    "cannot also approve or reject it."
                )

        # Authorization: against the approver-group *snapshot* taken when
        # the interaction was created (see resolve_group_member_ids), not
        # live group membership. `eligible_approver_ids is None` means no
        # `approver_groups` were configured — any workspace member may vote.
        if eligible_approver_ids is not None and str(user_id) not in set(
            eligible_approver_ids
        ):
            await audit.create_event(
                resource_type="approval_interaction",
                action="deny",
                resource_id=interaction.id,
                status=AuditEventStatus.FAILURE,
                data={
                    "wf_exec_id": interaction.wf_exec_id,
                    "action_ref": interaction.action_ref,
                    "reason": "not_eligible_approver",
                },
            )
            raise TracecatValidationError(
                "You are not a member of any group allowed to approve this action."
            )

        await audit.create_event(
            resource_type="approval_interaction",
            action=decision,
            resource_id=interaction.id,
            status=AuditEventStatus.ATTEMPT,
            data={
                "wf_exec_id": interaction.wf_exec_id,
                "action_ref": interaction.action_ref,
            },
        )

        # Upsert this user's vote (unique constraint on
        # (workspace_id, interaction_id, user_id) makes a double-click from
        # the same user idempotent — it overwrites their own prior vote
        # rather than counting twice).
        existing = await self.session.execute(
            select(ApprovalVote).where(
                ApprovalVote.workspace_id == self.workspace_id,
                ApprovalVote.interaction_id == interaction.id,
                ApprovalVote.user_id == user_id,
            )
        )
        vote = existing.scalars().first()
        if vote is None:
            vote = ApprovalVote(
                workspace_id=self.workspace_id,
                interaction_id=interaction.id,
                user_id=user_id,
            )
            self.session.add(vote)
        vote.decision = decision
        vote.comment = comment
        await self.session.flush()

        votes_result = await self.session.execute(
            select(ApprovalVote).where(ApprovalVote.interaction_id == interaction.id)
        )
        votes = votes_result.scalars().all()
        approve_count = sum(1 for v in votes if v.decision == "approve")
        rejected = any(v.decision == "reject" for v in votes)

        resolution: str | None = None
        if rejected:
            resolution = "rejected"
        elif approve_count >= required_approvers:
            resolution = "approved"

        # Built unconditionally (cheap: `votes` is already fetched above)
        # rather than inside the `if resolution is not None:` block below,
        # even though it's only actually used when resolved: it's also
        # reused further down, after the commit, for the Temporal update
        # payload — a second, separate `if resolution is not None:` block,
        # since the commit must land before that call goes out. A local
        # assigned in only one of two same-condition blocks can't be
        # statically proven bound in the other; assigning it here instead
        # sidesteps that rather than relying on both branches agreeing.
        vote_records = [
            {"user_id": str(v.user_id), "decision": v.decision, "comment": v.comment}
            for v in votes
        ]

        if resolution is not None:
            # Transition status *inside* the still-held row lock, before
            # signaling the workflow. Any voter blocked on the lock above
            # will see status != PENDING once unblocked and take the
            # early-return path instead of resolving a second time.
            interaction.status = InteractionStatus.COMPLETED
            interaction.response_payload = {
                "decision": resolution,
                "votes": vote_records,
            }
            self.session.add(interaction)
            assert resolution in ("approved", "rejected")
            await _mirror_approval_event_to_cases(
                session=self.session,
                role=self.role,
                wf_exec_id=interaction.wf_exec_id,
                event=ApprovalResolvedEvent(
                    wf_exec_id=interaction.wf_exec_id,
                    action_ref=interaction.action_ref,
                    resolution=resolution,
                ),
            )
        await self.session.commit()

        if resolution is not None:
            # Deferred import: tracecat.dsl.workflow imports this module
            # (via tracecat.ee.interactions.service) lazily to avoid a
            # circular import; mirror that here.
            from tracecat.dsl.client import get_temporal_client
            from tracecat.dsl.workflow import DSLWorkflow
            from tracecat.interactions.schemas import InteractionInput

            client = await get_temporal_client()
            handle = client.get_workflow_handle_for(
                DSLWorkflow.run, interaction.wf_exec_id
            )
            # Deterministic update ID keyed on the interaction: if this
            # exact resolution were ever sent twice (e.g. a retried
            # activity/request after a crash between commit and send),
            # Temporal itself dedupes the second call rather than running
            # the workflow's update handler again. Defense-in-depth on top
            # of the row lock above, which is what actually prevents two
            # *different* resolutions from both being computed.
            await handle.execute_update(
                DSLWorkflow.interaction_handler,
                InteractionInput(
                    interaction_id=interaction.id,
                    execution_id=interaction.wf_exec_id,
                    action_ref=interaction.action_ref,
                    data={
                        "decision": resolution,
                        "action_snapshot": request_payload.get("action_snapshot"),
                        "votes": vote_records,
                    },
                ),
                id=f"approval-resolve-{interaction.id}",
            )
            await audit.create_event(
                resource_type="approval_interaction",
                action=decision,
                resource_id=interaction.id,
                status=AuditEventStatus.SUCCESS,
                data={
                    "wf_exec_id": interaction.wf_exec_id,
                    "action_ref": interaction.action_ref,
                    "decision": resolution,
                    "old_status": "PENDING",
                    "new_status": "COMPLETED",
                },
            )

        return ApprovalVoteResult(
            resolution=resolution,
            approve_count=approve_count,
            required_approvers=required_approvers,
        )

    @staticmethod
    @activity.defn
    async def create_interaction_activity(
        input: CreateInteractionActivityInputs,
    ) -> uuid.UUID:
        """Create a new interaction record in the database.

        Args:
            params: Parameters for creating the interaction
        """
        async with InteractionService.with_session(role=input.role) as service:
            interaction = await service.create_interaction(input.params)
            service.logger.warning(
                "Created interaction in activity", interaction_id=interaction.id
            )
            if interaction.type == InteractionType.APPROVAL:
                request_payload = interaction.request_payload or {}
                await _mirror_approval_event_to_cases(
                    session=service.session,
                    role=input.role,
                    wf_exec_id=interaction.wf_exec_id,
                    event=ApprovalRequestedEvent(
                        wf_exec_id=interaction.wf_exec_id,
                        action_ref=interaction.action_ref,
                        required_approvers=request_payload.get("required_approvers", 1),
                    ),
                )
                await service.session.commit()
            return interaction.id

    @staticmethod
    @activity.defn
    async def update_interaction_activity(
        input: UpdateInteractionActivityInputs,
    ) -> uuid.UUID:
        """Update an existing interaction.

        Args:
            input: Parameters for updating the interaction

        Returns:
            The updated interaction
        """
        async with InteractionService.with_session(role=input.role) as service:
            interaction = await service.get_interaction(input.interaction_id)
            if interaction is None:
                raise ApplicationError("Interaction not found", non_retryable=True)
            old_status = interaction.status
            await service.update_interaction(interaction, input.params)
            if (
                interaction.type == InteractionType.APPROVAL
                and input.params.status == InteractionStatus.TIMED_OUT
            ):
                # Audit the timeout transition. `workflow.wait_condition`'s
                # timeout is a one-shot Temporal primitive (raises exactly
                # once per call), so this activity — and this audit event —
                # cannot fire twice for the same timeout.
                await AuditService(
                    session=service.session, role=input.role
                ).create_event(
                    resource_type="approval_interaction",
                    action="timeout",
                    resource_id=interaction.id,
                    status=AuditEventStatus.SUCCESS,
                    data={
                        "wf_exec_id": interaction.wf_exec_id,
                        "action_ref": interaction.action_ref,
                        "old_status": str(old_status),
                        "new_status": str(InteractionStatus.TIMED_OUT),
                    },
                )
                await _mirror_approval_event_to_cases(
                    session=service.session,
                    role=input.role,
                    wf_exec_id=interaction.wf_exec_id,
                    event=ApprovalTimedOutEvent(
                        wf_exec_id=interaction.wf_exec_id,
                        action_ref=interaction.action_ref,
                    ),
                )
                await service.session.commit()
            return interaction.id

    @staticmethod
    @activity.defn
    async def resolve_approver_group_members_activity(
        input: ResolveApproverGroupMembersActivityInputs,
    ) -> list[str]:
        """Snapshot approver group membership at approval-creation time.

        Must run as an activity: workflow code cannot query the database
        directly. See `InteractionService.resolve_group_member_ids` for the
        semantics this snapshot enforces.
        """
        async with InteractionService.with_session(role=input.role) as service:
            return await service.resolve_group_member_ids(input.group_names)
