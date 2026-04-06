import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { Sidebar } from "@/components/Sidebar"

// Mock fetch globally for pages that call the API on mount
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ services: [], total: 0, containers: [], general: { base_domain: "example.com" } }),
  }))
})

describe("Sidebar", () => {
  const renderSidebar = () =>
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar />
      </MemoryRouter>
    )

  it("renders the app name", () => {
    renderSidebar()
    expect(screen.getByText("tailBale")).toBeInTheDocument()
  })

  it("renders all navigation links", () => {
    renderSidebar()
    expect(screen.getByText("Dashboard")).toBeInTheDocument()
    expect(screen.getByText("Services")).toBeInTheDocument()
    expect(screen.getByText("Discover")).toBeInTheDocument()
    expect(screen.getByText("Events")).toBeInTheDocument()
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })

  it("navigation links have correct hrefs", () => {
    renderSidebar()
    expect(screen.getByText("Dashboard").closest("a")).toHaveAttribute("href", "/")
    expect(screen.getByText("Services").closest("a")).toHaveAttribute("href", "/services")
    expect(screen.getByText("Discover").closest("a")).toHaveAttribute("href", "/discover")
    expect(screen.getByText("Events").closest("a")).toHaveAttribute("href", "/events")
    expect(screen.getByText("Settings").closest("a")).toHaveAttribute("href", "/settings")
  })
})

