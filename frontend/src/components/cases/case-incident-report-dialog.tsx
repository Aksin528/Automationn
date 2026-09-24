"use client"

import html2canvas from "html2canvas"
import jsPDF from "jspdf"
import { useEffect, useState } from "react"
import type { CaseRead } from "@/client"
import {
  buildPrintElement,
  EMPTY_REPORT,
  INCIDENT_TYPES,
  type IncidentReportPayload,
  NOTIFIED_TO,
  shouldKeepWithNext,
} from "@/components/cases/case-incident-report-print"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import { useUpdateCase } from "@/lib/hooks"

// Re-exported so existing importers (e.g. `case-incident-report-section.tsx`)
// don't need to know these moved to `case-incident-report-print.ts`.
export { INCIDENT_TYPES, NOTIFIED_TO }
export type { IncidentReportPayload }

interface RenderedBlock {
  /** True when this block must not be the last one on its page -- a
   * section heading or a field label whose value follows separately. */
  keepWithNext: boolean
  dataUrl: string
  heightMm: number
}

/** Captures one direct child of the printed report (title, section
 * heading, field row, or one paragraph of the description) as its own
 * PNG, sized to `contentWidthMm` while preserving its aspect ratio.
 *
 * Capturing each block separately -- instead of one tall screenshot of
 * the whole report -- is what guarantees a page break can never fall
 * inside a block's own text: there is no single giant image left to slice
 * at an arbitrary height in the first place. `windowWidth`/`windowHeight`
 * pin the cloned document to the real one's size so the capture doesn't
 * shift (and clip its last line) depending on how the page behind it
 * happens to be scrolled or sized. */
async function renderBlock(
  child: HTMLElement,
  contentWidthMm: number
): Promise<RenderedBlock> {
  const canvas = await html2canvas(child, {
    // scale:2 (the previous value) roughly quadruples the pixels
    // html2canvas has to paint per block versus scale:1, which is the
    // dominant cost for a long, fully-filled report -- it's what turned a
    // save into a ~60s main-thread freeze. 1x stays sharp for rendered DOM
    // text (unlike a photo, there's no source detail scale:2 recovers).
    scale: 1,
    backgroundColor: "#ffffff",
    useCORS: true,
    scrollX: 0,
    scrollY: 0,
    windowWidth: document.documentElement.scrollWidth,
    windowHeight: document.documentElement.scrollHeight,
  })
  return {
    keepWithNext: shouldKeepWithNext(child),
    dataUrl: canvas.toDataURL("image/png"),
    heightMm: (canvas.height * contentWidthMm) / canvas.width,
  }
}

/** Renders the saved report to an off-screen DOM node, captures each of
 * its top-level blocks (title, section headings, field rows) as its own
 * image via `renderBlock`, then lays them out down the page -- starting a
 * new PDF page whenever the next block wouldn't fit in what's left of the
 * current one -- and returns the assembled `jsPDF` document (not yet saved
 * or uploaded; callers decide what to do with it).
 *
 * Laying out whole blocks this way (rather than slicing one tall
 * screenshot at fixed page-height intervals) is what guarantees a page
 * break never falls inside a field's label/value or mid-word: the
 * smallest unit ever placed is one whole block. A block flagged
 * `keepWithNext` (section heading, or a label whose value is a separate
 * block) is additionally never left alone at the bottom of a page --
 * placing it requires room for the block right after it too, otherwise
 * both move to the next page together. */
