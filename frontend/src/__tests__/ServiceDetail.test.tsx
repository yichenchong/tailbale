import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"

const mockService = {
  id: "svc_abc123",
  name: "Nextcloud",
  enabled: true,
  upstream_container_id: "c123",
  upstream_container_name: "nextcloud",
  upstream_scheme: "http",
  upstream_port: 80,
  healthcheck_path: "/status.php",
  hostname: "nextcloud.example.com",
  base_domain: "example.com",
  edge_container_name: "edge_nextcloud",
  network_name: "edge_net_nextcloud",
  ts_hostname: "edge-nextcloud",
  preserve_host_header: true,
  custom_caddy_snippet: null,
  app_profile: "nextcloud",
  status: {
    phase: "pending",
    message: "Awaiting first reconciliation",
    tailscale_ip: null,
    edge_container_id: null,
    last_reconciled_at: null,
    health_checks: {
      upstream_container_present: true,
      edge_container_running: false,
      cert_present: true,
    },
    cert_expires_at: "2026-08-01T00:00:00",
  },
  created_at: "2026-04-05T00:00:00",
  updated_at: "2026-04-05T00:00:00",
}

beforeEach(() => {
  vi.restoreAllMocks()
})

function renderWithRoute(path: string) {
  return import("@/pages/ServiceDetail").then(({ default: ServiceDetail }) => {
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetail />} />
        </Routes>
      </MemoryRouter>
    )
  })
}

