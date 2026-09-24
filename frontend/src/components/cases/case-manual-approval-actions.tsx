"use client"

import { CheckIcon, XIcon } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import { useCreateCaseComment, useGetCase } from "@/lib/hooks"

function getDisplayName(
  user: ReturnType<typeof useAuth>["user"] | null | undefined
): string {
  if (!user) {
    return "Unknown analyst"
  }
  const name = [user.firstName, user.lastName].filter(Boolean).join(" ")
  return name || user.email
}

/** Shape written into `Case.payload["telegram_approval"]` by the IR/Full
 * Investigation workflow's `build_telegram_keyboard` step (via a follow-up
 * `core.cases.update_case` action) — the same content sent to Telegram,
 * mirrored here purely for display. Not a generated type: `payload` is a
 * free-form JSON blob on the backend model. */
interface TelegramApprovalPayload {
  text?: string
  actions?: string[]
}

function getTelegramApproval(
  payload: Record<string, unknown> | null | undefined
): TelegramApprovalPayload | null {
  const value = payload?.telegram_approval
  if (!value || typeof value !== "object") {
    return null
  }
  return value as TelegramApprovalPayload
}

/**
 * Minimal manual approve/reject control for a case.
 *
 * This is intentionally NOT wired to the `Interaction`/`ApprovalVote`
 * workflow-gate machinery (see `PendingApprovalsSection`) — it doesn't pause
 * or resume any Temporal workflow, and it doesn't require the
 * `app_interactions_enabled` org setting. It just records the analyst's
 * decision as a case comment, the same way the Incident Response playbook's
 * Telegram Approval Handler already logs button-click decisions as case
 * comments today. It exists as a lightweight, disk/deploy-cheap stand-in so
 * the same decision can also be made from the UI; the fuller Temporal-gated
 * version is deferred until a stronger deployment target is available.
 *
 * When the workflow also wrote `Case.payload.telegram_approval.actions`,
 * this renders one approve/reject row per recommended action — matching
 * the per-action buttons in the Telegram message — instead of a single
 * case-level sign-off. Each click just posts a comment naming the specific
 * action; nothing downstream reacts to it yet, same as the case-level
 * fallback.
 */
export function ManualApprovalActions({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const [note, setNote] = useState("")
  const { user } = useAuth()
  const { caseData } = useGetCase({ caseId, workspaceId })
  const telegramApproval = getTelegramApproval(caseData?.payload)
  const actions = telegramApproval?.actions ?? []
  const { createComment, createCommentIsPending } = useCreateCaseComment({
    caseId,
    workspaceId,
  })

  const noteSuffix = note.trim() ? `\n\n${note.trim()}` : ""

  const handleCaseDecision = async (decision: "approve" | "reject") => {
    const label = decision === "approve" ? "✅ **Approved**" : "❌ **Rejected**"
    const content = `${label} by ${getDisplayName(user)}${noteSuffix}`
    try {
      await createComment({ content })
      setNote("")
      toast({
        title:
          decision === "approve" ? "Approval recorded" : "Rejection recorded",
        description: "Saved as a case comment.",
      })
    } catch (error) {
      console.error("Error recording manual approval decision", error)
    }
  }

  const handleActionDecision = async (
    index: number,
    action: string,
    decision: "approve" | "reject"
  ) => {
    const label = decision === "approve" ? "✅ **Approved**" : "❌ **Rejected**"
    const content = `${label} measure #${index + 1} (${action}) by ${getDisplayName(user)}${noteSuffix}`
    try {
      await createComment({ content })
      toast({
        title:
          decision === "approve"
            ? `Measure #${index + 1} approved`
            : `Measure #${index + 1} rejected`,
        description: "Saved as a case comment.",
      })
    } catch (error) {
      console.error("Error recording manual approval decision", error)
    }
  }

  return (
    <section className="mb-4 space-y-3 rounded-lg border border-border/60 px-4 py-3">
      {telegramApproval?.text ? (
        <div className="whitespace-pre-wrap rounded-md border border-dashed border-border/60 bg-muted/30 px-3 py-2 text-xs text-foreground">
          {telegramApproval.text}
        </div>
      ) : null}

      {actions.length > 0 ? (
        <div className="space-y-2">
          {actions.map((action, index) => (
            <div
              key={index}
              className="flex items-center justify-between gap-3 rounded-md border border-border/60 px-3 py-2"
            >
              <span className="min-w-0 truncate text-sm">
                {index + 1}. {action}
              </span>
              <div className="flex shrink-0 items-center gap-1.5">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 gap-1 text-destructive hover:text-destructive"
                  disabled={createCommentIsPending}
                  onClick={() => handleActionDecision(index, action, "reject")}
                >
                  <XIcon className="size-3" />
                  Reject
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="h-7 gap-1"
                  disabled={createCommentIsPending}
                  onClick={() => handleActionDecision(index, action, "approve")}
                >
                  <CheckIcon className="size-3" />
                  Approve
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {actions.length === 0 ? (
        <>
          <p className="text-xs text-muted-foreground">
            Record your decision on this case's recommended action — same
            approve/reject sign-off as the Telegram flow, logged here as a
            comment.
          </p>
          <Textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Add an optional note..."
            className="min-h-12 resize-none text-sm"
            disabled={createCommentIsPending}
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 gap-1.5 text-destructive hover:text-destructive"
              disabled={createCommentIsPending}
              onClick={() => handleCaseDecision("reject")}
            >
              <XIcon className="size-3.5" />
              Reject
            </Button>
            <Button
              type="button"
              size="sm"
              className="h-8 gap-1.5"
              disabled={createCommentIsPending}
              onClick={() => handleCaseDecision("approve")}
            >
              <CheckIcon className="size-3.5" />
              Approve
            </Button>
          </div>
        </>
      ) : null}
    </section>
  )
}
