/** Pure DOM-building logic for the printed/PDF version of the incident
 * report -- deliberately kept free of any jsPDF/html2canvas import so it
 * stays jest-testable without pulling in jsPDF's ESM build (which jest's
 * default transform can't parse). `case-incident-report-dialog.tsx`'s
 * `downloadIncidentReportPdf` is the only caller of `buildPrintElement`,
 * and owns the actual PDF generation. */

export const INCIDENT_TYPES = [
  "Fişinq",
  "Şifrə konfidensiallığı",
  "Şəbəkəyə müdaxilə",
  "Hesabın ələ keçirilməsi və ya sızdırılması",
  "Ransomware yoluxmaları",
  "Sistemlərə səlahiyyətsiz giriş",
  "İmtiyazlardan sui-istifadə",
  "Zərərli proqram yoluxmaları",
  "İnformasiya, tətbiq, sistem və ya aparat təminatlarına edilmiş səlahiyyətsiz dəyişikliklər",
  "İnformasiyanın səlahiyyətsiz açıqlanması və ya yayılması",
  "İnformasiya aktivlərinin oğurlanması və ya itirilməsi",
  "İnformasiya təhlükəsizliyi sənədlərində göstərilmiş tələblərin pozulması",
  "Şübhəli sistem davranışı və ya xətası",
  "Test",
  "Fiziki zərər",
  "Digər",
] as const

export const NOTIFIED_TO = [
  "Rəhbərlik",
  "Sistem/Tətbiq sahibi",
  "İnformasiya təhlükəsizliyi komandası",
  "Təchizatçı",
  "İnzibatçılar",
  "Dövlət qurumları",
  "İnsan Resursları",
  "Hüquq",
  "Digər",
] as const

/** Full shape of the cyber-incident report form, stored as one JSON blob at
 * `Case.payload["incident_report"]`. Chosen over a dedicated table/columns
 * to avoid a migration — same trick as `telegram_approval` (see
 * `case-manual-approval-actions.tsx`): the case's existing `payload` field
 * already round-trips through the standard case-read/update API. */
export interface IncidentReportPayload {
  reporterName: string
  reporterSurname: string
  reporterPosition: string
  reporterPhone: string
  reporterWorkAddress: string
  detectedSystem: string
  detectedAt: string
  firstResponseAt: string
  closedAt: string
  incidentTypes: string[]
  description: string
  involvedEmployees: string
  notifiedTo: string[]
  detectionMethods: string
  preventionMethods: string
  collectedEvidence: string
  remediationMethods: string
  recoveryMethods: string
  teamEffectiveness: string
  proceduresFollowed: string
  infoNeededQuickly: string
  recoveryBlockers: string
  whatToImproveNextTime: string
  futureActionsTaken: string
  futurePreventiveMeasures: string
  additionalResourcesNeeded: string
  otherRecommendations: string
  reviewedBy: string
  reviewedRequirements: string
  reviewer: string
}

export const EMPTY_REPORT: IncidentReportPayload = {
  reporterName: "",
  reporterSurname: "",
  reporterPosition: "",
  reporterPhone: "",
  reporterWorkAddress: "",
  detectedSystem: "",
  detectedAt: "",
  firstResponseAt: "",
  closedAt: "",
  incidentTypes: [],
  description: "",
  involvedEmployees: "",
  notifiedTo: [],
  detectionMethods: "",
  preventionMethods: "",
  collectedEvidence: "",
  remediationMethods: "",
  recoveryMethods: "",
  teamEffectiveness: "",
  proceduresFollowed: "",
  infoNeededQuickly: "",
  recoveryBlockers: "",
  whatToImproveNextTime: "",
  futureActionsTaken: "",
  futurePreventiveMeasures: "",
  additionalResourcesNeeded: "",
  otherRecommendations: "",
  reviewedBy: "",
  reviewedRequirements: "",
  reviewer: "",
}

/** One printable field. "text" renders `value` as-is; "list" renders
 * `items` as a bulleted list (used for the checkbox-group fields, instead
 * of joining them into one comma-separated line); "markdown" runs `value`
 * through `renderSimpleMarkdown` (used only for the case description,
 * which is authored as markdown upstream). */
type PrintField =
  | { label: string; kind: "text"; value: string }
  | { label: string; kind: "list"; items: string[] }
  | { label: string; kind: "markdown"; value: string }

interface PrintSection {
  heading: string
  fields: PrintField[]
}

