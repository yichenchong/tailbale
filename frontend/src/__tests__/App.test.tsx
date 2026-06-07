import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import App from "@/App"
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

describe("App setup flow", () => {
  it("redirects to the dashboard after completing the final setup step", async () => {
    window.history.replaceState({}, "", "/setup")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/auth/status")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ setup_complete: false, authenticated: true }),
          })
        }
        if (url.includes("/api/auth/setup-progress")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                user_exists: true,
                base_domain_set: true,
                cloudflare_configured: true,
                acme_email_set: true,
                tailscale_configured: true,
                docker_configured: false,
              }),
          })
        }
        if (url.includes("/api/settings/test/docker")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ success: true, message: "Docker connected" }),
          })
        }
        if (url.includes("/api/settings/docker")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ socket_path: "unix:///var/run/docker.sock" }),
          })
        }
        if (url.includes("/api/settings/setup-complete")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ setup_complete: true }),
          })
        }
        if (url.includes("/api/dashboard/summary")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                services: { total: 0, healthy: 0, warning: 0, error: 0 },
                expiring_certs: [],
                recent_errors: [],
                recent_events: [],
              }),
          })
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({}),
        })
      })
    )

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Complete Setup").closest("button")!)

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument()
    })
    expect(window.location.pathname).toBe("/")
  })
})

