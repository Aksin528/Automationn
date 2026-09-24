"""Server-side renderer for the case incident-report PDF.

Mirrors `frontend/src/components/cases/case-incident-report-print.ts`'s
`buildPrintSections`/`buildPrintElement` (section order, labels, and field
kinds) so the stored PDF matches what the "Report" dialog shows on screen.
Rendering here, instead of client-side via html2canvas/jsPDF, is deliberate:
a fully-filled report rasterizing dozens of blocks in the browser blocked the
main thread for up to a minute on save (see PR discussion). reportlab lays
out real PDF text (paginated automatically by `SimpleDocTemplate`), which
costs the browser nothing and takes a fraction of a second server-side.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any, Literal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

# reportlab's built-in "Helvetica"/"Helvetica-Bold" are the base-14 PDF fonts,
# which only cover WinAnsi/Latin-1 -- they silently drop Azerbaijani letters
# (ə, ş, ç, ğ, ı, ö, ü) as missing glyphs. DejaVu Sans has full Latin
# Extended-A coverage, so it's bundled here (`fonts/`) and registered once at
# import time rather than relying on the container having system fonts
# installed. Mirrors why the old client-side renderer went through the
# browser's own DOM/font stack instead of jsPDF's built-in fonts -- see
# `case-incident-report-print.ts`'s `buildPrintElement` docstring.
_FONTS_DIR = Path(__file__).parent / "fonts"
FONT_REGULAR = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
if FONT_REGULAR not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(_FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(_FONTS_DIR / "DejaVuSans-Bold.ttf")))


def _fallback_dash(value: str | None) -> str:
    value = (value or "").strip()
    return value if value else "—"


def _bold_inline(text: str) -> str:
    """Escapes `text` for reportlab's mini-XML, then turns `**x**` into `<b>x</b>`.

    Mirrors `appendInlineBold` in the frontend's markdown renderer -- the
    only inline markup either side supports.
    """
    parts = text.split("**")
    out: list[str] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        safe = escape(part)
        out.append(f"<b>{safe}</b>" if i % 2 == 1 else safe)
    return "".join(out)


_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")


def _render_markdown(text: str, styles: dict[str, ParagraphStyle]) -> list[Any]:
    """Minimal markdown-to-flowables renderer for the description field.

    Mirrors `renderSimpleMarkdown`: `#`..`####` headings, `**bold**`, `-`/`*`
    bullet lists, and plain paragraphs -- not full CommonMark, matching the
    same deliberately narrow subset the frontend renderer covers. Bullets
    are plain "•"-prefixed paragraphs, not `ListFlowable`/`ListItem` --
    that combination hits a real reportlab bug (`IndexError` inside
    `ListFlowable.getSpaceBefore`, confirmed against reportlab 5.0.1) --
    so this sidesteps it entirely rather than working around it.
    """
    text = text.strip()
    if not text:
        return [Paragraph("—", styles["value"])]

    flowables: list[Any] = []

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        heading_match = _HEADING_RE.match(line)
        bullet_match = _BULLET_RE.match(line)

        if heading_match:
            hashes, heading_text = heading_match.groups()
            size = 11 if len(hashes) <= 2 else 10
            flowables.append(
                Paragraph(
                    _bold_inline(heading_text),
                    ParagraphStyle(
                        "md_heading",
                        parent=styles["value"],
                        fontSize=size,
                        leading=size + 3,
                        spaceBefore=6,
                        spaceAfter=3,
                        fontName=FONT_BOLD,
                    ),
                )
            )
            continue

        if bullet_match:
            flowables.append(
                Paragraph(
                    f"•  {_bold_inline(bullet_match.group(1))}",
                    styles["bullet"],
                )
            )
            continue

        if not line.strip():
            flowables.append(Spacer(1, 4 * mm / 6))
            continue

        flowables.append(Paragraph(_bold_inline(line), styles["value"]))

    return flowables


FieldKind = Literal["text", "list", "markdown"]


class PrintField:
    __slots__ = ("label", "kind", "value", "items")

    def __init__(
        self,
        label: str,
        kind: FieldKind,
        value: str = "",
        items: list[str] | None = None,
    ) -> None:
        self.label = label
        self.kind = kind
        self.value = value
        self.items = items or []


class PrintSection:
    __slots__ = ("heading", "fields")

    def __init__(self, heading: str, fields: list[PrintField]) -> None:
        self.heading = heading
        self.fields = fields


def _text(label: str, report: dict[str, Any], key: str) -> PrintField:
    return PrintField(label, "text", value=_fallback_dash(report.get(key)))


def _list(label: str, report: dict[str, Any], key: str) -> PrintField:
    items = report.get(key)
    return PrintField(
        label, "list", items=list(items) if isinstance(items, list) else []
    )


def _build_sections(report: dict[str, Any]) -> list[PrintSection]:
    """Mirrors `buildPrintSections` in `case-incident-report-print.ts` exactly
    -- same sections, same labels, same field order."""
    return [
        PrintSection(
            "Kiberinsidentin aşkarlanması",
            [
                _text("Ad", report, "reporterName"),
                _text("Soyad", report, "reporterSurname"),
                _text("Vəzifə", report, "reporterPosition"),
                _text("Telefon", report, "reporterPhone"),
                _text("İş ünvanı", report, "reporterWorkAddress"),
                _text("Aşkarlandığı sistem və ya tətbiq", report, "detectedSystem"),
                _text("Aşkarlanma tarixi", report, "detectedAt"),
                _text("İlk cavabın verilmə tarixi", report, "firstResponseAt"),
                _text("Bağlanma tarixi", report, "closedAt"),
            ],
        ),
        PrintSection(
            "Kiberinsidentin xülasəsi",
            [
                _list("Aşkarlanan kiberinsidentin növü", report, "incidentTypes"),
                PrintField(
                    "İnsidentin təsviri (başvermə səbəbi təsvir edilməklə)",
                    "markdown",
                    value=report.get("description") or "",
                ),
                _text("Cəlb olunan əməkdaşların adları", report, "involvedEmployees"),
            ],
        ),
        PrintSection(
            "Məlumatın təqdim edildiyi struktur bölmə və ya təşkilat",
            [_list("Bildiriş edilib", report, "notifiedTo")],
        ),
        PrintSection(
            "Görülmüş fəaliyyətlər (başlama və bitmə tarixləri ilə)",
            [
                _text("Aşkarlanma metodları", report, "detectionMethods"),
                _text("Qarşısının alınması metodları", report, "preventionMethods"),
                _text("Toplanılmış sübutlar", report, "collectedEvidence"),
                _text("Aradan qaldırılması metodları", report, "remediationMethods"),
                _text("Bərpa metodları", report, "recoveryMethods"),
            ],
        ),
        PrintSection(
            "Qiymətləndirmə",
            [
                _text(
                    "İşçi qrupu cavab tədbirlərinin görülməsində nə dərəcədə effektiv idilər?",
                    report,
                    "teamEffectiveness",
                ),
                _text(
                    "Sənədləşdirilmiş prosedurlara əməl edildi?",
                    report,
                    "proceduresFollowed",
                ),
                _text(
                    "Qısa zaman ərzində hansı informasiyaların əldə edilməsi zərurəti yaranmışdı?",
                    report,
                    "infoNeededQuickly",
                ),
                _text(
                    "Bərpaya mane olacaq və ya gecikdirəcək hər hansı addım və ya fəaliyyət izlənildi?",
                    report,
                    "recoveryBlockers",
                ),
                _text(
                    "İşçi qrupu növbəti dəfə kiberinsident baş verərkən nəyi fərqli edə bilərlər?",
                    report,
                    "whatToImproveNextTime",
                ),
                _text(
                    "Gələcəkdə kiberinsidentin baş verməməsi üçün hansı tədbirlər və fəaliyyətlər yerinə yetirildi?",
                    report,
                    "futureActionsTaken",
                ),
                _text(
                    "Hansı bərpaedici və ya qabaqlayıcı tədbirlər kiberinsidentin gələcəkdə "
                    "başvermə ehtimalını azalda bilər?",
                    report,
                    "futurePreventiveMeasures",
                ),
                _text(
                    "Gələcəkdə aşkarlanma, analiz və aradan qaldırılmasının asanlaşdırılması üçün "
                    "hansı əlavə resurslara ehtiyac duyulur?",
                    report,
                    "additionalResourcesNeeded",
                ),
                _text("Digər nəticələr və tövsiyələr", report, "otherRecommendations"),
            ],
        ),
        PrintSection(
            "Monitorinq",
            [
                _text(
                    "Nəzərdən keçirildi (Kibertəhlükəsizlik Mütəxəssisi / İdarəsi / Digər)",
                    report,
                    "reviewedBy",
                ),
                _text("Nəzərdən keçirən şəxs", report, "reviewer"),
                _text(
                    "Həyata keçirilməsi tövsiyə olunan tələblər",
                    report,
                    "reviewedRequirements",
                ),
            ],
        ),
    ]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "report_title",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=18,
            leading=22,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "report_subtitle",
            parent=base["Normal"],
            fontName=FONT_REGULAR,
            fontSize=11,
            textColor=colors.HexColor("#666666"),
            spaceAfter=14,
        ),
        "heading": ParagraphStyle(
            "section_heading",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=13,
            leading=16,
            textColor=colors.HexColor("#111111"),
            backColor=colors.HexColor("#f1f1f1"),
            borderPadding=6,
            spaceBefore=10,
            spaceAfter=8,
        ),
        "label": ParagraphStyle(
            "field_label",
            parent=base["Normal"],
            fontName=FONT_REGULAR,
            fontSize=9.5,
            leading=12,
            textColor=colors.HexColor("#666666"),
            spaceAfter=1,
        ),
        "value": ParagraphStyle(
            "field_value",
            parent=base["Normal"],
            fontName=FONT_REGULAR,
            fontSize=11,
            leading=15,
            spaceAfter=10,
        ),
        "bullet": ParagraphStyle(
            "field_bullet",
            parent=base["Normal"],
            fontName=FONT_REGULAR,
            fontSize=11,
            leading=15,
            leftIndent=12,
            spaceAfter=2,
        ),
    }


def render_incident_report_pdf(report: dict[str, Any], case_short_id: str) -> bytes:
    """Renders `Case.payload["incident_report"]` to PDF bytes."""
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"Kiberinsidentə dair Hesabat — {case_short_id}",
    )

    story: list[Any] = [
        Paragraph("Kiberinsidentə dair Hesabat", styles["title"]),
        Paragraph(escape(case_short_id), styles["subtitle"]),
    ]

    for section in _build_sections(report):
        story.append(Paragraph(escape(section.heading), styles["heading"]))
        for field in section.fields:
            if field.kind == "markdown":
                story.append(Paragraph(escape(field.label), styles["label"]))
                story.extend(_render_markdown(field.value, styles))
                story.append(Spacer(1, 6))
                continue

            story.append(Paragraph(escape(field.label), styles["label"]))
            if field.kind == "list":
                if not field.items:
                    story.append(Paragraph("—", styles["value"]))
                else:
                    for item in field.items:
                        story.append(Paragraph(f"•  {escape(item)}", styles["bullet"]))
                    story.append(Spacer(1, 6))
            else:
                # whitespace-preserving line breaks match the frontend's
                # `white-space: pre-wrap` on the value div.
                value_html = escape(field.value).replace("\n", "<br/>")
                story.append(Paragraph(value_html, styles["value"]))

    doc.build(story)
    return buffer.getvalue()
