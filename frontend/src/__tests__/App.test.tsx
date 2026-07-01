import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { renderRoute } from "./testkit"
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
  const renderSidebar = () => renderRoute(<Sidebar />, { initialEntries: ["/"] })

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
    expect(screen.getByText("Orphan DNS")).toBeInTheDocument()
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })

  it("navigation links have correct hrefs", () => {
    renderSidebar()
    expect(screen.getByText("Dashboard").closest("a")).toHaveAttribute("href", "/")
    expect(screen.getByText("Services").closest("a")).toHaveAttribute("href", "/services")
    expect(screen.getByText("Discover").closest("a")).toHaveAttribute("href", "/discover")
    expect(screen.getByText("Events").closest("a")).toHaveAttribute("href", "/events")
    expect(screen.getByText("Orphan DNS").closest("a")).toHaveAttribute("href", "/orphan-dns")
    expect(screen.getByText("Settings").closest("a")).toHaveAttribute("href", "/settings")
  })
})

describe("App setup flow", () => {
  it("sends /login to setup before the admin account exists", async () => {
    window.history.replaceState({}, "", "/login")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/auth/status")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ setup_complete: false, authenticated: false }),
          })
        }
        if (url.includes("/api/auth/setup-progress")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () =>
              Promise.resolve({
                user_exists: false,
                base_domain_set: false,
                cloudflare_configured: false,
                acme_email_set: false,
                tailscale_configured: false,
                docker_configured: false,
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
      expect(screen.getByText("Step 1 of 6: Account")).toBeInTheDocument()
    })
    expect(window.location.pathname).toBe("/setup")
    expect(screen.queryByText("Sign in to continue.")).not.toBeInTheDocument()
  })

  it("allows login while setup is incomplete after the admin account exists", async () => {
    window.history.replaceState({}, "", "/login")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/auth/status")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ setup_complete: false, authenticated: false }),
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
                cloudflare_configured: false,
                acme_email_set: false,
                tailscale_configured: false,
                docker_configured: false,
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
      expect(screen.getByText("Sign in to continue.")).toBeInTheDocument()
    })
    expect(window.location.pathname).toBe("/login")
    expect(screen.queryByText(/Step \d of 6:/)).not.toBeInTheDocument()
  })

  it("shows startup error instead of assuming setup is complete when auth status fails", async () => {
    window.history.replaceState({}, "", "/")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/auth/status")) {
          return Promise.resolve({
            ok: false,
            status: 500,
            json: () => Promise.resolve({ detail: "database unavailable" }),
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
      expect(screen.getByText("Startup error")).toBeInTheDocument()
    })
    expect(screen.getByText("database unavailable")).toBeInTheDocument()
    expect(window.location.pathname).toBe("/")
    expect(screen.queryByText("Sign in to continue.")).not.toBeInTheDocument()
  })

  it("shows startup error when setup progress fails", async () => {
    window.history.replaceState({}, "", "/login")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/auth/status")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ setup_complete: false, authenticated: false }),
          })
        }
        if (url.includes("/api/auth/setup-progress")) {
          return Promise.resolve({
            ok: false,
            status: 503,
            json: () => Promise.resolve({ detail: "setup progress unavailable" }),
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
      expect(screen.getByText("Startup error")).toBeInTheDocument()
    })
    expect(screen.getByText("setup progress unavailable")).toBeInTheDocument()
    expect(window.location.pathname).toBe("/login")
    expect(screen.queryByText("Step 1 of 6: Account")).not.toBeInTheDocument()
  })

  it("redirects unauthenticated setup visits to login after the admin account exists", async () => {
    window.history.replaceState({}, "", "/setup")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/auth/status")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ setup_complete: false, authenticated: false }),
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
                cloudflare_configured: false,
                acme_email_set: false,
                tailscale_configured: false,
                docker_configured: false,
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
      expect(screen.getByText("Sign in to continue.")).toBeInTheDocument()
    })
    expect(window.location.pathname).toBe("/login")
    expect(screen.queryByText(/Step \d of 6:/)).not.toBeInTheDocument()
  })

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

  it("sends /setup to login once setup is complete and the user is unauthenticated", async () => {
    window.history.replaceState({}, "", "/setup")

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/auth/status")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ setup_complete: true, authenticated: false }),
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
      expect(screen.getByText("Sign in to continue.")).toBeInTheDocument()
    })
    expect(window.location.pathname).toBe("/login")
    expect(screen.queryByText(/Step \d of 6:/)).not.toBeInTheDocument()
  })
})

