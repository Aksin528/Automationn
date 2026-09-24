"""Tests for approval-gate lifecycle events mirrored onto a case's Activity
timeline (`_mirror_approval_event_to_cases`,
`packages/tracecat-ee/tracecat_ee/interactions/service.py`).

Covers the three call sites that write a `CaseEvent`: interaction creation
(`create_interaction_activity` -> `approval_requested`), vote resolution
(`record_vote` -> `approval_resolved`), and timeout
(`update_interaction_activity` -> `approval_timed_out`) — plus that an
execution with no linked case is a silent no-op, not an error.

`create_interaction_activity` and `update_interaction_activity` each open
their own session (`InteractionService.with_session`, a real committing
connection — matches how they actually run as Temporal activities), not
the SAVEPOINT-wrapped `session` fixture the rest of this repo's unit tests
use. Setup and verification for those two therefore goes through
`get_async_session_context_manager()` too, for the same reason the
Temporal e2e approval tests do (see `tests/temporal/test_approval_snapshot.
py`): a SAVEPOINT-isolated session's data isn't visible to a genuinely
separate connection. `record_vote`, by contrast, is called directly with
whatever session the caller passes in — no connection boundary is crossed,
so the ordinary `session` fixture works there.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.interactions.service import (
    CreateInteractionActivityInputs,
    InteractionCreate,
    InteractionService,
    InteractionUpdate,
    UpdateInteractionActivityInputs,
)

from tracecat.auth.types import Role
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CaseEventsService, CasesService
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Case, CaseEvent, Interaction, User
from tracecat.interactions.enums import InteractionStatus, InteractionType

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


async def _make_user(session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@example.com",
        hashed_password="x",
        is_active=True,
        is_verified=True,
        is_superuser=False,
        last_login_at=None,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _make_case(session: AsyncSession, role: Role) -> Case:
    service = CasesService(session=session, role=role)
    return await service.create_case(
        CaseCreate(
            summary="Test case",
            description="Case for approval-event mirroring test",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )


async def _link_execution_to_case(
    session: AsyncSession, role: Role, case: Case, wf_exec_id: str
) -> None:
    session.add(
        CaseEvent(
            workspace_id=role.workspace_id,
            case_id=case.id,
            type=CaseEventType.CASE_UPDATED,
            data={"wf_exec_id": wf_exec_id},
        )
    )
    await session.commit()


async def _make_approval_interaction(
    session: AsyncSession,
    role: Role,
    *,
    wf_exec_id: str,
    required_approvers: int = 1,
) -> Interaction:
    interaction = Interaction(
        id=uuid.uuid4(),
        workspace_id=role.workspace_id,
        wf_exec_id=wf_exec_id,
        action_ref="isolate_host",
        action_type="core.transform.reshape",
        type=InteractionType.APPROVAL,
        status=InteractionStatus.PENDING,
        request_payload={"required_approvers": required_approvers},
        response_payload=None,
    )
    session.add(interaction)
    await session.commit()
    await session.refresh(interaction)
    return interaction


@pytest.fixture(autouse=True)
def _set_ctx_role(svc_role: Role):
    token = ctx_role.set(svc_role)
    yield
    ctx_role.reset(token)


@pytest.fixture
def mock_execute_update():
    """Same mock as `test_approval_vote_service.py`: `record_vote` calls
    `execute_update` on the resolving path — irrelevant to what's under
    test here (the case-event mirror), so it's mocked out rather than
    requiring a real Temporal server."""
    with patch("tracecat.dsl.client.get_temporal_client") as mock_get_client:
        mock_handle = AsyncMock()
        mock_client = AsyncMock()
        mock_client.get_workflow_handle_for = MagicMock(return_value=mock_handle)
        mock_get_client.return_value = mock_client
        yield mock_handle.execute_update


class TestApprovalRequestedEvent:
    async def test_written_to_linked_case_on_interaction_creation(
        self, svc_role: Role
    ) -> None:
        async with get_async_session_context_manager() as session:
            case = await _make_case(session, svc_role)
            wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
            await _link_execution_to_case(session, svc_role, case, wf_exec_id)

        interaction_id = await InteractionService.create_interaction_activity(
            CreateInteractionActivityInputs(
                role=svc_role,
                params=InteractionCreate(
                    wf_exec_id=wf_exec_id,
                    action_ref="isolate_host",
                    action_type="core.transform.reshape",
                    type=InteractionType.APPROVAL,
                    status=InteractionStatus.IDLE,
                    request_payload={"required_approvers": 2},
                ),
            )
        )

        assert interaction_id is not None
        async with get_async_session_context_manager() as session:
            events = await CaseEventsService(
                session=session, role=svc_role
            ).list_events(case)
        assert any(str(e.type) == "approval_requested" for e in events)

    async def test_no_op_when_execution_has_no_linked_case(
        self, svc_role: Role
    ) -> None:
        wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        # Deliberately not calling _link_execution_to_case: this execution
        # is not linked to any case, which must be silent, not an error.
        interaction_id = await InteractionService.create_interaction_activity(
            CreateInteractionActivityInputs(
                role=svc_role,
                params=InteractionCreate(
                    wf_exec_id=wf_exec_id,
                    action_ref="isolate_host",
                    action_type="core.transform.reshape",
                    type=InteractionType.APPROVAL,
                    status=InteractionStatus.IDLE,
                    request_payload={"required_approvers": 1},
                ),
            )
        )
        assert interaction_id is not None  # didn't raise


class TestApprovalResolvedEvent:
    async def test_written_on_approve(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_execute_update: AsyncMock,
    ) -> None:
        case = await _make_case(session, svc_role)
        wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        await _link_execution_to_case(session, svc_role, case, wf_exec_id)
        interaction = await _make_approval_interaction(
            session, svc_role, wf_exec_id=wf_exec_id, required_approvers=1
        )
        user = await _make_user(session)
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=user.id, decision="approve")

        assert result.resolution == "approved"
        events = await CaseEventsService(session=session, role=svc_role).list_events(
            case
        )
        resolved = [e for e in events if str(e.type) == "approval_resolved"]
        assert len(resolved) == 1
        assert resolved[0].data["resolution"] == "approved"

    async def test_written_on_reject(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_execute_update: AsyncMock,
    ) -> None:
        case = await _make_case(session, svc_role)
        wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        await _link_execution_to_case(session, svc_role, case, wf_exec_id)
        interaction = await _make_approval_interaction(
            session, svc_role, wf_exec_id=wf_exec_id, required_approvers=2
        )
        user = await _make_user(session)
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=user.id, decision="reject")

        assert result.resolution == "rejected"
        events = await CaseEventsService(session=session, role=svc_role).list_events(
            case
        )
        resolved = [e for e in events if str(e.type) == "approval_resolved"]
        assert len(resolved) == 1
        assert resolved[0].data["resolution"] == "rejected"

    async def test_not_written_while_still_pending(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_execute_update: AsyncMock,
    ) -> None:
        """A vote that doesn't yet meet `required_approvers` must not fire
        a resolved event — only the actual resolving vote does."""
        case = await _make_case(session, svc_role)
        wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        await _link_execution_to_case(session, svc_role, case, wf_exec_id)
        interaction = await _make_approval_interaction(
            session, svc_role, wf_exec_id=wf_exec_id, required_approvers=2
        )
        user = await _make_user(session)
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=user.id, decision="approve")

        assert result.resolution is None
        events = await CaseEventsService(session=session, role=svc_role).list_events(
            case
        )
        assert not any(str(e.type) == "approval_resolved" for e in events)


class TestApprovalTimedOutEvent:
    async def test_written_on_timeout(self, svc_role: Role) -> None:
        async with get_async_session_context_manager() as session:
            case = await _make_case(session, svc_role)
            wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
            await _link_execution_to_case(session, svc_role, case, wf_exec_id)
            interaction = await _make_approval_interaction(
                session, svc_role, wf_exec_id=wf_exec_id
            )

        await InteractionService.update_interaction_activity(
            UpdateInteractionActivityInputs(
                role=svc_role,
                interaction_id=interaction.id,
                params=InteractionUpdate(status=InteractionStatus.TIMED_OUT),
            )
        )

        async with get_async_session_context_manager() as session:
            events = await CaseEventsService(
                session=session, role=svc_role
            ).list_events(case)
        assert any(str(e.type) == "approval_timed_out" for e in events)
