from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

from tracecat.contexts import ctx_interaction, ctx_stream_id
from tracecat.dsl.action import DSLActivities, EvaluateTemplatedObjectActivityInput
from tracecat.dsl.schemas import ActionStatement, TaskResult
from tracecat.interactions.schemas import (
    ApprovalInteraction,
    InteractionContext,
    ResponseInteraction,
)

if TYPE_CHECKING:
    from tracecat.dsl.workflow import DSLWorkflow


def maybe_interactive(
    func: Callable[..., Awaitable[TaskResult]],
) -> Callable[[DSLWorkflow, ActionStatement], Awaitable[TaskResult]]:
    """Decorator that manages interactivity for a task execution.

    Args:
        task: The action statement being executed

    Returns:
        Decorator function that wraps the task execution
    """

    async def wrapper(wf: DSLWorkflow, task: ActionStatement) -> TaskResult:
        match task.interaction:
            case ResponseInteraction():
                # We only support response interactions for now
                # Open an interaction context
                interaction_id = await wf.interactions.prepare_interaction(
                    action_ref=task.ref,
                    action_type=task.action,
                    interaction_type=task.interaction.type,
                )
                context = InteractionContext(
                    interaction_id=interaction_id,
                    execution_id=wf.wf_exec_id,
                    action_ref=task.ref,
                )
                token = ctx_interaction.set(context)
                try:
                    action_result = await func(wf, task)
                finally:
                    ctx_interaction.reset(token)
                # Apply the wait condition
                interaction_result = await wf.interactions.wait_for_response(
                    interaction_id=interaction_id,
                )
                action_result.update(
                    interaction=interaction_result,
                    interaction_id=str(interaction_id),
                    interaction_type=str(task.interaction.type),
                )
            case ApprovalInteraction():
                # Unlike a response interaction, approval must be resolved
                # *before* the action itself runs: this is a gate, not a
                # follow-up. Note: `approve_if` is validated at commit time
                # but not yet auto-evaluated here — every approval currently
                # waits for a human decision. See plan for follow-up.
                stream_id = ctx_stream_id.get()

                # Snapshot the action's resolved args *now*, before waiting
                # on a human. Whatever the approver ends up seeing/approving
                # is what executes — not whatever `task.args` happens to
                # resolve to by the time they click, which could differ if
                # the workflow context has moved on in the meantime (e.g.
                # inside a for_each). Must run as an activity: expression
                # evaluation is non-deterministic and cannot run inline in
                # workflow code.
                action_context = wf._build_action_context(task, stream_id)
                resolved_args = await workflow.execute_activity(
                    DSLActivities.resolve_action_args_activity,
                    arg=EvaluateTemplatedObjectActivityInput(
                        obj=dict(task.args), operand=action_context, key=""
                    ),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

                # Snapshot eligible approvers at request time too (see plan
                # decision on req 6: group membership snapshot, not live).
                eligible_approver_ids: list[str] | None = None
                if task.interaction.approver_groups:
                    from tracecat_ee.interactions.service import (
                        InteractionService,
                        ResolveApproverGroupMembersActivityInputs,
                    )

                    eligible_approver_ids = await workflow.execute_activity(
                        InteractionService.resolve_approver_group_members_activity,
                        arg=ResolveApproverGroupMembersActivityInputs(
                            role=wf.role,
                            group_names=task.interaction.approver_groups,
                        ),
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )

                interaction_id = await wf.interactions.prepare_interaction(
                    action_ref=task.ref,
                    action_type=task.action,
                    interaction_type=task.interaction.type,
                    request_payload={
                        "required_approvers": task.interaction.required_approvers,
                        "approver_groups": task.interaction.approver_groups,
                        "eligible_approver_ids": eligible_approver_ids,
                        "message": task.interaction.message,
                        "separation_of_duties": task.interaction.separation_of_duties,
                        "requester_id": str(wf.role.user_id)
                        if wf.role.user_id
                        else None,
                        "action_snapshot": {
                            "action_ref": task.ref,
                            "action_type": task.action,
                            "args": resolved_args,
                        },
                    },
                )
                interaction_result = await wf.interactions.wait_for_response(
                    interaction_id=interaction_id,
                    timeout=task.interaction.timeout,
                )
                decision = interaction_result.get("decision")
                if decision == "rejected":
                    raise ApplicationError(
                        f"Approval rejected for action {task.ref!r}",
                        non_retryable=True,
                    )
                # Execute using the frozen snapshot, not `task.args` as it
                # stands now — this is what makes the approval binding to
                # the exact parameters the approver saw, regardless of any
                # workflow-context drift between request and resolution.
                snapshot = interaction_result.get("action_snapshot") or {}
                frozen_task = task.model_copy(
                    update={"args": snapshot.get("args", resolved_args)}
                )
                action_result = await func(wf, frozen_task)
                action_result.update(
                    interaction=interaction_result,
                    interaction_id=str(interaction_id),
                    interaction_type=str(task.interaction.type),
                )
            case _:
                action_result = await func(wf, task)
        return action_result

    return wrapper
