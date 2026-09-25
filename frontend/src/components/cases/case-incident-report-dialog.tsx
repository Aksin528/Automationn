"use client"

import { useEffect, useState } from "react"
import type { CaseRead } from "@/client"
import {
  EMPTY_REPORT,
  INCIDENT_TYPES,
  type IncidentReportPayload,
  NOTIFIED_TO,
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

/** Asks the backend for a pre-signed download URL to the incident report PDF
 * already rendered and stored server-side by `upload_incident_report_pdf`
 * (see that function's docstring for why rendering moved out of the
 * browser). Downloading therefore never re-triggers an html2canvas/jsPDF
 * render -- it just fetches the same file the "Save" flow already
 * produced, via `GET /cases/{caseId}/report/pdf`.
 *
 * Plain `fetch` for the same reason as `persistIncidentReportPdf` below:
 * no generated `@/client` binding for this route yet. */
export async function getIncidentReportDownloadUrl(
  caseId: string,
  workspaceId: string
): Promise<{ downloadUrl: string; fileName: string }> {
  const response = await fetch(
    `/api/workspaces/${workspaceId}/cases/${caseId}/report/pdf`,
    {
      method: "GET",
      credentials: "include",
    }
  )
  if (!response.ok) {
    throw new Error(
      `Failed to get report PDF download link (${response.status} ${response.statusText})`
    )
  }
  const data = (await response.json()) as {
    download_url: string
    file_name: string
  }
  return { downloadUrl: data.download_url, fileName: data.file_name }
}

/** Asks the backend to render the just-saved report to PDF and persist it to
 * blob storage (the `reports` bucket in MinIO), via
 * `POST /cases/{caseId}/report/pdf`. No body: the endpoint re-renders
 * `Case.payload["incident_report"]` itself (see `tracecat/cases/
 * incident_report_pdf.py`), which the caller's `updateCase` call already
 * saved moments before. Deliberately not client-side rendering -- the old
 * html2canvas/jsPDF path could block the tab's main thread for up to a
 * minute on a fully-filled report; this is a cheap POST with no rendering
 * work in the browser at all.
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
