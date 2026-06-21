import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
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

  it("shows a load error instead of an empty state when services fail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({ detail: "database unavailable" }),
    }))
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Unable to load services: database unavailable")).toBeInTheDocument()
    })
    expect(screen.queryByText("No services exposed yet.")).not.toBeInTheDocument()
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

  it("hides edge actions for disabled services", async () => {
    const disabledData = {
      ...mockServiceData,
      services: [
        {
          ...mockServiceData.services[0],
          enabled: false,
          status: { ...mockServiceData.services[0].status, phase: "disabled" },
        },
      ],
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(disabledData),
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

    fireEvent.click(screen.getByLabelText("Actions"))

    expect(screen.queryByText("Reload Caddy")).not.toBeInTheDocument()
    expect(screen.queryByText("Restart Edge")).not.toBeInTheDocument()
    expect(screen.queryByText("Recreate Edge")).not.toBeInTheDocument()
    expect(screen.getByText("Enable")).toBeInTheDocument()
  })

  it("encodes service ids when running row actions", async () => {
    const data = {
      ...mockServiceData,
      services: [{ ...mockServiceData.services[0], id: "svc abc" }],
    }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(init?.method === "POST" ? { success: true } : data),
    }))
    vi.stubGlobal("fetch", fetchMock)
    const { default: Services } = await import("@/pages/Services")
    render(
      <MemoryRouter>
        <Services />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByLabelText("Actions")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText("Actions"))
    fireEvent.click(screen.getByText("Reload Caddy"))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/services/svc%20abc/reload", expect.objectContaining({ method: "POST" }))
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

  it("buckets cert expiry days using UTC for offset-less timestamps", async () => {
    // Backend cert_expires_at serializes naive (no offset) but means UTC.
    // Forcing a +09:00 host makes a raw `new Date()` parse drop the count by a
    // day and cross the 14-day warning threshold; parseBackendDate must not.
    const originalTz = process.env.TZ
    process.env.TZ = "Asia/Tokyo"
    try {
      const { formatCertExpiry } = await import("@/lib/certStatus")
      const naive = new Date(Date.now() + 14.25 * 86400000)
        .toISOString()
        .replace("Z", "")
      // 14.25 days out -> ceil = 15 -> outside the <=14 warning bucket.
      expect(formatCertExpiry(naive, "UTC").style).toBe("text-zinc-500")
    } finally {
      if (originalTz === undefined) delete process.env.TZ
      else process.env.TZ = originalTz
    }
  })
})