function fallbackDash(value: string): string {
  return value.trim() ? value : "—"
}

function textField(label: string, value: string): PrintField {
  return { label, kind: "text", value: fallbackDash(value) }
}

function listField(label: string, items: string[]): PrintField {
  return { label, kind: "list", items }
}

/** Mirrors the dialog's own sections/labels/field order exactly, so the
 * downloaded PDF matches what the analyst sees and fills in on screen. */
function buildPrintSections(report: IncidentReportPayload): PrintSection[] {
  return [
    {
      heading: "Kiberinsidentin aşkarlanması",
      fields: [
        textField("Ad", report.reporterName),
        textField("Soyad", report.reporterSurname),
        textField("Vəzifə", report.reporterPosition),
        textField("Telefon", report.reporterPhone),
        textField("İş ünvanı", report.reporterWorkAddress),
        textField("Aşkarlandığı sistem və ya tətbiq", report.detectedSystem),
        textField("Aşkarlanma tarixi", report.detectedAt),
        textField("İlk cavabın verilmə tarixi", report.firstResponseAt),
        textField("Bağlanma tarixi", report.closedAt),
      ],
    },
    {
      heading: "Kiberinsidentin xülasəsi",
      fields: [
        listField("Aşkarlanan kiberinsidentin növü", report.incidentTypes),
        {
          label: "İnsidentin təsviri (başvermə səbəbi təsvir edilməklə)",
          kind: "markdown",
          value: report.description,
        },
        textField("Cəlb olunan əməkdaşların adları", report.involvedEmployees),
      ],
    },
    {
      heading: "Məlumatın təqdim edildiyi struktur bölmə və ya təşkilat",
      fields: [listField("Bildiriş edilib", report.notifiedTo)],
    },
    {
      heading: "Görülmüş fəaliyyətlər (başlama və bitmə tarixləri ilə)",
      fields: [
        textField("Aşkarlanma metodları", report.detectionMethods),
        textField("Qarşısının alınması metodları", report.preventionMethods),
        textField("Toplanılmış sübutlar", report.collectedEvidence),
        textField("Aradan qaldırılması metodları", report.remediationMethods),
        textField("Bərpa metodları", report.recoveryMethods),
      ],
    },
    {
      heading: "Qiymətləndirmə",
      fields: [
        textField(
          "İşçi qrupu cavab tədbirlərinin görülməsində nə dərəcədə effektiv idilər?",
          report.teamEffectiveness
        ),
        textField(
          "Sənədləşdirilmiş prosedurlara əməl edildi?",
          report.proceduresFollowed
        ),
        textField(
          "Qısa zaman ərzində hansı informasiyaların əldə edilməsi zərurəti yaranmışdı?",
          report.infoNeededQuickly
        ),
        textField(
          "Bərpaya mane olacaq və ya gecikdirəcək hər hansı addım və ya fəaliyyət izlənildi?",
          report.recoveryBlockers
        ),
        textField(
          "İşçi qrupu növbəti dəfə kiberinsident baş verərkən nəyi fərqli edə bilərlər?",
          report.whatToImproveNextTime
        ),
        textField(
          "Gələcəkdə kiberinsidentin baş verməməsi üçün hansı tədbirlər və fəaliyyətlər yerinə yetirildi?",
          report.futureActionsTaken
        ),
        textField(
          "Hansı bərpaedici və ya qabaqlayıcı tədbirlər kiberinsidentin gələcəkdə başvermə ehtimalını azalda bilər?",
          report.futurePreventiveMeasures
        ),
        textField(
          "Gələcəkdə aşkarlanma, analiz və aradan qaldırılmasının asanlaşdırılması üçün hansı əlavə resurslara ehtiyac duyulur?",
          report.additionalResourcesNeeded
        ),
        textField("Digər nəticələr və tövsiyələr", report.otherRecommendations),
      ],
    },
    {
      heading: "Monitorinq",
      fields: [
        textField(
          "Nəzərdən keçirildi (Kibertəhlükəsizlik Mütəxəssisi / İdarəsi / Digər)",
          report.reviewedBy
        ),
        textField("Nəzərdən keçirən şəxs", report.reviewer),
        textField(
          "Həyata keçirilməsi tövsiyə olunan tələblər",
          report.reviewedRequirements
        ),
      ],
    },
  ]
}

/** Splits a line on `**bold**` markers and appends alternating plain-text
 * and `<strong>` nodes to `target`. Only used by `renderSimpleMarkdown`. */
