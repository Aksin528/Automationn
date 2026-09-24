"use client"

import {
  AlertCircle,
  CheckIcon,
  ClockIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react"
import { useState } from "react"
import type { ApprovalListItem } from "@/client"
import { CaseEventTimestamp } from "@/components/cases/case-panel-common"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import type { TracecatApiError } from "@/lib/errors"
import {
  useCaseApprovals,
  useDismissCaseApproval,
  useSubmitCaseApproval,
} from "@/lib/hooks"

/**
 * Panel listing a case's native tool-call approvals (the same `Approval`
 * records gating an agent session's containment actions) -- pending,
 * approved, and rejected together, in the order the agent proposed each
 * action. The agent proposes actions one at a time (not as a batch), so
 * this list is what lets an analyst see the whole running plan in one
 * place instead of only ever seeing the single currently-pending action.
 * Resolved cards are read-only; only a Pending card has Approve/Reject.
 *
 * This reads and resolves the same data as the Agents "Review approvals"
 * dialog, the Inbox, and the "Sync approvals to Telegram" workflow -- a
 * decision made here resolves the paused agent session directly, and shows
 * as resolved on every other surface too. It is not a separate,
 * comment-only sign-off (see the retired `ManualApprovalActions`, which
 * this replaces).
 */
export function PendingApprovalsSection({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const { caseApprovals, caseApprovalsIsLoading, caseApprovalsError } =
    useCaseApprovals({ caseId, workspaceId })

  if (caseApprovalsIsLoading) {
    return (
      <div className="space-y-3">
        <ApprovalCardSkeleton />
        <ApprovalCardSkeleton />
      </div>
    )
  }

  if (caseApprovalsError) {
    return (
      <div className="flex items-center justify-center p-8">
        <div className="flex items-center gap-2 text-red-600">
          <AlertCircle className="h-4 w-4" />
          <span className="text-sm">Failed to load approvals</span>
        </div>
      </div>
    )
  }

  if (!caseApprovals?.length) {
    return (
      <div className="flex items-center justify-center p-8 text-sm text-muted-foreground">
        No approvals recorded for this case yet.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {caseApprovals.map((approval) => (
        <ApprovalCard
          key={approval.id}
          caseId={caseId}
          workspaceId={workspaceId}
          approval={approval}
        />
      ))}
    </div>
  )
}

/** Strip the `mcp.<integration-name>.` prefix runtime tool names carry, so
 * the card shows the tool as an analyst would recognize it (e.g.
 * `cortex_isolate_endpoint` rather than
 * `mcp.soc-mcp-actions.cortex_isolate_endpoint`). */
function displayToolName(toolName: string): string {
  const parts = toolName.split(".")
  return parts.length > 2 ? parts.slice(2).join(".") : toolName
}

function ApprovalCard({
  caseId,
  workspaceId,
  approval,
}: {
  caseId: string
  workspaceId: string
  approval: ApprovalListItem
}) {
  const { submitCaseApproval, submitCaseApprovalIsPending } =
    useSubmitCaseApproval({ caseId, workspaceId })
  const { dismissCaseApproval, dismissCaseApprovalIsPending } =
    useDismissCaseApproval({ caseId, workspaceId })
  // Expiry isn't known until we try -- the list never says "expired" up
  // front (see submit_approvals in the backend router). This flips to true
  // only after an actual Approve/Reject attempt comes back 410 Gone.
  const [locallyExpired, setLocallyExpired] = useState(false)

  const handleDecision = async (approved: boolean) => {
    try {
      await submitCaseApproval({
        sessionId: approval.session_id,
        toolCallId: approval.tool_call_id,
        approved,
      })
    } catch (error) {
      if ((error as TracecatApiError).status === 410) {
        setLocallyExpired(true)
        return
      }
      console.error("Error submitting case approval decision", error)
    }
  }

  const handleDismiss = async () => {
    try {
      await dismissCaseApproval({ sessionId: approval.session_id })
    } catch (error) {
      console.error("Error dismissing case approval", error)
    }
  }

  const args = approval.tool_call_args ?? {}
  const argEntries = Object.entries(args)
  const isPending = approval.status === "pending"
  const isApproved = approval.status === "approved"
  const isRejected = approval.status === "rejected"
  // Expiry only ever applies to a Pending card -- it's discovered
  // reactively, at the moment this specific card's own Approve/Reject
  // attempt comes back 410 Gone (see useSubmitCaseApproval).
  const isExpired = isPending && locallyExpired
  const isMuted = isExpired || isRejected

  return (
    <section className="space-y-3 rounded-lg border border-border/60 px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex min-w-0 items-center gap-2">
            <span
              className={`truncate text-sm font-medium ${isMuted ? "text-muted-foreground" : "text-foreground"}`}
            >
              {displayToolName(approval.tool_name)}
            </span>
            {isExpired ? (
              <Badge
                variant="outline"
                className="h-5 shrink-0 gap-1 rounded-full border-muted-foreground/40 px-1.5 text-xs leading-none text-muted-foreground"
              >
                <ClockIcon className="size-3" />
                Expired
              </Badge>
            ) : isApproved ? (
              <Badge
                variant="outline"
                className="h-5 shrink-0 gap-1 rounded-full border-emerald-500/50 px-1.5 text-xs leading-none text-emerald-600"
              >
                <CheckIcon className="size-3" />
                Approved
              </Badge>
            ) : isRejected ? (
              <Badge
                variant="outline"
                className="h-5 shrink-0 gap-1 rounded-full border-destructive/50 px-1.5 text-xs leading-none text-destructive"
              >
                <XIcon className="size-3" />
                Rejected
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="h-5 shrink-0 rounded-full px-1.5 text-xs leading-none"
              >
                Pending
              </Badge>
            )}
          </div>
          {argEntries.length > 0 ? (
            <p className="truncate text-sm text-muted-foreground">
              {argEntries.map(([k, v]) => `${k}=${String(v)}`).join(", ")}
            </p>
          ) : null}
          {isExpired ? (
            <p className="text-xs text-muted-foreground">
              This session is no longer running (likely a restart or deployment
              while it was waiting). Send a new chat message to the case to
              retry, then dismiss this one.
            </p>
          ) : null}
        </div>
        <CaseEventTimestamp createdAt={approval.created_at} showIcon={false} />
      </div>

      {isExpired ? (
        <div className="flex items-center justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-8 gap-1.5 text-muted-foreground"
            disabled={dismissCaseApprovalIsPending}
            onClick={handleDismiss}
          >
            <Trash2Icon className="size-3.5" />
            Dismiss
          </Button>
        </div>
      ) : isPending ? (
        <div className="flex items-center justify-end gap-2">
          <Button
            type="button"
            variant="destructive"
            size="sm"
            className="h-8 gap-1.5"
            disabled={submitCaseApprovalIsPending}
            onClick={() => handleDecision(false)}
          >
            <XIcon className="size-3.5" />
            Reject
          </Button>
          <Button
            type="button"
            size="sm"
            className="h-8 gap-1.5"
            disabled={submitCaseApprovalIsPending}
            onClick={() => handleDecision(true)}
          >
            <CheckIcon className="size-3.5" />
            Approve
          </Button>
        </div>
      ) : null}
    </section>
  )
}

function ApprovalCardSkeleton() {
  return (
    <div className="space-y-3 rounded-lg border border-border/60 px-5 py-4">
      <div className="flex items-center gap-2">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-4 w-16" />
      </div>
      <Skeleton className="h-3 w-2/3" />
      <div className="flex justify-end gap-2">
        <Skeleton className="h-8 w-20" />
        <Skeleton className="h-8 w-20" />
      </div>
    </div>
  )
}
