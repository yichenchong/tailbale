import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"

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
