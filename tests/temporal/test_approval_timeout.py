"""Temporal e2e test for approval-gate timeout semantics (plan req 4).

Covers `wait_for_response`'s existing timeout behavior end-to-end through a
real time-skipping Temporal test environment (not mocked timers): an
approval that nobody resolves before its `timeout` elapses must transition
to TIMED_OUT, the downstream gated action must never execute, and a vote
that arrives after the timeout must be a no-op through the real
`InteractionService.record_vote` — reporting the existing TIMED_OUT outcome
rather than resuming or re-deciding anything — so a late vote can never
trigger execution of an action whose approval window already closed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Callable

import pytest
from sqlalchemy import select
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from tracecat_ee.interactions.service import InteractionService

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Interaction
from tracecat.dsl._converter import get_data_converter
from tracecat.dsl.common import DSLEntrypoint, DSLInput, DSLRunArgs
from tracecat.dsl.schemas import ActionStatement, RunActionInput
from tracecat.dsl.worker import get_activities
from tracecat.dsl.workflow import DSLWorkflow
from tracecat.executor.activities import ExecutorActivities
from tracecat.interactions.enums import InteractionStatus, InteractionType
from tracecat.interactions.schemas import ApprovalInteraction
from tracecat.storage.object import InlineObject, StoredObject

pytestmark = [
    pytest.mark.temporal,
    pytest.mark.usefixtures(
        "registry_version_with_manifest", "db", "db_encryption_key"
    ),
]


@pytest.fixture
def db_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """`record_vote` goes through the real `AuditService`, which constructs a
    `SettingsService` (to look up an org-configured audit webhook URL) —
    and `SettingsService.__init__` unconditionally requires
    `TRACECAT__DB_ENCRYPTION_KEY` to be set, even though no webhook is
    configured here and no secret is actually being decrypted. Nothing in
    the temporal test fixtures sets this key by default, so it must be set
    explicitly here (see the identical fixture in test_approval_snapshot.py).
    """
    from cryptography.fernet import Fernet

    monkeypatch.setattr(
        config, "TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )


@pytest.fixture
async def env() -> AsyncGenerator[WorkflowEnvironment, None]:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=get_data_converter(compression_enabled=False)
    ) as workflow_env:
        yield workflow_env


@pytest.fixture
def temporal_client_points_at_test_env(
    env: WorkflowEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """See the identical fixture in test_approval_snapshot.py: `record_vote`
    calls `execute_update` via `tracecat.dsl.client.get_temporal_client()`,
    the production connector (defaults to real `localhost:7233`) — but the
    workflow under test here runs against the ephemeral time-skipping test
    server (`env.client`) on a random port, not that address."""

    async def _fake_get_temporal_client(plugins: object = None) -> object:
        del plugins
        return env.client

    monkeypatch.setattr(
        "tracecat.dsl.client.get_temporal_client", _fake_get_temporal_client
    )


def _build_approval_gated_dsl(*, title: str, ref: str, timeout: float) -> DSLInput:
    return DSLInput(
        title=title,
        description=title,
        entrypoint=DSLEntrypoint(ref=ref),
        actions=[
            ActionStatement(
                ref=ref,
                action="core.transform.reshape",
                args={"value": "isolate"},
                interaction=ApprovalInteraction(
                    type=InteractionType.APPROVAL,
                    required_approvers=1,
                    timeout=timeout,
                ),
            )
        ],
    )


async def _get_interaction(wf_exec_id: str) -> Interaction:
    async with get_async_session_context_manager() as session:
        result = await session.execute(
            select(Interaction).where(Interaction.wf_exec_id == wf_exec_id)
        )
        interaction = result.scalars().first()
        assert interaction is not None
        return interaction


@pytest.mark.anyio
async def test_timeout_prevents_execution_and_rejects_late_vote(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    temporal_client_points_at_test_env: None,
) -> None:
    """No one votes before `timeout` elapses: the gated action must never
    run, the interaction must end up TIMED_OUT, and a vote arriving after
    the fact must be rejected rather than resolving a dead interaction."""
    call_count = 0

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        nonlocal call_count
        call_count += 1
        return InlineObject(data={"ok": True})

    timeout_seconds = 5.0
    dsl = _build_approval_gated_dsl(
        title="approval-timeout", ref="isolate_host", timeout=timeout_seconds
    )
    activities = get_activities()
    activities.append(execute_action_activity_mock)

    async with (
        test_worker_factory(env.client, activities=activities),
        test_worker_factory(
            env.client,
            activities=[execute_action_activity_mock],
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
        ),
    ):
        exec_id = generate_test_exec_id(f"approval_timeout_{uuid.uuid4().hex[:6]}")
        handle: WorkflowHandle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl,
                role=test_role,
                wf_id=TEST_WF_ID,
                trigger_inputs=InlineObject(data={}),
            ),
            id=exec_id,
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )

        # Nobody votes. The time-skipping test server auto-advances through
        # the workflow's `wait_condition(timeout=...)` while we're blocked
        # here awaiting the result — no manual `env.sleep()` needed since
        # there's nothing else for the workflow to do in the meantime.
        with pytest.raises(WorkflowFailureError):
            await handle.result()

        interaction = await _get_interaction(exec_id)
        assert interaction.status == InteractionStatus.TIMED_OUT

        # The gated action must never have run — this is req 4's central
        # guarantee, not just "the workflow eventually fails."
        assert call_count == 0

        # A vote that arrives after the timeout — e.g. a slow approver who
        # didn't refresh their screen — must not resume or re-decide
        # anything. Per the idempotent-resolution design (plan req 2/3),
        # a vote on an already-resolved interaction (TIMED_OUT included)
        # is a no-op that reports the existing outcome rather than raising:
        # the important guarantee is that it can never trigger execution,
        # not that it's rejected with an error.
        async with get_async_session_context_manager() as session:
            svc = InteractionService(session=session, role=test_role)
            fresh = await svc.get_interaction(interaction.id)
            assert fresh is not None
            late_result = await svc.record_vote(
                fresh, user_id=test_role.user_id, decision="approve"
            )
        assert late_result.resolution == "timed_out"

    # Still never executed, even after the late vote attempt above.
    assert call_count == 0