function appendInlineBold(target: HTMLElement, line: string): void {
  const parts = line.split("**")
  parts.forEach((part, index) => {
    if (!part) {
      return
    }
    if (index % 2 === 1) {
      const strong = document.createElement("strong")
      strong.textContent = part
      target.appendChild(strong)
    } else {
      target.appendChild(document.createTextNode(part))
    }
  })
}

/** Minimal, purpose-built markdown-to-DOM renderer for the "description"
 * field only -- covers the specific subset seen in real Cortex/Splunk/Check
 * Point/PMG-pulled case descriptions (`##`/`###` headings, `**bold**`,
 * "- "/"* " bullet lists, plain paragraphs), not full CommonMark. Renders
 * into real DOM nodes (not innerHTML) so there's no HTML-injection risk
 * from case description text. The case panel's own description editor has
 * its own, separate rich-text rendering -- this exists only for the
 * printed PDF. */
export function renderSimpleMarkdown(
  container: HTMLElement,
  markdown: string
): void {
  const text = markdown.trim()
  if (!text) {
    container.textContent = "—"
    return
  }

  let currentList: HTMLUListElement | null = null

  for (const rawLine of text.split("\n")) {
    const line = rawLine.trimEnd()
    const headingMatch = line.match(/^(#{1,4})\s+(.*)$/)
    const bulletMatch = line.match(/^[-*]\s+(.*)$/)

    if (headingMatch) {
      currentList = null
      const [, hashes, headingText] = headingMatch
      const heading = document.createElement("div")
      heading.style.fontWeight = "700"
      heading.style.margin = "0"
      // Spacing lives in padding, never margin: each of these ends up as
      // its own captured block, and html2canvas measures the border box,
      // so a margin would silently vanish from the PDF (and the bottom
      // padding doubles as the clipping guard -- see BLOCK_BOTTOM_PAD).
      heading.style.padding = "8px 0 4px 0"
      heading.style.fontSize = hashes.length <= 2 ? "13px" : "12px"
      appendInlineBold(heading, headingText)
      container.appendChild(heading)
      continue
    }

    if (bulletMatch) {
      if (!currentList) {
        currentList = document.createElement("ul")
        currentList.style.margin = "0"
        currentList.style.padding = "2px 0 8px 18px"
        container.appendChild(currentList)
      }
      const li = document.createElement("li")
      li.style.margin = "0"
      li.style.paddingBottom = "2px"
      appendInlineBold(li, bulletMatch[1])
      currentList.appendChild(li)
      continue
    }

    currentList = null

    if (!line.trim()) {
      const spacer = document.createElement("div")
      spacer.style.height = "6px"
      container.appendChild(spacer)
      continue
    }

    const para = document.createElement("div")
    para.style.margin = "0"
    para.style.padding = "0 0 6px 0"
    para.style.wordBreak = "break-word"
    appendInlineBold(para, line)
    container.appendChild(para)
  }
}

/** Marks a block that must not be the last thing on a page: the block
 * right after it has to fit on the same page, or both move to the next
 * one together. Read back by `shouldKeepWithNext`. */
const KEEP_WITH_NEXT_ATTR = "data-keep-with-next"

/** True when this block must stay on the same page as the block after it
 * (report title, section heading, or a field label whose value follows as
 * a separate block). */
export function shouldKeepWithNext(element: HTMLElement): boolean {
  return element.getAttribute(KEEP_WITH_NEXT_ATTR) === "true"
}

function keepWithNext<T extends HTMLElement>(element: T): T {
  element.setAttribute(KEEP_WITH_NEXT_ATTR, "true")
  return element
}

function buildFieldLabel(text: string): HTMLDivElement {
  const label = document.createElement("div")
  label.textContent = text
  label.style.color = "#666666"
  label.style.fontSize = "10.5px"
  label.style.margin = "0"
  label.style.padding = "0 0 2px 0"
  return label
}

/** Builds the printable report as an off-screen DOM node (plain HTML, not
 * React) so `html2canvas` can rasterize it. Rendering through the real DOM
 * with the browser's own font stack -- rather than drawing text directly
 * with jsPDF's built-in fonts -- sidesteps jsPDF's standard fonts not
 * covering Azerbaijani letters (ə, ğ, ş, ç, ı, ö, ü): the browser already
 * renders this text correctly everywhere else in the app, so capturing that
 * rendering guarantees the PDF matches it instead of risking mojibake from
 * an unembedded/incomplete font.
 *
 * Two layout rules matter to the PDF and are easy to break by accident:
 *
 * 1. Every direct child of the root is captured as its OWN image, and
 *    html2canvas measures the border box -- so vertical spacing must be
 *    padding, never margin (a margin is simply dropped from the PDF), and
 *    every block needs a few px of bottom padding, because the last text
 *    line otherwise gets clipped when the cloned document's font metrics
 *    round a hair taller than the measured box.
 * 2. A block is the smallest unit a page break can fall between, so the
 *    long description is emitted as one block PER markdown paragraph/
 *    list/heading rather than a single tall block -- otherwise it can
 *    neither be split nor fit, and leaves most of a page blank. */
export function buildPrintElement(
  report: IncidentReportPayload,
  caseShortId: string
): HTMLDivElement {
  const root = document.createElement("div")
  // Absolute (not fixed) keeps html2canvas's bounds maths in document
  // coordinates, which stays correct no matter how the real page behind
  // it happens to be scrolled when the analyst clicks download.
  root.style.position = "absolute"
  root.style.top = "0"
  root.style.left = "-99999px"
  root.style.width = "760px"
  root.style.background = "#ffffff"
  root.style.color = "#111111"
  root.style.fontFamily = "Arial, 'Helvetica Neue', Helvetica, sans-serif"
  root.style.fontSize = "12px"
  root.style.lineHeight = "1.5"

  const title = document.createElement("h1")
  title.textContent = "Kiberinsidentə dair Hesabat"
  title.style.fontSize = "18px"
  title.style.margin = "0"
  title.style.padding = "0 0 6px 0"
  root.appendChild(keepWithNext(title))

  const subtitle = document.createElement("div")
  subtitle.textContent = caseShortId
  subtitle.style.color = "#666666"
  subtitle.style.fontSize = "11px"
  subtitle.style.margin = "0"
  subtitle.style.padding = "0 0 14px 0"
  root.appendChild(subtitle)

  for (const section of buildPrintSections(report)) {
    // The grey band is the inner h2; the wrapper carries the spacing so
    // the band itself doesn't grow with it.
    const headingWrap = document.createElement("div")
    headingWrap.style.padding = "12px 0 8px 0"
    const heading = document.createElement("h2")
    heading.textContent = section.heading
    heading.style.fontSize = "13px"
    heading.style.background = "#f1f1f1"
    heading.style.padding = "7px 10px"
    heading.style.margin = "0"
    heading.style.borderRadius = "4px"
    headingWrap.appendChild(heading)
    root.appendChild(keepWithNext(headingWrap))

    for (const field of section.fields) {
      if (field.kind === "markdown") {
        root.appendChild(keepWithNext(buildFieldLabel(field.label)))

        const markdownContainer = document.createElement("div")
        renderSimpleMarkdown(markdownContainer, field.value)
        const markdownBlocks = Array.from(
          markdownContainer.children
        ) as HTMLElement[]

        if (markdownBlocks.length === 0) {
          // Empty description: `renderSimpleMarkdown` wrote a bare dash as
          // text, with no element to lift out.
          const empty = document.createElement("div")
          empty.textContent = markdownContainer.textContent || "—"
          empty.style.padding = "0 0 10px 0"
          root.appendChild(empty)
        } else {
          for (const block of markdownBlocks) {
            root.appendChild(block)
          }
          const lastBlock = markdownBlocks[markdownBlocks.length - 1]
          lastBlock.style.paddingBottom = "12px"
        }
        continue
      }

      const row = document.createElement("div")
      row.style.margin = "0"
      row.style.padding = "0 0 10px 0"
      row.appendChild(buildFieldLabel(field.label))

      if (field.kind === "list") {
        if (field.items.length === 0) {
          const empty = document.createElement("div")
          empty.textContent = "—"
          row.appendChild(empty)
        } else {
          const list = document.createElement("ul")
          list.style.margin = "0"
          list.style.padding = "0 0 0 18px"
          for (const item of field.items) {
            const li = document.createElement("li")
            li.textContent = item
            li.style.margin = "0"
            li.style.paddingBottom = "2px"
            list.appendChild(li)
          }
          row.appendChild(list)
        }
      } else {
        const value = document.createElement("div")
        value.textContent = field.value
        value.style.whiteSpace = "pre-wrap"
        value.style.wordBreak = "break-word"
        row.appendChild(value)
      }

      root.appendChild(row)
    }
  }

  return root
}
