import {
  formatReportDateTime,
  normalizeReportDateTime,
  renderSimpleMarkdown,
  toBakuDateTimeLocal,
} from "@/components/cases/case-incident-report-print"

describe("report date/time helpers", () => {
  it("converts an instant to Baku wall-clock time (UTC+4)", () => {
    expect(toBakuDateTimeLocal("2026-10-15T21:30:00Z")).toBe("2026-10-16T01:30")
    expect(toBakuDateTimeLocal("2026-10-15T08:05:00+00:00")).toBe(
      "2026-10-15T12:05"
    )
  })

  it("never renders midnight as 24:00", () => {
    expect(toBakuDateTimeLocal("2026-10-15T20:00:00Z")).toBe("2026-10-16T00:00")
  })

  it("returns an empty string for empty or unparsable input", () => {
    expect(toBakuDateTimeLocal("")).toBe("")
    expect(toBakuDateTimeLocal(null)).toBe("")
    expect(toBakuDateTimeLocal("not a date")).toBe("")
  })

  it("keeps a datetime-local value and turns an old date into midnight", () => {
    expect(normalizeReportDateTime("2026-10-15T14:32")).toBe("2026-10-15T14:32")
    expect(normalizeReportDateTime("2026-10-15")).toBe("2026-10-15T00:00")
    expect(normalizeReportDateTime("")).toBe("")
    expect(normalizeReportDateTime(undefined)).toBe("")
  })

  it("formats stored values for display without inventing a time", () => {
    expect(formatReportDateTime("2026-10-15T14:32")).toBe("15.10.2026 14:32")
    expect(formatReportDateTime("2026-10-15")).toBe("15.10.2026")
    expect(formatReportDateTime("")).toBe("")
    expect(formatReportDateTime("some text")).toBe("some text")
  })
})

describe("renderSimpleMarkdown", () => {
  it("renders an empty/blank value as a dash", () => {
    const container = document.createElement("div")
    renderSimpleMarkdown(container, "   \n  ")
    expect(container.textContent).toBe("—")
  })

  it("renders ## and ### headings as bold, non-literal text", () => {
    const container = document.createElement("div")
    renderSimpleMarkdown(container, "## Check Point Mail Security\n### General")

    const headings = Array.from(
      container.querySelectorAll<HTMLDivElement>("div")
    ).filter((el) => el.style.fontWeight === "700")
    expect(headings).toHaveLength(2)
    expect(headings[0].textContent).toBe("Check Point Mail Security")
    expect(headings[1].textContent).toBe("General")
    // The raw "##"/"###" markers must not leak into the rendered text.
    expect(container.textContent).not.toContain("#")
  })

  it("renders **bold** spans as real <strong> elements, not literal asterisks", () => {
    const container = document.createElement("div")
    renderSimpleMarkdown(container, "**Blade:** Threat Extraction")

    const strong = container.querySelector("strong")
    expect(strong).not.toBeNull()
    expect(strong?.textContent).toBe("Blade:")
    expect(container.textContent).toBe("Blade: Threat Extraction")
    expect(container.textContent).not.toContain("**")
  })

  it("groups consecutive '- ' lines into one <ul>, not one per line", () => {
    const container = document.createElement("div")
    renderSimpleMarkdown(
      container,
      "- Ransomware yoluxmaları\n- Şəbəkəyə müdaxilə\n- Fişinq"
    )

    const lists = container.querySelectorAll("ul")
    expect(lists).toHaveLength(1)
    expect(lists[0].querySelectorAll("li")).toHaveLength(3)
    expect(lists[0].querySelectorAll("li")[1].textContent).toBe(
      "Şəbəkəyə müdaxilə"
    )
  })

  it("starts a new <ul> when a bullet run is interrupted by a paragraph", () => {
    const container = document.createElement("div")
    renderSimpleMarkdown(container, "- one\n- two\nsome text\n- three")

    const lists = container.querySelectorAll("ul")
    expect(lists).toHaveLength(2)
    expect(lists[0].querySelectorAll("li")).toHaveLength(2)
    expect(lists[1].querySelectorAll("li")).toHaveLength(1)
  })

  it("renders a real Cortex-style description without literal markdown syntax", () => {
    const container = document.createElement("div")
    renderSimpleMarkdown(
      container,
      "## Cortex XDR Case\n\n### General\n\n**Severity:** medium\n**Status:** Resolved"
    )

    expect(container.textContent).not.toMatch(/[#]/)
    expect(container.textContent).not.toContain("**")
    expect(container.querySelectorAll("strong")).toHaveLength(2)
  })
})