async function buildIncidentReportPdf(
  report: Partial<IncidentReportPayload>,
  caseShortId: string
): Promise<jsPDF> {
  // Merge with defaults, matching `buildInitialReport`'s own leniency --
  // a report saved by an earlier version of this form may be missing
  // fields added since, and `.join` on a missing array field would
  // otherwise throw instead of just rendering as empty.
  const complete: IncidentReportPayload = { ...EMPTY_REPORT, ...report }
  const element = buildPrintElement(complete, caseShortId)
  document.body.appendChild(element)

  try {
    const pdf = new jsPDF({ orientation: "p", unit: "mm", format: "a4" })
    const pageWidthMm = pdf.internal.pageSize.getWidth()
    const pageHeightMm = pdf.internal.pageSize.getHeight()
    const marginMm = 12
    const contentWidthMm = pageWidthMm - marginMm * 2
    const usableHeightMm = pageHeightMm - marginMm * 2

    const children = Array.from(element.children) as HTMLElement[]
    const blocks = await Promise.all(
      children.map((child) => renderBlock(child, contentWidthMm))
    )

    let cursorMm = 0
    let placedAnything = false

    for (let i = 0; i < blocks.length; i++) {
      const block = blocks[i]
      const nextBlock = i + 1 < blocks.length ? blocks[i + 1] : null
      const neededMm =
        block.keepWithNext && nextBlock
          ? block.heightMm + nextBlock.heightMm
          : block.heightMm
      const remainingMm = usableHeightMm - cursorMm

      if (placedAnything && neededMm > remainingMm) {
        pdf.addPage()
        cursorMm = 0
      }

      pdf.addImage(
        block.dataUrl,
        "PNG",
        marginMm,
        marginMm + cursorMm,
        contentWidthMm,
        block.heightMm
      )
      cursorMm += block.heightMm
      placedAnything = true
    }

    return pdf
  } finally {
    document.body.removeChild(element)
  }
}

/** Builds the report PDF and triggers a browser download -- the existing
 * "Download" button behavior, unchanged. */
export async function downloadIncidentReportPdf(
  report: Partial<IncidentReportPayload>,
  caseShortId: string
): Promise<void> {
  const pdf = await buildIncidentReportPdf(report, caseShortId)
  pdf.save(`incident-report-${caseShortId}.pdf`)
}

/** Asks the backend to render the just-saved report to PDF and persist it to
 * blob storage (the `reports` bucket in MinIO), via
 * `POST /cases/{caseId}/report/pdf`. No body: the endpoint re-renders
 * `Case.payload["incident_report"]` itself (see `tracecat/cases/
 * incident_report_pdf.py`), which the caller's `updateCase` call already
 * saved moments before. Deliberately NOT client-side (no `buildIncidentReportPdf`
 * call here) -- that used html2canvas/jsPDF and could block the tab's main
 * thread for up to a minute on a fully-filled report; this is a cheap POST
 * with no rendering work in the browser at all.
 *
 * This is a plain `fetch` rather than the generated `@/client` because it
 * has no generated binding (see `tracecat/cases/router.py`'s
 * `upload_incident_report_pdf`); `credentials: "include"` mirrors the
 * generated client's own default (`OpenAPI.CREDENTIALS`), so the browser's
 * existing session cookie is sent the same way it would be for any other
 * case API call. Failures here are intentionally non-fatal to the caller --
 * the Postgres-backed report save must not fail just because the blob
 * snapshot couldn't be written. */
async function persistIncidentReportPdf(
  caseId: string,
  workspaceId: string
): Promise<void> {
  const response = await fetch(
    `/api/workspaces/${workspaceId}/cases/${caseId}/report/pdf`,
    {
      method: "POST",
      credentials: "include",
    }
  )
  if (!response.ok) {
    throw new Error(
      `Failed to store report PDF (${response.status} ${response.statusText})`
    )
  }
}

function toDateInputValue(iso: string | null | undefined): string {
  if (!iso) return ""
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return ""
  return parsed.toISOString().slice(0, 10)
}

/** Builds the starting draft: saved report if one exists, otherwise a fresh
 * one pre-filled from whatever the case already has (detection date,
 * description, tagged system). Everything else — reporter details, incident
 * type checkboxes, notified-to checkboxes, activities taken, evaluation,
 * monitoring — has no case-level source and starts blank. */
