import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

const mockSettings = {
  general: {
    base_domain: "example.com",
    acme_email: "admin@example.com",
    reconcile_interval_seconds: 60,
    cert_renewal_window_days: 30,
  },
  cloudflare: { zone_id: "zone123", token_configured: true },
  tailscale: {
    auth_key_configured: true,
    control_url: "https://controlplane.tailscale.com",
    default_ts_hostname_prefix: "edge",
  },
  docker: { socket_path: "unix:///var/run/docker.sock" },
  paths: {
    generated_root: "data/generated",
    cert_root: "data/certs",
    tailscale_state_root: "data/tailscale",
  },
  setup_complete: true,
}

beforeEach(() => {
  vi.restoreAllMocks()
})

function mockFetch(data: unknown = mockSettings) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(data),
  })
}

describe("SettingsPage", () => {
  it("shows loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    expect(screen.getByText("Loading settings...")).toBeInTheDocument()
  })

  it("renders page title and tabs", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })
    expect(screen.getByText("General")).toBeInTheDocument()
    expect(screen.getByText("Cloudflare")).toBeInTheDocument()
    expect(screen.getByText("Tailscale")).toBeInTheDocument()
    expect(screen.getByText("Docker")).toBeInTheDocument()
    expect(screen.getByText("Paths")).toBeInTheDocument()
  })

  it("shows General tab fields by default", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Base Domain")).toBeInTheDocument()
    })
    expect(screen.getByText("ACME Email")).toBeInTheDocument()
    expect(screen.getByText("Reconcile Interval (seconds)")).toBeInTheDocument()
    expect(screen.getByText("Cert Renewal Window (days)")).toBeInTheDocument()
    expect(screen.getByDisplayValue("example.com")).toBeInTheDocument()
    expect(screen.getByDisplayValue("admin@example.com")).toBeInTheDocument()
  })

  it("has Save button on General tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Save")).toBeInTheDocument()
    })
  })

  it("switches to Cloudflare tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Cloudflare"))
    expect(screen.getByText("Zone ID")).toBeInTheDocument()
    expect(screen.getByText("API Token")).toBeInTheDocument()
    expect(screen.getByDisplayValue("zone123")).toBeInTheDocument()
    expect(screen.getByText("Configured")).toBeInTheDocument()
    expect(screen.getByText("Test Connection")).toBeInTheDocument()
  })

  it("switches to Tailscale tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Tailscale"))
    expect(screen.getByText("Auth Key")).toBeInTheDocument()
    expect(screen.getByText("Control URL")).toBeInTheDocument()
    expect(screen.getByText("Default TS Hostname Prefix")).toBeInTheDocument()
    expect(screen.getByText("Configured")).toBeInTheDocument()
    expect(screen.getByText("Validate Key")).toBeInTheDocument()
  })

  it("switches to Docker tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Docker"))
    expect(screen.getByText("Docker Socket Path")).toBeInTheDocument()
    expect(
      screen.getByDisplayValue("unix:///var/run/docker.sock")
    ).toBeInTheDocument()
    expect(screen.getByText("Test Connection")).toBeInTheDocument()
  })

  it("switches to Paths tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Paths"))
    expect(screen.getByText("Generated Config Root")).toBeInTheDocument()
    expect(screen.getByText("Certificate Root")).toBeInTheDocument()
    expect(screen.getByText("Tailscale State Root")).toBeInTheDocument()
    expect(screen.getByDisplayValue("data/generated")).toBeInTheDocument()
    expect(screen.getByDisplayValue("data/certs")).toBeInTheDocument()
    expect(screen.getByDisplayValue("data/tailscale")).toBeInTheDocument()
  })

  it("shows Not set when secrets not configured", async () => {
    const noSecrets = {
      ...mockSettings,
      cloudflare: { zone_id: "", token_configured: false },
      tailscale: {
        ...mockSettings.tailscale,
        auth_key_configured: false,
      },
    }
    vi.stubGlobal("fetch", mockFetch(noSecrets))
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Cloudflare"))
    expect(screen.getByText("Not set")).toBeInTheDocument()
  })

  it("calls save on General tab Save click", async () => {
    const fetchMock = mockFetch()
    vi.stubGlobal("fetch", fetchMock)
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Save")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => {
      // Should have called fetch for initial load + save
      const calls = fetchMock.mock.calls
      const putCall = calls.find(
        (c: unknown[]) =>
          typeof c[1] === "object" &&
          (c[1] as RequestInit).method === "PUT"
      )
      expect(putCall).toBeDefined()
      expect(String(putCall![0])).toContain("/settings/general")
    })
  })

  it("shows test result on connection test", async () => {
    let callCount = 0
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        callCount++
        if (callCount === 1) {
          // Initial settings load
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockSettings),
          })
        }
        // Test connection result
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({ success: true, message: "Docker is reachable" }),
        })
      })
    )
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Docker"))
    fireEvent.click(screen.getByText("Test Connection"))

    await waitFor(() => {
      expect(screen.getByText("Docker is reachable")).toBeInTheDocument()
    })
  })
})
