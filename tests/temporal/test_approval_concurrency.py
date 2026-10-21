"""Temporal e2e test for approval-gate concurrency safety (plan req 2 & 3).

`tests/unit/test_approval_vote_service.py::TestIdempotentResolution::
test_concurrent_votes_from_two_sessions_resolve_exactly_once` already proves
the row-lock serializes two concurrent `record_vote` calls at the database
level (with the Temporal update call mocked). This file proves the same
property end-to-end through a real running `DSLWorkflow` and a real
`execute_update`: two concurrent approvers voting on a `required_approvers:
2` interaction must resume the workflow — and therefore execute the gated
action — exactly once, never twice, even though both votes land at
essentially the same instant.
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
from tracecat.db.models import ApprovalVote, Interaction, User
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
    """See the identical fixture in test_approval_snapshot.py: `record_vote`
    goes through the real `AuditService`, which constructs a
    `SettingsService` that unconditionally requires
    `TRACECAT__DB_ENCRYPTION_KEY` to be set."""
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
    resolves its Temporal client via the production connector, which
    doesn't know about this test's ephemeral time-skipping server."""

    async def _fake_get_temporal_client(plugins: object = None) -> object:
        del plugins
        return env.client

    monkeypatch.setattr(
        "tracecat.dsl.client.get_temporal_client", _fake_get_temporal_client
    )


async def _ensure_users(*user_ids: uuid.UUID) -> None:
    async with get_async_session_context_manager() as session:
        for user_id in user_ids:
            if await session.get(User, user_id) is not None:
                continue
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


async def _wait_for_pending_interaction(
    wf_exec_id: str, *, timeout: float = 15.0
) -> Interaction:
    async def _poll() -> Interaction:
        while True:
            async with get_async_session_context_manager() as session:
                result = await session.execute(
                    select(Interaction).where(
                        Interaction.wf_exec_id == wf_exec_id,
                        Interaction.status == InteractionStatus.PENDING,
                    )
                )
                interaction = result.scalars().first()
                if interaction is not None:
                    return interaction
            await asyncio.sleep(0.25)

    return await asyncio.wait_for(_poll(), timeout=timeout)


def _build_two_approver_dsl(*, title: str, ref: str) -> DSLInput:
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
                    required_approvers=2,
                ),
            )
        ],
    )


@pytest.mark.anyio
async def test_two_concurrent_approvers_resume_workflow_exactly_once(
    env: WorkflowEnvironment,
    test_role: Role,
    test_worker_factory: Callable[..., Worker],
    temporal_client_points_at_test_env: None,
) -> None:
    """Two different users vote `approve` on a `required_approvers: 2`
    interaction at essentially the same instant. Exactly one of the two
    `record_vote` calls may observe the threshold being met and resolve the
    interaction — proven three independent ways: only one result reports a
    resolution, the workflow's gated action runs exactly once (not zero,
    not twice), and exactly two vote rows land in the database."""
    call_count = 0

    @activity.defn(name=ExecutorActivities.execute_action_activity.__name__)
    async def execute_action_activity_mock(
        input: RunActionInput, role: Role
    ) -> StoredObject:
        nonlocal call_count
        call_count += 1
        return InlineObject(data={"ok": True})

    dsl = _build_two_approver_dsl(title="approval-concurrency", ref="isolate_host")
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
        exec_id = generate_test_exec_id(f"approval_concurrency_{uuid.uuid4().hex[:6]}")
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

        interaction = await _wait_for_pending_interaction(exec_id)

        alice_id = uuid.uuid4()
        bob_id = uuid.uuid4()
        await _ensure_users(alice_id, bob_id)

        async def _vote_as(user_id: uuid.UUID) -> str | None:
            async with get_async_session_context_manager() as session:
                svc = InteractionService(session=session, role=test_role)
                fresh = await svc.get_interaction(interaction.id)
                assert fresh is not None
                result = await svc.record_vote(
                    fresh, user_id=user_id, decision="approve"
                )
                return result.resolution

        resolutions = await asyncio.gather(_vote_as(alice_id), _vote_as(bob_id))

        # Exactly one of the two concurrent calls saw the threshold met and
        # triggered resolution — the other saw the (still-pending, count=1)
        # state and correctly reported no resolution of its own. If the row
        # lock had failed to serialize them, both could independently see
        # count=2 and both would report "approved".
        assert resolutions.count("approved") == 1
        assert resolutions.count(None) == 1

        await handle.result()

    # The gated action ran exactly once — not zero times (the resolution
    # never reached the workflow) and not twice (a duplicate resume fired
    # it again).
    assert call_count == 1

    async with get_async_session_context_manager() as session:
        votes_result = await session.execute(
            select(ApprovalVote).where(ApprovalVote.interaction_id == interaction.id)
        )
        votes = votes_result.scalars().all()
    assert len(votes) == 2
    assert {v.user_id for v in votes} == {alice_id, bob_id}
    assert all(v.decision == "approve" for v in votes)
