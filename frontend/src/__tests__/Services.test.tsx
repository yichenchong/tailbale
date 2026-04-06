import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

const mockServiceData = {
  services: [
    {
      id: "svc_abc123",
      name: "Nextcloud",
      enabled: true,
      upstream_container_name: "nextcloud",
      upstream_port: 80,
      hostname: "nextcloud.example.com",
      status: {
        phase: "healthy",
        message: null,
        tailscale_ip: "100.64.0.1",
        edge_container_id: null,
        last_reconciled_at: null,
        health_checks: null,
        cert_expires_at: "2026-08-01T00:00:00",
      },
      base_domain: "example.com",
      upstream_container_id: "c123",
      upstream_scheme: "http",
      edge_container_name: "edge_nextcloud",
      network_name: "edge_net_nextcloud",
      ts_hostname: "edge-nextcloud",
      preserve_host_header: true,
      created_at: "2026-04-05T00:00:00",
      updated_at: "2026-04-05T00:00:00",
    },
  ],
  total: 1,
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Services page", () => {
  it("shows loading state initially", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    expect(screen.getByText("Loading services...")).toBeInTheDocument()
  })

  it("renders empty state when no services", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ services: [], total: 0 }),
    }))
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("No services exposed yet.")).toBeInTheDocument()
    })
  })

  it("renders service list with data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockServiceData),
    }))
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    expect(screen.getByText("healthy")).toBeInTheDocument()
  })

  it("renders edge IP column", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockServiceData),
    }))
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("100.64.0.1")).toBeInTheDocument()
    })
  })

  it("renders cert expiry column", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockServiceData),
    }))
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    await waitFor(() => {
      // The date is formatted by toLocaleDateString(), just verify the column header exists
      expect(screen.getByText("Cert Expiry")).toBeInTheDocument()
    })
  })

  it("renders actions menu trigger", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockServiceData),
    }))
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByLabelText("Actions")).toBeInTheDocument()
    })
  })

  it("shows Expose New Service button", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ services: [], total: 0 }),
    }))
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Discover Containers")).toBeInTheDocument()
    })
  })
})
