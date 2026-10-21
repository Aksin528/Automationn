"""Tests for how the incident report PDF renderer shows date/time fields."""

import pytest

from tracecat.cases.incident_report_pdf import (
    _build_sections,
    _format_report_datetime,
    render_incident_report_pdf,
)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("2026-10-15T14:32", "15.10.2026 14:32"),
        ("2026-10-15", "15.10.2026"),
        ("  2026-10-15T09:05  ", "15.10.2026 09:05"),
        ("", ""),
        (None, ""),
        ("some free text", "some free text"),
    ],
)
def test_format_report_datetime(stored: str | None, expected: str) -> None:
    assert _format_report_datetime(stored) == expected


def test_date_time_fields_are_formatted_in_sections() -> None:
    sections = _build_sections(
        {
            "detectedAt": "2026-10-15T14:32",
            "firstResponseAt": "2026-10-15",
            "closedAt": "",
        }
    )
    values = {
        field.label: field.value
        for section in sections
        for field in section.fields
        if field.kind == "text"
    }

    assert values["Aşkarlanma tarixi"] == "15.10.2026 14:32"
    # An old date-only value is shown as a date, without an invented time.
    assert values["İlk cavabın verilmə tarixi"] == "15.10.2026"
    assert values["Bağlanma tarixi"] == "—"


def test_render_pdf_accepts_date_time_values() -> None:
    pdf = render_incident_report_pdf(
        {
            "reporterName": "Ana",
            "detectedAt": "2026-10-15T14:32",
            "firstResponseAt": "2026-10-15T14:40",
            "closedAt": "2026-10-16T09:05",
        },
        "CASE-0001",
    )

    assert pdf.startswith(b"%PDF")
