import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { PageLoading, PageError } from "@/components/PageState"

describe("PageLoading", () => {
  it("announces its content through a polite live region (role=status)", () => {
    // The shared page-load spinner MUST be a live region so assistive tech is
    // told the page is loading (WCAG 4.1.3), matching every other async state in
    // the app (settings Save/Test, stale-data banners, DeveloperTab logs).
    render(<PageLoading>Loading dashboard...</PageLoading>)
    const status = screen.getByRole("status")
    expect(status).toHaveTextContent("Loading dashboard...")
  })
})

describe("PageError", () => {
  it("announces its content assertively (role=alert)", () => {
    render(<PageError>Something broke</PageError>)
    const alert = screen.getByRole("alert")
    expect(alert).toHaveTextContent("Something broke")
  })
})
