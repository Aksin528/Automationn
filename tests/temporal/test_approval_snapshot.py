"""Temporal e2e test for approval-gate snapshot immutability (plan req 1).

Exercises the real `DSLWorkflow` + `InteractionService` + Temporal update path
end-to-end: an approval-gated action's args are resolved and frozen into
`Interaction.request_payload["action_snapshot"]` the moment the approval is
requested (`resolve_action_args_activity`, called from
`tracecat_ee.interactions.decorators.maybe_interactive`). The eventual
execution — driven through the real `InteractionService.record_vote`, not a
hand-rolled Temporal update — must use exactly that frozen snapshot, never
whatever the workflow's live context looks like by the time a human clicks
Approve.

Proof strategy: run two workflows concurrently with different TRIGGER data
(TRIGGER is fixed at workflow start, so it cannot be mutated mid-flight to
prove the point within a single execution — see plan notes). Both reach
PENDING with their own snapshot; approve them in reverse-of-creation order.
If the executed args were derived from *anything* live/shared instead of the
per-interaction snapshot, this reverse-order approval would surface it as
cross-contamination between the two executions.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator, Callable

import pytest
from sqlalchemy import select
from temporalio import activity
from temporalio.client import WorkflowHandle
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from tracecat_ee.interactions.service import InteractionService

from tests.shared import TEST_WF_ID, generate_test_exec_id
from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Interaction, User
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
    configured here and no secret is actually being decrypted. Unlike the
    unit tests in `tests/unit/test_approval_vote_service.py` (which mock
    the Temporal update call but still hit this same real audit code path),
    nothing in the temporal test fixtures sets this key by default, so it
    must be set explicitly here.
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
    """`InteractionService.record_vote` calls `execute_update` on a client it
    gets from `tracecat.dsl.client.get_temporal_client()` — the production
    connector, which reads `config.TEMPORAL__CLUSTER_URL` and defaults to
    the real `localhost:7233`. Inside a `WorkflowEnvironment.start_time_skipping()`
    test, the workflow itself runs against an *ephemeral* test server on a
    random port (`env.client`), not that address — so a `record_vote` call
    made directly from test code, exactly as the authenticated vote endpoint
    would, needs to reach the same ephemeral server the workflow is on, not
    a real Temporal service that doesn't exist here. Point it at `env.client`
    directly rather than trying to discover the ephemeral server's port.
    """

    async def _fake_get_temporal_client(plugins: object = None) -> object:
        del plugins
        return env.client

    monkeypatch.setattr(
        "tracecat.dsl.client.get_temporal_client", _fake_get_temporal_client
    )


def _build_approval_gated_dsl(*, title: str, ref: str) -> DSLInput:
    """Single-action DSL: the action is approval-gated and its one arg is
    templated against TRIGGER, so the resolved value differs per execution
    depending on what `trigger_inputs` that execution was started with."""
    return DSLInput(
        title=title,
        description=title,
        entrypoint=DSLEntrypoint(ref=ref),
        actions=[
            ActionStatement(
                ref=ref,
                action="core.transform.reshape",
                args={"value": "${{ TRIGGER.host_id }}"},
                interaction=ApprovalInteraction(
                    type=InteractionType.APPROVAL,
                    required_approvers=1,
                ),
            )
        ],
    )


async def _ensure_approver_user(user_id: uuid.UUID) -> None:
    """`ApprovalVote.user_id` has a real FK to `user`. `test_role.user_id` is
    a synthetic worker-offset UUID (see `mock_org_id` in conftest.py) with no
    backing row, so a real `record_vote` call (the still-PENDING, actually-
    voting path — as opposed to the post-resolution no-op path) fails the FK
    constraint unless a `User` row exists first."""
    async with get_async_session_context_manager() as session:
        existing = await session.get(User, user_id)
        if existing is not None:
            return
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@example.com",
                hashed_password="x",
                is_active=True,
                is_verified=True,
                is_superuser=False,
                last_login_at=None,
            )
        )
        await session.commit()


async def _wait_for_pending_interactions(
    wf_exec_ids: list[str], *, timeout: float = 15.0
) -> dict[str, Interaction]:
    """Poll the DB for both executions' interactions to reach PENDING.

    Deliberately a single query covering every wf_exec_id, on one polling
    loop — not one `asyncio.gather`'d loop per execution reopening a session
    every 50ms. That pattern (tried first) reproducibly stalled fixture
    setup elsewhere in the same test: the DSL worker's own activities share
    this event loop and connection pool, and two tight concurrent polling
    loops were enough to starve them. A single, more slowly-polled query is
    just as valid a proof of "reached PENDING with the right snapshot" and
    doesn't compete for the pool this aggressively."""
    found: dict[str, Interaction] = {}

    async def _poll() -> dict[str, Interaction]:
        while len(found) < len(wf_exec_ids):
            async with get_async_session_context_manager() as session:
                result = await session.execute(
                    select(Interaction).where(
                        Interaction.wf_exec_id.in_(wf_exec_ids),
                        Interaction.status == InteractionStatus.PENDING,
                    )
                )
                for interaction in result.scalars().all():
                    found[interaction.wf_exec_id] = interaction
            if len(found) < len(wf_exec_ids):
                await asyncio.sleep(0.25)
        return found

    return await asyncio.wait_for(_poll(), timeout=timeout)


