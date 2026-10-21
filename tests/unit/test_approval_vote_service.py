"""Tests for the approval-gate security hardening in `InteractionService.record_vote`.

Covers: atomic resolution / idempotent resume (req 2, 3), timeout/late-vote
rejection (req 4), separation of duties (req 5), approver group snapshot
semantics (req 6). Does not require a real Temporal server: the workflow
resume call (`WorkflowHandle.execute_update`) is mocked, since what's under
test here is the database-side locking/authorization/idempotency logic, not
Temporal's own update-delivery mechanics (covered separately by the
Temporal e2e tests).
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import orjson
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.interactions.service import InteractionService

from tracecat.audit.enums import AuditEventStatus
from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import Group, GroupMember, Interaction, User
from tracecat.exceptions import TracecatValidationError
from tracecat.interactions.enums import InteractionStatus, InteractionType

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


async def _make_user(session: AsyncSession, *, email: str | None = None) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email or f"{uuid.uuid4()}@example.com",
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


async def _make_interaction(
    session: AsyncSession,
    svc_role: Role,
    *,
    request_payload: dict | None = None,
    status: InteractionStatus = InteractionStatus.PENDING,
    response_payload: dict | None = None,
) -> Interaction:
    interaction = Interaction(
        id=uuid.uuid4(),
        workspace_id=svc_role.workspace_id,
        wf_exec_id=f"wf_test/exec_{uuid.uuid4().hex[:8]}",
        action_ref="isolate_host",
        action_type="core.transform.reshape",
        type=InteractionType.APPROVAL,
        status=status,
        request_payload=request_payload or {"required_approvers": 1},
        response_payload=response_payload,
    )
    session.add(interaction)
    await session.commit()
    await session.refresh(interaction)
    return interaction


@pytest.fixture
def mock_execute_update():
    """Patch the Temporal resume call; return the AsyncMock for assertions."""
    with patch("tracecat.dsl.client.get_temporal_client") as mock_get_client:
        mock_handle = AsyncMock()
        mock_client = AsyncMock()
        # `Client.get_workflow_handle_for` is synchronous in the real SDK
        # (confirmed: `inspect.iscoroutinefunction` is False) and source
        # calls it without `await`. `mock_client` is an AsyncMock, which
        # makes every attribute an AsyncMock by default too — left alone,
        # calling this attribute without awaiting it returns a coroutine
        # instead of `mock_handle`. Override with a plain MagicMock so the
        # mock's calling convention matches the real (sync) method.
        mock_client.get_workflow_handle_for = MagicMock(return_value=mock_handle)
        mock_get_client.return_value = mock_client
        yield mock_handle.execute_update


class TestSingleApproverResolution:
    async def test_approve_resolves_immediately_when_one_approver_required(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        user = await _make_user(session)
        interaction = await _make_interaction(
            session, svc_role, request_payload={"required_approvers": 1}
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=user.id, decision="approve")

        assert result.resolution == "approved"
        assert result.approve_count == 1
        mock_execute_update.assert_called_once()
        await session.refresh(interaction)
        assert interaction.status == InteractionStatus.COMPLETED

    async def test_reject_vetoes_immediately_even_with_multiple_required(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        user = await _make_user(session)
        interaction = await _make_interaction(
            session, svc_role, request_payload={"required_approvers": 2}
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=user.id, decision="reject")

        assert result.resolution == "rejected"
        mock_execute_update.assert_called_once()


class TestMultiApproverCounting:
    async def test_stays_pending_until_threshold_then_resolves_exactly_once(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        alice = await _make_user(session)
        bob = await _make_user(session)
        interaction = await _make_interaction(
            session, svc_role, request_payload={"required_approvers": 2}
        )
        svc = InteractionService(session=session, role=svc_role)

        first = await svc.record_vote(interaction, user_id=alice.id, decision="approve")
        assert first.resolution is None
        assert first.approve_count == 1
        mock_execute_update.assert_not_called()

        second = await svc.record_vote(interaction, user_id=bob.id, decision="approve")
        assert second.resolution == "approved"
        assert second.approve_count == 2
        mock_execute_update.assert_called_once()

    async def test_same_user_double_vote_does_not_double_count(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        alice = await _make_user(session)
        interaction = await _make_interaction(
            session, svc_role, request_payload={"required_approvers": 2}
        )
        svc = InteractionService(session=session, role=svc_role)

        await svc.record_vote(interaction, user_id=alice.id, decision="approve")
        # Double-click / retry from the same user: overwrites their own vote,
        # does not add a second vote toward the threshold.
        result = await svc.record_vote(
            interaction, user_id=alice.id, decision="approve"
        )

        assert result.resolution is None
        assert result.approve_count == 1
        mock_execute_update.assert_not_called()


class TestIdempotentResolution:
    async def test_vote_after_resolution_is_a_noop_reporting_existing_outcome(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        bob = await _make_user(session)
        interaction = await _make_interaction(
            session,
            svc_role,
            status=InteractionStatus.COMPLETED,
            response_payload={"decision": "approved", "votes": []},
            request_payload={"required_approvers": 1},
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=bob.id, decision="approve")

        assert result.resolution == "approved"
        mock_execute_update.assert_not_called()

    async def test_vote_after_timeout_is_rejected_as_timed_out(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        alice = await _make_user(session)
        interaction = await _make_interaction(
            session, svc_role, status=InteractionStatus.TIMED_OUT
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(
            interaction, user_id=alice.id, decision="approve"
        )

        assert result.resolution == "timed_out"
        mock_execute_update.assert_not_called()

    async def test_concurrent_votes_from_two_sessions_resolve_exactly_once(
        self, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        """Two independent DB sessions (simulating two concurrent API requests)
        voting at the same time must not both decide the resolution — the
        row lock in `record_vote` must serialize them.

        Setup deliberately does NOT use the `session` fixture: that fixture
        wraps the whole test in one uncommitted SAVEPOINT-nested transaction
        on a single connection, so `session.commit()` inside it never becomes
        visible to a genuinely separate connection (correct Postgres MVCC
        behavior). To exercise the row lock across two *real* concurrent
        sessions, setup must go through the same committing
        `get_async_session_context_manager()` the two voters use below.
        """
        async with get_async_session_context_manager() as setup_session:
            alice = await _make_user(setup_session)
            bob = await _make_user(setup_session)
            interaction = await _make_interaction(
                setup_session, svc_role, request_payload={"required_approvers": 2}
            )

        async def vote_as(user_id: uuid.UUID) -> object:
            async with get_async_session_context_manager() as own_session:
                svc = InteractionService(session=own_session, role=svc_role)
                # Re-fetch within this session's transaction
                interaction_own = await svc.get_interaction(interaction.id)
                assert interaction_own is not None
                return await svc.record_vote(
                    interaction_own, user_id=user_id, decision="approve"
                )

        results = await asyncio.gather(vote_as(alice.id), vote_as(bob.id))

        resolutions = [r.resolution for r in results]  # type: ignore[attr-defined]
        assert resolutions.count("approved") == 1
        assert resolutions.count(None) == 1
        # Exactly one of the two calls actually resumed the workflow.
        assert mock_execute_update.call_count == 1


class TestSeparationOfDuties:
    async def test_requester_cannot_approve_own_request_when_enabled(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        requester = await _make_user(session)
        interaction = await _make_interaction(
            session,
            svc_role,
            request_payload={
                "required_approvers": 1,
                "separation_of_duties": True,
                "requester_id": str(requester.id),
            },
        )
        svc = InteractionService(session=session, role=svc_role)

        with pytest.raises(TracecatValidationError, match="[Ss]eparation of duties"):
            await svc.record_vote(interaction, user_id=requester.id, decision="approve")
        mock_execute_update.assert_not_called()

    async def test_different_user_can_approve_when_enabled(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        requester = await _make_user(session)
        approver = await _make_user(session)
        interaction = await _make_interaction(
            session,
            svc_role,
            request_payload={
                "required_approvers": 1,
                "separation_of_duties": True,
                "requester_id": str(requester.id),
            },
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(
            interaction, user_id=approver.id, decision="approve"
        )
        assert result.resolution == "approved"

    async def test_requester_can_approve_when_disabled(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        requester = await _make_user(session)
        interaction = await _make_interaction(
            session,
            svc_role,
            request_payload={
                "required_approvers": 1,
                "separation_of_duties": False,
                "requester_id": str(requester.id),
            },
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(
            interaction, user_id=requester.id, decision="approve"
        )
        assert result.resolution == "approved"


class TestApproverGroupSnapshot:
    async def test_only_snapshotted_members_can_vote(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        alice = await _make_user(session)
        outsider = await _make_user(session)
        interaction = await _make_interaction(
            session,
            svc_role,
            request_payload={
                "required_approvers": 1,
                "approver_groups": ["soc-tier-2"],
                "eligible_approver_ids": [str(alice.id)],
            },
        )
        svc = InteractionService(session=session, role=svc_role)

        with pytest.raises(TracecatValidationError, match="not a member"):
            await svc.record_vote(interaction, user_id=outsider.id, decision="approve")

        result = await svc.record_vote(
            interaction, user_id=alice.id, decision="approve"
        )
        assert result.resolution == "approved"

    async def test_removal_from_live_group_does_not_revoke_snapshot_eligibility(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        """Bob was in the group when the approval was created (snapshotted),
        then removed from the live group. He can still approve — the
        snapshot, not live membership, is authoritative (req 6 decision)."""
        bob = await _make_user(session)
        interaction = await _make_interaction(
            session,
            svc_role,
            request_payload={
                "required_approvers": 1,
                "approver_groups": ["soc-tier-2"],
                "eligible_approver_ids": [str(bob.id)],
            },
        )
        # Note: no Group/GroupMember rows are created for bob at all here —
        # proving authorization comes purely from the snapshot, not a live
        # join against `group`/`group_member`.
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=bob.id, decision="approve")
        assert result.resolution == "approved"

    async def test_resolve_group_member_ids_reads_live_membership_at_call_time(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        """Sanity check for the snapshot-taking side: the resolver activity
        body reads live membership *once*, at the moment it's called."""
        alice = await _make_user(session)
        group = Group(
            id=uuid.uuid4(), name="soc-tier-2", organization_id=svc_role.organization_id
        )
        session.add(group)
        await session.commit()
        session.add(GroupMember(user_id=alice.id, group_id=group.id))
        await session.commit()

        svc = InteractionService(session=session, role=svc_role)
        ids = await svc.resolve_group_member_ids(["soc-tier-2"])
        assert ids == [str(alice.id)]

        ids_unknown_group = await svc.resolve_group_member_ids(["does-not-exist"])
        assert ids_unknown_group == []


