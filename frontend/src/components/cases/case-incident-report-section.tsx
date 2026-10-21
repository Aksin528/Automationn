"use client"

import { Download, FileText, Pencil, Plus, Trash2 } from "lucide-react"
import { useState } from "react"
import type { CaseRead } from "@/client"
import {
  CaseIncidentReportDialog,
  getIncidentReportDownloadUrl,
} from "@/components/cases/case-incident-report-dialog"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useUpdateCase } from "@/lib/hooks"

interface CaseIncidentReportSectionProps {
  caseId: string
  workspaceId: string
  caseData: CaseRead | undefined
}

/**
 * "Report" tab content. Unlike attachments, a case has at most one saved
 * incident report (stored as `Case.payload["incident_report"]` -- see
 * `CaseIncidentReportDialog`'s module docstring), so this renders either an
 * empty-state "Create report" prompt or a single row for the saved report,
 * styled to match `CaseAttachmentsSection`'s row/action pattern so the two
 * tabs feel consistent.
 */
export function CaseIncidentReportSection({
  caseId,
  workspaceId,
  caseData,
}: CaseIncidentReportSectionProps) {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [downloadPending, setDownloadPending] = useState(false)
  const { updateCase, updateCaseIsPending } = useUpdateCase({
    caseId,
    workspaceId,
  })

  const savedReport = caseData?.payload?.incident_report
  const hasReport = savedReport != null && typeof savedReport === "object"

  const handleDownload = async () => {
    if (!hasReport) {
      return
    }
    setDownloadPending(true)
    try {
      const { downloadUrl, fileName } = await getIncidentReportDownloadUrl(
        caseId,
        workspaceId
      )
      const link = document.createElement("a")
      link.href = downloadUrl
      link.download = fileName
      link.rel = "noopener"
      link.style.display = "none"
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } catch (error) {
      console.error("Error downloading incident report PDF", error)
      toast({
        title: "Download failed",
        description: "Failed to download the report PDF.",
      })
    } finally {
      setDownloadPending(false)
    }
  }

  const handleDelete = async () => {
    try {
      const nextPayload = { ...(caseData?.payload ?? {}) }
      delete nextPayload.incident_report
      await updateCase({ payload: nextPayload })
      toast({
        title: "Incident report deleted",
        description: "Removed from this case.",
      })
    } catch (error) {
      console.error("Error deleting incident report", error)
      toast({
        title: "Delete failed",
        description: "Failed to delete the incident report.",
      })
    }
  }

  return (
    <TooltipProvider>
      <div className="mx-auto w-full">
        <div className="space-y-4 p-4">
          {hasReport ? (
            <div className="flex items-center gap-4 rounded-md p-2 px-3.5 transition-colors hover:bg-muted/40 group">
              <div className="rounded bg-red-50 p-1 text-red-600">
                <FileText className="h-4 w-4" />
              </div>

              <div className="flex-1">
                <span className="truncate text-sm font-medium">
                  Kiberinsidentə dair Hesabat
                </span>
              </div>

              <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setDialogOpen(true)}
                      disabled={updateCaseIsPending}
                    >
                      <Pencil className="size-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <div className="text-xs">Open report</div>
                  </TooltipContent>
                </Tooltip>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={handleDownload}
                      disabled={downloadPending || updateCaseIsPending}
                    >
                      <Download className="size-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <div className="text-xs">
                      {downloadPending
                        ? "Generating PDF..."
                        : "Download as PDF"}
                    </div>
                  </TooltipContent>
                </Tooltip>

                <Button
                  variant="ghost"
                  size="sm"
                  className="text-red-600 hover:bg-red-50 hover:text-red-700"
                  title="Delete report"
                  onClick={handleDelete}
                  disabled={updateCaseIsPending}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div
                onClick={() => setDialogOpen(true)}
                className={
                  "group flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-muted-foreground/25 p-1.5 transition-all hover:border-muted-foreground/50 hover:bg-muted/30"
                }
              >
                <div className="rounded bg-muted p-1.5 transition-colors group-hover:bg-muted-foreground/10">
                  <Plus className="h-3.5 w-3.5 text-muted-foreground" />
                </div>
                <span className="text-xs text-muted-foreground transition-colors group-hover:text-foreground">
                  Create report
                </span>
              </div>

              <div className="flex flex-col items-center justify-center py-4">
                <div className="mb-3 rounded-full bg-muted/50 p-2">
                  <FileText className="h-6 w-6 text-muted-foreground" />
                </div>
                <h3 className="mb-1 text-sm font-medium text-muted-foreground">
                  No report yet
                </h3>
                <p className="max-w-[250px] text-center text-xs text-muted-foreground/75">
                  Create the cyber-incident report by clicking the button above.
                </p>
              </div>
            </>
          )}
        </div>
      </div>

      <CaseIncidentReportDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        caseId={caseId}
        workspaceId={workspaceId}
        caseData={caseData}
      />
    </TooltipProvider>
  )
}