@pytest.mark.anyio
async def test_approved_execution_uses_frozen_snapshot_not_live_context(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    temporal_client_points_at_test_env: None,
) -> None:
    """Two concurrent approval-gated executions with different TRIGGER data:
    each one's eventual execution must use its own snapshot, taken at
    request time, regardless of approval order or the other execution's
    state."""
    # Keyed by wf_exec_id, not action ref: both DSLs use the same ref
    # ("isolate_host"), so keying by ref alone would let the second
    # execution's call silently overwrite the first's entry and mask
    # exactly the cross-contamination this test exists to catch.
    received_args: dict[str, dict] = {}
    call_count = 0

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        nonlocal call_count
        call_count += 1
        received_args[input.run_context.wf_exec_id] = dict(input.task.args)
        return InlineObject(data={"ok": True})

    dsl_a = _build_approval_gated_dsl(title="snapshot-a", ref="isolate_host")
    dsl_b = _build_approval_gated_dsl(title="snapshot-b", ref="isolate_host")
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
        exec_id_a = generate_test_exec_id(f"snapshot_a_{uuid.uuid4().hex[:6]}")
        exec_id_b = generate_test_exec_id(f"snapshot_b_{uuid.uuid4().hex[:6]}")

        handle_a: WorkflowHandle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl_a,
                role=test_role,
                wf_id=TEST_WF_ID,
                trigger_inputs=InlineObject(data={"host_id": "HOST-A"}),
            ),
            id=exec_id_a,
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )
        handle_b: WorkflowHandle = await env.client.start_workflow(
            DSLWorkflow.run,
            DSLRunArgs(
                dsl=dsl_b,
                role=test_role,
                wf_id=TEST_WF_ID,
                trigger_inputs=InlineObject(data={"host_id": "HOST-B"}),
            ),
            id=exec_id_b,
            task_queue=config.TEMPORAL__CLUSTER_QUEUE,
        )

        pending = await _wait_for_pending_interactions([exec_id_a, exec_id_b])
        interaction_a, interaction_b = pending[exec_id_a], pending[exec_id_b]

        # The snapshot is already frozen at PENDING time, before any human
        # decision — assert it directly against what each execution's own
        # TRIGGER should have resolved to.
        assert interaction_a.request_payload["action_snapshot"]["args"] == {
            "value": "HOST-A"
        }
        assert interaction_b.request_payload["action_snapshot"]["args"] == {
            "value": "HOST-B"
        }

        # Approve in *reverse* order of creation (B, then A) through the
        # real service — the same code path the authenticated vote endpoint
        # uses — so this is a genuine exercise of what actually ships,
        # not a hand-rolled Temporal update mimicking it.
        await _ensure_approver_user(test_role.user_id)
        async with get_async_session_context_manager() as session:
            svc = InteractionService(session=session, role=test_role)
            fresh_b = await svc.get_interaction(interaction_b.id)
            assert fresh_b is not None
            await svc.record_vote(
                fresh_b, user_id=test_role.user_id, decision="approve"
            )

        async with get_async_session_context_manager() as session:
            svc = InteractionService(session=session, role=test_role)
            fresh_a = await svc.get_interaction(interaction_a.id)
            assert fresh_a is not None
            await svc.record_vote(
                fresh_a, user_id=test_role.user_id, decision="approve"
            )

        await handle_b.result()
        await handle_a.result()

    assert call_count == 2
    # Each execution's action ran with exactly its own frozen snapshot —
    # no cross-contamination despite the reversed approval order and a
    # shared action ref between the two DSLs.
    assert received_args[exec_id_a] == {"value": "HOST-A"}
    assert received_args[exec_id_b] == {"value": "HOST-B"}