class TestInputValidation:
    async def test_rejects_invalid_decision_value(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        user = await _make_user(session)
        interaction = await _make_interaction(session, svc_role)
        svc = InteractionService(session=session, role=svc_role)

        with pytest.raises(TracecatValidationError):
            await svc.record_vote(interaction, user_id=user.id, decision="maybe")

    async def test_rejects_non_approval_interaction_type(
        self, session: AsyncSession, svc_role: Role, mock_execute_update: AsyncMock
    ) -> None:
        user = await _make_user(session)
        interaction = await _make_interaction(session, svc_role)
        interaction.type = InteractionType.RESPONSE
        session.add(interaction)
        await session.commit()
        svc = InteractionService(session=session, role=svc_role)

        with pytest.raises(TracecatValidationError):
            await svc.record_vote(interaction, user_id=user.id, decision="approve")


class TestAuditTrail:
    """Assert the security audit trail (plan req 7): `record_vote` fires an
    `AuditService.create_event` webhook post for every decision it makes,
    not just the happy path. `create_event`'s own generic behavior (payload
    shape, silent skip when unconfigured) is already covered by
    `tests/unit/test_audit_service.py`; this is specifically what
    `record_vote` sends for each of its own lifecycle transitions.
    """

    @pytest.fixture
    def webhook(self, monkeypatch: pytest.MonkeyPatch):
        """`record_vote` builds its own `AuditService(session=self.session,
        role=self.role)` internally — there's no instance to hand a
        pre-configured webhook URL to, so patch the class method instead.
        Mocks the HTTP POST via respx so `create_event` takes its real
        "webhook configured" path rather than the silent-skip early return
        it takes when `_get_webhook_url()` is None (see
        `AuditService.create_event`) — asserting call counts against a
        skipped path would pass vacuously without exercising anything.
        """
        webhook_url = "https://example.com/audit"
        monkeypatch.setattr(
            AuditService, "_get_webhook_url", AsyncMock(return_value=webhook_url)
        )
        with respx.mock:
            route = respx.post(webhook_url).mock(return_value=httpx.Response(200))
            yield route

    @staticmethod
    def _payloads(route: respx.Route) -> list[dict]:
        return [orjson.loads(call.request.content) for call in route.calls]

    async def test_approve_flow_sends_attempt_then_success_events(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_execute_update: AsyncMock,
        webhook: respx.Route,
    ) -> None:
        """A vote that resolves the interaction fires two events, not one:
        ATTEMPT before the vote is recorded, then SUCCESS once the workflow
        resume (`execute_update`) has actually gone through — so the trail
        distinguishes "someone tried to approve" from "and it went
        through," which matters if `execute_update` itself ever fails."""
        user = await _make_user(session)
        interaction = await _make_interaction(
            session, svc_role, request_payload={"required_approvers": 1}
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=user.id, decision="approve")

        assert result.resolution == "approved"
        payloads = self._payloads(webhook)
        assert len(payloads) == 2
        attempt, success = payloads
        assert attempt["action"] == "approve"
        assert attempt["status"] == AuditEventStatus.ATTEMPT.value
        assert attempt["resource_type"] == "approval_interaction"
        assert attempt["data"]["action_ref"] == interaction.action_ref
        assert success["action"] == "approve"
        assert success["status"] == AuditEventStatus.SUCCESS.value
        assert success["data"]["decision"] == "approved"
        assert success["data"]["new_status"] == "COMPLETED"

    async def test_reject_flow_sends_attempt_then_success_events(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_execute_update: AsyncMock,
        webhook: respx.Route,
    ) -> None:
        user = await _make_user(session)
        interaction = await _make_interaction(
            session, svc_role, request_payload={"required_approvers": 2}
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=user.id, decision="reject")

        assert result.resolution == "rejected"
        payloads = self._payloads(webhook)
        assert len(payloads) == 2
        attempt, success = payloads
        assert attempt["action"] == "reject"
        assert attempt["status"] == AuditEventStatus.ATTEMPT.value
        assert success["action"] == "reject"
        assert success["status"] == AuditEventStatus.SUCCESS.value
        assert success["data"]["decision"] == "rejected"

    async def test_separation_of_duties_denial_is_audited(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_execute_update: AsyncMock,
        webhook: respx.Route,
    ) -> None:
        requester = await _make_user(session)
        interaction = await _make_interaction(
            session,
            svc_role,
            request_payload={
                "required_approvers": 1,
                "separation_of_duties": True,
                "requester_id": str(requester.id),
            },
        )
        svc = InteractionService(session=session, role=svc_role)

        with pytest.raises(TracecatValidationError):
            await svc.record_vote(interaction, user_id=requester.id, decision="approve")

        payloads = self._payloads(webhook)
        assert len(payloads) == 1
        assert payloads[0]["action"] == "deny"
        assert payloads[0]["status"] == AuditEventStatus.FAILURE.value
        assert payloads[0]["data"]["reason"] == "separation_of_duties"
        # Denied before any vote is recorded — no downstream resume for a
        # request that was never actually accepted as a vote.
        mock_execute_update.assert_not_called()

    async def test_late_vote_after_resolution_is_audited_as_duplicate(
        self,
        session: AsyncSession,
        svc_role: Role,
        mock_execute_update: AsyncMock,
        webhook: respx.Route,
    ) -> None:
        bob = await _make_user(session)
        interaction = await _make_interaction(
            session,
            svc_role,
            status=InteractionStatus.COMPLETED,
            request_payload={"required_approvers": 1},
            response_payload={"decision": "approved", "votes": []},
        )
        svc = InteractionService(session=session, role=svc_role)

        result = await svc.record_vote(interaction, user_id=bob.id, decision="approve")

        assert result.resolution == "approved"
        payloads = self._payloads(webhook)
        assert len(payloads) == 1
        assert payloads[0]["action"] == "approve"
        assert payloads[0]["status"] == AuditEventStatus.FAILURE.value
        assert payloads[0]["data"]["reason"] == "duplicate_or_late_vote"