function buildInitialReport(
  caseData: CaseRead | undefined
): IncidentReportPayload {
  const saved = caseData?.payload?.incident_report
  if (saved && typeof saved === "object") {
    return { ...EMPTY_REPORT, ...(saved as Partial<IncidentReportPayload>) }
  }
  return {
    ...EMPTY_REPORT,
    detectedAt: toDateInputValue(caseData?.created_at),
    description: caseData?.description ?? "",
    detectedSystem: (caseData?.tags ?? []).map((tag) => tag.name).join(", "),
  }
}

function CheckboxGroup({
  options,
  selected,
  onChange,
}: {
  options: readonly string[]
  selected: string[]
  onChange: (next: string[]) => void
}) {
  return (
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      {options.map((option) => {
        const checked = selected.includes(option)
        return (
          <label
            key={option}
            className="flex items-start gap-2 text-sm leading-snug"
          >
            <Checkbox
              checked={checked}
              onCheckedChange={(next) => {
                if (next) {
                  onChange([...selected, option])
                } else {
                  onChange(selected.filter((value) => value !== option))
                }
              }}
              className="mt-0.5"
            />
            <span>{option}</span>
          </label>
        )
      })}
    </div>
  )
}

function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  )
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h4 className="rounded-md bg-muted px-3 py-1.5 text-sm font-medium">
      {children}
    </h4>
  )
}

/**
 * Full cyber-incident report form for a case, opened as a popup (not an
 * inline tab) from a trigger button in the case panel's tab row. Saved as
 * `Case.payload["incident_report"]` via the existing case-update mutation —
 * no new backend endpoint or migration. See `IncidentReportPayload` for the
 * complete field list and `buildInitialReport` for which fields auto-fill
 * from the case itself vs. start blank.
 */
