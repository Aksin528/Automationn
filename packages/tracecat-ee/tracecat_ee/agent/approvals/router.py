"""EE Approvals API router for submitting approval decisions."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.service import RPCError, RPCStatusCode

from tracecat.agent.approvals.enums import ApprovalStatus
from tracecat.agent.session.service import AgentSessionService
from tracecat.agent.types import ToolApproved, ToolDenied
from tracecat.auth.dependencies import WorkspaceActorRouteRole, WorkspaceUserRouteRole
from tracecat.authz.controls import require_scope
from tracecat.chat.schemas import ApprovalDecision, ContinueRunRequest
from tracecat.db.engine import get_async_session
from tracecat.exceptions import TracecatNotFoundError
from tracecat.logger import logger
from tracecat_ee.agent.approvals.service import ApprovalMap, ApprovalService

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalSubmission(BaseModel):
    """Request model for submitting approval decisions."""

    approvals: ApprovalMap


class ApprovalListItem(BaseModel):
    """Lightweight approval record for polling/listing purposes.

    Deliberately does not resolve `approved_by` into a full user object (see
    `ApprovalRead` for that) -- this endpoint exists for external pollers
    (e.g. a scheduled workflow that forwards newly-pending tool-call
    approvals to Telegram) that only need the tool call identity, not
    reviewer identity.
    """

    id: uuid.UUID
    session_id: uuid.UUID
    case_id: uuid.UUID | None = None
    tool_call_id: str
    tool_name: str
    status: ApprovalStatus
    tool_call_args: dict[str, Any] | None = None
    created_at: datetime
    is_expired: bool = False
    """True when status is PENDING but the underlying agent session's
    Temporal execution is confirmed gone (e.g. it outlived its own
    execution timeout unresolved). Submitting a decision for one of these
    will fail -- the UI should offer to dismiss/retry instead of approve/
    reject. Always False for non-PENDING approvals."""


@router.get("", response_model=list[ApprovalListItem])
@require_scope("agent:read")
async def list_approvals(
    *,
    role: WorkspaceActorRouteRole,
    status_filter: ApprovalStatus | None = Query(default=None, alias="status"),
    case_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_async_session),
) -> list[ApprovalListItem]:
    """List approvals in the workspace, optionally filtered by status and/or case.

    Used by external notification pollers to discover approvals without
    already knowing a specific session_id -- e.g. a scheduled workflow that
    checks for newly-created pending tool-call approvals and forwards them
    to Telegram. Also used by the case detail page's Approvals tab
    (case_id filter) to show pending approvals for that case directly.
    """
    # No proactive Temporal liveness check here on purpose: with unlimited
    # workflow timeouts enabled for this workspace, checking every pending
    # approval's session on every list/poll call would be pure overhead in
    # the common case. `is_expired` is always False from this endpoint --
    # expiry is detected reactively, at the moment an approve/reject
    # actually fails (see submit_approvals below), not shown speculatively.
    approval_service = ApprovalService(session=session, role=role)
    approvals = await approval_service.list_approvals(
        status=status_filter, case_id=case_id
    )
    return [
        ApprovalListItem(
            id=a.id,
            session_id=a.session_id,
            case_id=a.case_id,
            tool_call_id=a.tool_call_id,
            tool_name=a.tool_name,
            status=ApprovalStatus(a.status),
            tool_call_args=a.tool_call_args,
            created_at=a.created_at,
        )
        for a in approvals
        # session_id is nullable on the model but every approval created via
        # create_approval/create_approvals always sets it; a null here means
        # a decision could never be submitted for it anyway, so it can't be
        # actioned by this endpoint's consumers -- skip rather than 500.
        if a.session_id is not None
    ]


def _to_approval_decisions(approvals: ApprovalMap) -> list[ApprovalDecision]:
    decisions: list[ApprovalDecision] = []
    for tool_call_id, value in approvals.items():
        if isinstance(value, bool):
            decisions.append(
                ApprovalDecision(
                    tool_call_id=tool_call_id,
                    action="approve" if value else "deny",
                    reason=None if value else "Tool denied by user",
                )
            )
            continue
        if isinstance(value, ToolApproved):
            if value.override_args:
                decisions.append(
                    ApprovalDecision(
                        tool_call_id=tool_call_id,
                        action="override",
                        override_args=value.override_args,
                    )
                )
            else:
                decisions.append(
                    ApprovalDecision(
                        tool_call_id=tool_call_id,
                        action="approve",
                    )
                )
            continue
        if isinstance(value, ToolDenied):
            decisions.append(
                ApprovalDecision(
                    tool_call_id=tool_call_id,
                    action="deny",
                    reason=value.message or "Tool denied by user",
                )
            )
            continue
        raise ValueError(
            "Invalid approval payload for tool call "
            f"'{tool_call_id}': expected bool, ToolApproved, or ToolDenied."
        )
    return decisions


@router.post("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:update")
async def submit_approvals(
    *,
    role: WorkspaceActorRouteRole,
    session_id: uuid.UUID,
    payload: ApprovalSubmission,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Submit approval decisions to a running agent workflow.

    This endpoint sends approval decisions back to an agent workflow
    that is waiting for human-in-the-loop approval on tool calls.

    Accepts both interactive user sessions (the case-panel UI) and
    scoped service-account API keys (e.g. an automation forwarding a
    Telegram button decision) -- the `agent:update` scope requirement
    above is the actual gate; this only controls which credential
    types are allowed to present it.

    Args:
        role: The authenticated user or service-account role.
        session_id: The agent session ID (used to lookup the workflow).
        payload: The approval decisions mapping tool_call_id to decision.
        session: Database session for workspace-scoped lookups.

    Raises:
        HTTPException 400: If the approval submission fails validation.
        HTTPException 404: If the agent session/workflow is not found.
        HTTPException 500: For unexpected errors.
    """
    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access required",
        )

    # Verify the session belongs to the caller's workspace
    # This prevents cross-workspace access if an attacker knows another workspace's session_id
    session_service = AgentSessionService(session, role)
    agent_session = await session_service.get_session(session_id)
    if agent_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent session not found",
        )

    try:
        decisions = _to_approval_decisions(payload.approvals)
        continuation = ContinueRunRequest(
            decisions=decisions,
            source="inbox",
        )
        await session_service.run_turn(session_id, continuation)
    except TracecatNotFoundError as exc:
        logger.warning(
            "Agent session not found while submitting approvals",
            session_id=session_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        logger.warning(
            "Failed to submit approvals",
            session_id=session_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            # The agent session's Temporal execution is confirmed gone
            # (outlived unresolved -- typically from a deployment/restart
            # while it was paused, not a routine timeout). 410 Gone lets
            # the frontend distinguish this from a generic failure and
            # switch that approval to an "expired" + Dismiss state instead
            # of just showing an error.
            logger.warning(
                "Agent session's Temporal execution no longer exists",
                session_id=session_id,
            )
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="This approval's session has expired and can no longer be resolved.",
            ) from exc
        logger.exception(
            "Unexpected Temporal RPC error while submitting approvals",
            session_id=session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit approvals",
        ) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected error while submitting approvals",
            session_id=session_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit approvals",
        ) from exc


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("agent:delete")
async def delete_approval(
    *,
    role: WorkspaceUserRouteRole,
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Dismiss all pending approvals for a session.

    If the Temporal workflow is alive, deny the approvals so it fails at the
    agent step. If the workflow is already gone, delete the approval records
    directly. The session itself is left intact in both cases.
    """
    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Workspace access required",
        )

    session_service = AgentSessionService(session, role)
    approval_service = ApprovalService(session=session, role=role)

    agent_session = await session_service.get_session(session_id)
    if agent_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent session not found",
        )

    pending = [
        a
        for a in await approval_service.list_approvals_for_session(session_id)
        if a.status == ApprovalStatus.PENDING
    ]

    if not pending:
        return

    decisions = [
        ApprovalDecision(
            tool_call_id=a.tool_call_id,
            action="deny",
            reason="Dismissed from approvals inbox",
        )
        for a in pending
    ]
    try:
        await session_service.run_turn(
            session_id,
            ContinueRunRequest(decisions=decisions, source="inbox"),
        )
        return
    except Exception:
        logger.warning(
            "run_turn failed; deleting approval records directly",
            session_id=str(session_id),
            exc_info=True,
        )

    for approval in pending:
        await approval_service.delete_approval(approval)
