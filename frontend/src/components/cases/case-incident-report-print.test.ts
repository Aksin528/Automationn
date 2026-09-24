import { renderSimpleMarkdown } from "@/components/cases/case-incident-report-print"

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