describe("ServiceDetail page", () => {
  it("shows loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    await renderWithRoute("/services/svc_abc123")
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("renders service details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    expect(screen.getAllByText("nextcloud.example.com").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText("pending").length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText("Enabled")).toBeInTheDocument()
  })

  it("shows configuration section", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Configuration")).toBeInTheDocument()
    })
    expect(screen.getByText("http://nextcloud:80")).toBeInTheDocument()
    expect(screen.getByText("/status.php")).toBeInTheDocument()
  })

  it("shows runtime section", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Runtime")).toBeInTheDocument()
    })
    expect(screen.getByText("edge_nextcloud")).toBeInTheDocument()
    expect(screen.getByText("edge_net_nextcloud")).toBeInTheDocument()
    expect(screen.getByText("edge-nextcloud")).toBeInTheDocument()
  })

  it("shows health checks section with indicators", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Health Checks")).toBeInTheDocument()
    })
    expect(screen.getByText("Upstream Container")).toBeInTheDocument()
    expect(screen.getByText("Edge Running")).toBeInTheDocument()
    expect(screen.getByText("Certificate")).toBeInTheDocument()
  })

  it("shows placeholder when no health checks", async () => {
    const svc = { ...mockService, status: { ...mockService.status, health_checks: null } }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(svc),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("No health checks available yet.")).toBeInTheDocument()
    })
  })

  it("shows action buttons", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Disable")).toBeInTheDocument()
    })
    expect(screen.getByText("Delete")).toBeInTheDocument()
    expect(screen.getByText("Edit")).toBeInTheDocument()
    expect(screen.getByText("Reload Caddy")).toBeInTheDocument()
    expect(screen.getByText("Restart Edge")).toBeInTheDocument()
    expect(screen.getByText("Recreate Edge")).toBeInTheDocument()
    expect(screen.getByText("Force Renew Cert")).toBeInTheDocument()
    expect(screen.getByText("Re-run Reconcile")).toBeInTheDocument()
  })

  it("hides edge mutation actions when disabled", async () => {
    const disabledService = {
      ...mockService,
      enabled: false,
      status: { ...mockService.status, phase: "disabled" },
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(disabledService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Enable")).toBeInTheDocument()
    })

    expect(screen.queryByText("Reload Caddy")).not.toBeInTheDocument()
    expect(screen.queryByText("Restart Edge")).not.toBeInTheDocument()
    expect(screen.queryByText("Recreate Edge")).not.toBeInTheDocument()
    expect(screen.queryByText("Update Edge")).not.toBeInTheDocument()
    expect(screen.getByText("Force Renew Cert")).toBeInTheDocument()
    expect(screen.getByText("Re-run Reconcile")).toBeInTheDocument()
  })

  it("encodes service ids when requesting detail endpoints", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc%20abc")
    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    expect(fetchMock).toHaveBeenCalledWith("/api/services/svc%20abc", expect.anything())
    expect(fetchMock).toHaveBeenCalledWith("/api/services/svc%20abc/edge-version", expect.anything())
  })

  it("prevents edit saves that violate backend constraints", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Edit")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Edit"))
    const save = screen.getByText("Save")
    const port = screen.getByLabelText("Upstream Port")
    const name = screen.getByLabelText("Name")

    fireEvent.change(port, { target: { value: "70000" } })
    expect(save).toBeDisabled()

    fireEvent.change(port, { target: { value: "443" } })
    expect(save).toBeEnabled()

    fireEvent.change(name, { target: { value: "   " } })
    expect(save).toBeDisabled()
  })

  it("sends trimmed valid edit values with numeric port", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ...mockService, name: "Cloud", upstream_port: 443 }),
        })
      }
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockService),
      })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Edit")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Edit"))
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  Cloud  " } })
    fireEvent.change(screen.getByLabelText("Upstream Port"), { target: { value: "443" } })
    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/services/svc_abc123", expect.objectContaining({ method: "PUT" }))
    })
    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")
    expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({
      name: "Cloud",
      upstream_port: 443,
    })
  })

  it("ignores stale detail refreshes after navigating to another service", async () => {
    const oldService = { ...mockService, id: "svc_old", name: "Oldcloud" }
    const newService = { ...mockService, id: "svc_new", name: "Newcloud" }
    let resolveOld!: (value: { ok: boolean; json: () => Promise<typeof oldService> }) => void
    const edgeVersion = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/services/svc_old") {
        return new Promise((resolve) => { resolveOld = resolve })
      }
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVersion) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(newService) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ServiceDetail } = await import("@/pages/ServiceDetail")
    render(
      <MemoryRouter initialEntries={["/services/svc_old"]}>
        <Link to="/services/svc_new">Go new</Link>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetail />} />
        </Routes>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByText("Go new"))
    await waitFor(() => {
      expect(screen.getByText("Newcloud")).toBeInTheDocument()
    })

    resolveOld({ ok: true, json: () => Promise.resolve(oldService) })
    await Promise.resolve()
    await Promise.resolve()
    expect(screen.queryByText("Oldcloud")).not.toBeInTheDocument()
    expect(screen.getByText("Newcloud")).toBeInTheDocument()
  })

  it("closes confirmation dialogs when navigating between services", async () => {
    const newService = { ...mockService, id: "svc_new", name: "Newcloud" }
    const edgeVersion = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVersion) })
      }
      const service = url === "/api/services/svc_new" ? newService : mockService
      return Promise.resolve({ ok: true, json: () => Promise.resolve(service) })
    }))

    const { default: ServiceDetail } = await import("@/pages/ServiceDetail")
    render(
      <MemoryRouter initialEntries={["/services/svc_abc123"]}>
        <Link to="/services/svc_new">Go new</Link>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetail />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText("Delete"))
    expect(screen.getByText('Delete "Nextcloud"?')).toBeInTheDocument()

    fireEvent.click(screen.getByText("Go new"))
    await waitFor(() => {
      expect(screen.getByText("Newcloud")).toBeInTheDocument()
    })
    expect(screen.queryByText('Delete "Nextcloud"?')).not.toBeInTheDocument()
    expect(screen.queryByText('Delete "Newcloud"?')).not.toBeInTheDocument()
  })

  it("shows logs tabs placeholder", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Edge Logs")).toBeInTheDocument()
    })
    expect(screen.getByText("Events")).toBeInTheDocument()
    expect(screen.getByText("Edge container logs will appear here once the reconciler is running.")).toBeInTheDocument()
  })

  it("shows error for nonexistent service", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "Service not found" }),
    }))
    await renderWithRoute("/services/svc_nonexistent")
    await waitFor(() => {
      expect(screen.getByText("Service not found")).toBeInTheDocument()
    })
  })

  it("shows back button", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Back to Services")).toBeInTheDocument()
    })
  })
})
