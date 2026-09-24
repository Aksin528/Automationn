"""Tests for the case pending-approvals endpoint's `current_approvals` count.

`Interaction.response_payload` (where per-voter results normally live) stays
`None` until an approval resolves, so a still-`PENDING` interaction has no
built-in vote tally. `list_pending_approvals` (`tracecat/cases/router.py`)
computes one live from `ApprovalVote` rows and surfaces it as
`InteractionRead.current_approvals` — this file covers that aggregation,
not the vote-recording logic itself (already covered by
`tests/unit/test_approval_vote_service.py`).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.router import list_pending_approvals
from tracecat.cases.schemas import CaseCreate
from tracecat.cases.service import CasesService
from tracecat.contexts import ctx_role
from tracecat.db.models import ApprovalVote, Case, CaseEvent, Interaction, User
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
            description="Case for pending-approvals aggregation test",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
        )
    )


async def _link_execution_to_case(
    session: AsyncSession, role: Role, case: Case, wf_exec_id: str
) -> None:
    """Mirror what a real workflow trigger/status-change writes: a
    `CaseEvent` whose `data` blob carries the linking `wf_exec_id` — the
    same field `list_pending_approvals` queries for linkage. The event
    `type` itself is irrelevant to that query (it filters on the `data`
    key's presence, not the type), so any valid type works here."""
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
    session: AsyncSession, role: Role, *, wf_exec_id: str, required_approvers: int
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


async def _cast_vote(
    session: AsyncSession,
    role: Role,
    interaction: Interaction,
    user_id: uuid.UUID,
    decision: str,
) -> None:
    session.add(
        ApprovalVote(
            workspace_id=role.workspace_id,
            interaction_id=interaction.id,
            user_id=user_id,
            decision=decision,
        )
    )
    await session.commit()


@pytest.fixture
def interactions_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """`list_pending_approvals` short-circuits to `[]` unless the
    `app_interactions_enabled` org setting is on. Rather than seeding a
    real settings row (which needs `TRACECAT__DB_ENCRYPTION_KEY` and a
    genuinely committed session — see the identical problem worked around
    in the Temporal e2e approval tests), patch the router's own
    `get_setting` call directly: what's under test here is the vote-count
    aggregation, not the settings gate."""
    monkeypatch.setattr(
        "tracecat.cases.router.get_setting", AsyncMock(return_value=True)
    )


@pytest.fixture(autouse=True)
def _set_ctx_role(svc_role: Role):
    """`@require_scope` (on `list_pending_approvals`) reads `ctx_role.get()`,
    not the function's own `role` parameter — calling the endpoint function
    directly (bypassing FastAPI's request handling) needs this set
    explicitly, the same way the `test_role`/`test_admin_role` fixtures do
    for tests that go through real HTTP-shaped call paths."""
    token = ctx_role.set(svc_role)
    yield
    ctx_role.reset(token)


class TestCurrentApprovalsCount:
    async def test_reflects_live_vote_count_while_pending(
        self,
        session: AsyncSession,
        svc_role: Role,
        interactions_enabled: None,
    ) -> None:
        case = await _make_case(session, svc_role)
        wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        await _link_execution_to_case(session, svc_role, case, wf_exec_id)
        interaction = await _make_approval_interaction(
            session, svc_role, wf_exec_id=wf_exec_id, required_approvers=3
        )
        alice = await _make_user(session)
        bob = await _make_user(session)
        await _cast_vote(session, svc_role, interaction, alice.id, "approve")
        await _cast_vote(session, svc_role, interaction, bob.id, "approve")

        results = await list_pending_approvals(
            role=svc_role, session=session, case_id=case.id
        )

        assert len(results) == 1
        assert results[0].id == interaction.id
        assert results[0].current_approvals == 2

    async def test_zero_when_no_votes_cast_yet(
        self,
        session: AsyncSession,
        svc_role: Role,
        interactions_enabled: None,
    ) -> None:
        case = await _make_case(session, svc_role)
        wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        await _link_execution_to_case(session, svc_role, case, wf_exec_id)
        interaction = await _make_approval_interaction(
            session, svc_role, wf_exec_id=wf_exec_id, required_approvers=1
        )

        results = await list_pending_approvals(
            role=svc_role, session=session, case_id=case.id
        )

        assert len(results) == 1
        assert results[0].id == interaction.id
        assert results[0].current_approvals == 0

    async def test_reject_votes_are_not_counted(
        self,
        session: AsyncSession,
        svc_role: Role,
        interactions_enabled: None,
    ) -> None:
        case = await _make_case(session, svc_role)
        wf_exec_id = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        await _link_execution_to_case(session, svc_role, case, wf_exec_id)
        interaction = await _make_approval_interaction(
            session, svc_role, wf_exec_id=wf_exec_id, required_approvers=2
        )
        alice = await _make_user(session)
        bob = await _make_user(session)
        await _cast_vote(session, svc_role, interaction, alice.id, "approve")
        await _cast_vote(session, svc_role, interaction, bob.id, "reject")

        results = await list_pending_approvals(
            role=svc_role, session=session, case_id=case.id
        )

        # Note: in the real flow a reject would have already resolved (and
        # thus un-PENDING'd) this interaction via `record_vote`'s veto
        # semantics — these two votes existing on a still-PENDING row here
        # is a test-only shortcut to isolate the aggregation query. What's
        # under test is specifically that the count only reflects "approve"
        # decisions, not that this exact state is reachable in production.
        assert len(results) == 1
        assert results[0].current_approvals == 1

    async def test_votes_on_other_interactions_are_not_counted(
        self,
        session: AsyncSession,
        svc_role: Role,
        interactions_enabled: None,
    ) -> None:
        case = await _make_case(session, svc_role)
        wf_exec_id_a = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        wf_exec_id_b = f"wf_test/exec_{uuid.uuid4().hex[:8]}"
        await _link_execution_to_case(session, svc_role, case, wf_exec_id_a)
        await _link_execution_to_case(session, svc_role, case, wf_exec_id_b)
        interaction_a = await _make_approval_interaction(
            session, svc_role, wf_exec_id=wf_exec_id_a, required_approvers=2
        )
        interaction_b = await _make_approval_interaction(
            session, svc_role, wf_exec_id=wf_exec_id_b, required_approvers=2
        )
        alice = await _make_user(session)
        await _cast_vote(session, svc_role, interaction_a, alice.id, "approve")

        results = await list_pending_approvals(
            role=svc_role, session=session, case_id=case.id
        )

        by_id = {r.id: r for r in results}
        assert len(results) == 2
        assert by_id[interaction_a.id].current_approvals == 1
        assert by_id[interaction_b.id].current_approvals == 0