export function CaseIncidentReportDialog({
  open,
  onOpenChange,
  caseId,
  workspaceId,
  caseData,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  caseId: string
  workspaceId: string
  caseData: CaseRead | undefined
}) {
  const [report, setReport] = useState<IncidentReportPayload>(EMPTY_REPORT)
  const { updateCase, updateCaseIsPending } = useUpdateCase({
    caseId,
    workspaceId,
  })

  useEffect(() => {
    if (open) {
      setReport(buildInitialReport(caseData))
    }
    // Only re-derive when the dialog opens, not on every caseData refetch —
    // otherwise in-progress edits would get clobbered by a background
    // refetch while the analyst is typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  function set<K extends keyof IncidentReportPayload>(
    key: K,
    value: IncidentReportPayload[K]
  ) {
    setReport((prev) => ({ ...prev, [key]: value }))
  }

  const handleSave = async () => {
    try {
      await updateCase({
        payload: { ...(caseData?.payload ?? {}), incident_report: report },
      })
    } catch (error) {
      console.error("Error saving incident report", error)
      return
    }

    toast({
      title: "Incident report saved",
      description: "Stored on this case.",
    })
    onOpenChange(false)

    // The backend re-renders the payload just saved above into a PDF itself
    // (see `persistIncidentReportPdf`'s docstring) -- no client-side
    // rendering happens here, so there's no heavy work to keep off the main
    // thread and nothing to defer. Still fire-and-forget: the Postgres save
    // above is already the source of truth for editing, so a failure here
    // only gets a toast, never undoes it.
    void persistIncidentReportPdf(caseId, workspaceId).catch((error) => {
      console.error("Error storing incident report PDF", error)
      toast({
        title: "Report PDF upload failed",
        description:
          "Report fields were saved, but the PDF snapshot failed to upload.",
      })
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>Kiberinsidentə dair Hesabat</DialogTitle>
          <DialogDescription>
            Detected date, description, and tagged system are pre-filled from
            this case. Everything else is filled in manually.
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[65vh] pr-4">
          <div className="space-y-6 pb-2 pr-1">
            <div className="space-y-3">
              <SectionHeading>Kiberinsidentin aşkarlanması</SectionHeading>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Ad">
                  <Input
                    value={report.reporterName}
                    onChange={(e) => set("reporterName", e.target.value)}
                  />
                </Field>
                <Field label="Soyad">
                  <Input
                    value={report.reporterSurname}
                    onChange={(e) => set("reporterSurname", e.target.value)}
                  />
                </Field>
                <Field label="Vəzifə">
                  <Input
                    value={report.reporterPosition}
                    onChange={(e) => set("reporterPosition", e.target.value)}
                  />
                </Field>
                <Field label="Telefon">
                  <Input
                    value={report.reporterPhone}
                    onChange={(e) => set("reporterPhone", e.target.value)}
                  />
                </Field>
                <Field label="İş ünvanı">
                  <Input
                    value={report.reporterWorkAddress}
                    onChange={(e) => set("reporterWorkAddress", e.target.value)}
                  />
                </Field>
                <Field label="Aşkarlandığı sistem və ya tətbiq">
                  <Input
                    value={report.detectedSystem}
                    onChange={(e) => set("detectedSystem", e.target.value)}
                  />
                </Field>
                <Field label="Aşkarlanma tarixi">
                  <Input
                    type="date"
                    value={report.detectedAt}
                    onChange={(e) => set("detectedAt", e.target.value)}
                  />
                </Field>
                <Field label="İlk cavabın verilmə tarixi">
                  <Input
                    type="date"
                    value={report.firstResponseAt}
                    onChange={(e) => set("firstResponseAt", e.target.value)}
                  />
                </Field>
                <Field label="Bağlanma tarixi">
                  <Input
                    type="date"
                    value={report.closedAt}
                    onChange={(e) => set("closedAt", e.target.value)}
                  />
                </Field>
              </div>
            </div>

            <Separator />

            <div className="space-y-3">
              <SectionHeading>Kiberinsidentin xülasəsi</SectionHeading>
              <Field label="Aşkarlanan kiberinsidentin növü">
                <CheckboxGroup
                  options={INCIDENT_TYPES}
                  selected={report.incidentTypes}
                  onChange={(next) => set("incidentTypes", next)}
                />
              </Field>
              <Field label="İnsidentin təsviri (başvermə səbəbi təsvir edilməklə)">
                <Textarea
                  value={report.description}
                  onChange={(e) => set("description", e.target.value)}
                  className="min-h-20"
                />
              </Field>
              <Field label="Cəlb olunan əməkdaşların adları">
                <Textarea
                  value={report.involvedEmployees}
                  onChange={(e) => set("involvedEmployees", e.target.value)}
                  className="min-h-14"
                />
              </Field>
            </div>

            <Separator />

            <div className="space-y-3">
              <SectionHeading>
                Məlumatın təqdim edildiyi struktur bölmə və ya təşkilat
              </SectionHeading>
              <CheckboxGroup
                options={NOTIFIED_TO}
                selected={report.notifiedTo}
                onChange={(next) => set("notifiedTo", next)}
              />
            </div>

            <Separator />

            <div className="space-y-3">
              <SectionHeading>
                Görülmüş fəaliyyətlər (başlama və bitmə tarixləri ilə)
              </SectionHeading>
              <Field label="Aşkarlanma metodları">
                <Textarea
                  value={report.detectionMethods}
                  onChange={(e) => set("detectionMethods", e.target.value)}
                  className="min-h-16"
                />
              </Field>
              <Field label="Qarşısının alınması metodları">
                <Textarea
                  value={report.preventionMethods}
                  onChange={(e) => set("preventionMethods", e.target.value)}
                  className="min-h-16"
                />
              </Field>
              <Field label="Toplanılmış sübutlar">
                <Textarea
                  value={report.collectedEvidence}
                  onChange={(e) => set("collectedEvidence", e.target.value)}
                  className="min-h-16"
                />
              </Field>
              <Field label="Aradan qaldırılması metodları">
                <Textarea
                  value={report.remediationMethods}
                  onChange={(e) => set("remediationMethods", e.target.value)}
                  className="min-h-16"
                />
              </Field>
              <Field label="Bərpa metodları">
                <Textarea
                  value={report.recoveryMethods}
                  onChange={(e) => set("recoveryMethods", e.target.value)}
                  className="min-h-16"
                />
              </Field>
            </div>

            <Separator />

            <div className="space-y-3">
              <SectionHeading>Qiymətləndirmə</SectionHeading>
              <Field label="İşçi qrupu cavab tədbirlərinin görülməsində nə dərəcədə effektiv idilər?">
                <Textarea
                  value={report.teamEffectiveness}
                  onChange={(e) => set("teamEffectiveness", e.target.value)}
                  className="min-h-14"
                />
              </Field>
              <Field label="Sənədləşdirilmiş prosedurlara əməl edildi?">
                <Textarea
                  value={report.proceduresFollowed}
                  onChange={(e) => set("proceduresFollowed", e.target.value)}
                  className="min-h-14"
                />
              </Field>
              <Field label="Qısa zaman ərzində hansı informasiyaların əldə edilməsi zərurəti yaranmışdı?">
                <Textarea
                  value={report.infoNeededQuickly}
                  onChange={(e) => set("infoNeededQuickly", e.target.value)}
                  className="min-h-14"
                />
              </Field>
              <Field label="Bərpaya mane olacaq və ya gecikdirəcək hər hansı addım və ya fəaliyyət izlənildi?">
                <Textarea
                  value={report.recoveryBlockers}
                  onChange={(e) => set("recoveryBlockers", e.target.value)}
                  className="min-h-14"
                />
              </Field>
              <Field label="İşçi qrupu növbəti dəfə kiberinsident baş verərkən nəyi fərqli edə bilərlər?">
                <Textarea
                  value={report.whatToImproveNextTime}
                  onChange={(e) => set("whatToImproveNextTime", e.target.value)}
                  className="min-h-14"
                />
              </Field>
              <Field label="Gələcəkdə kiberinsidentin baş verməməsi üçün hansı tədbirlər və fəaliyyətlər yerinə yetirildi?">
                <Textarea
                  value={report.futureActionsTaken}
                  onChange={(e) => set("futureActionsTaken", e.target.value)}
                  className="min-h-14"
                />
              </Field>
              <Field label="Hansı bərpaedici və ya qabaqlayıcı tədbirlər kiberinsidentin gələcəkdə başvermə ehtimalını azalda bilər?">
                <Textarea
                  value={report.futurePreventiveMeasures}
                  onChange={(e) =>
                    set("futurePreventiveMeasures", e.target.value)
                  }
                  className="min-h-14"
                />
              </Field>
              <Field label="Gələcəkdə aşkarlanma, analiz və aradan qaldırılmasının asanlaşdırılması üçün hansı əlavə resurslara ehtiyac duyulur?">
                <Textarea
                  value={report.additionalResourcesNeeded}
                  onChange={(e) =>
                    set("additionalResourcesNeeded", e.target.value)
                  }
                  className="min-h-14"
                />
              </Field>
              <Field label="Digər nəticələr və tövsiyələr">
                <Textarea
                  value={report.otherRecommendations}
                  onChange={(e) => set("otherRecommendations", e.target.value)}
                  className="min-h-14"
                />
              </Field>
            </div>

            <Separator />

            <div className="space-y-3">
              <SectionHeading>Monitorinq</SectionHeading>
              <Field label="Nəzərdən keçirildi (Kibertəhlükəsizlik Mütəxəssisi / İdarəsi / Digər)">
                <Input
                  value={report.reviewedBy}
                  onChange={(e) => set("reviewedBy", e.target.value)}
                />
              </Field>
              <Field label="Nəzərdən keçirən şəxs">
                <Input
                  value={report.reviewer}
                  onChange={(e) => set("reviewer", e.target.value)}
                />
              </Field>
              <Field label="Həyata keçirilməsi tövsiyə olunan tələblər">
                <Textarea
                  value={report.reviewedRequirements}
                  onChange={(e) => set("reviewedRequirements", e.target.value)}
                  className="min-h-14"
                />
              </Field>
            </div>
          </div>
        </ScrollArea>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={updateCaseIsPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={handleSave}
            disabled={updateCaseIsPending}
          >
            Save report
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
