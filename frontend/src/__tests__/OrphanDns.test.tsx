import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

const mockJobs = {
  jobs: [
    {
      id: "job_abc123",
      service_id: null,
      kind: "dns_orphan_cleanup",
      status: "pending",
      progress: 0,
      message: "Orphaned DNS record for deleted service 'Nextcloud'",
      details: {
        record_id: "cf_r1",
        hostname: "nextcloud.example.com",
        zone_id: "zone1",
        value: "100.64.0.1",
        service_name: "Nextcloud",
      },
      created_at: "2026-04-08T14:30:00Z",
      updated_at: "2026-04-08T14:30:00Z",
    },
    {
      id: "job_def456",
      service_id: null,
      kind: "dns_orphan_cleanup",
      status: "failed",
      progress: 0,
      message: "Retry failed: API timeout",
      details: {
        record_id: "cf_r2",
        hostname: "vaultwarden.example.com",
        zone_id: "zone1",
        value: "100.64.0.2",
        service_name: "Vaultwarden",
      },
      created_at: "2026-04-07T10:00:00Z",
      updated_at: "2026-04-08T09:00:00Z",
    },
  ],
  total: 2,
}

const mockSettings = {
  general: { base_domain: "example.com", acme_email: "a@b.com", reconcile_interval_seconds: 60, cert_renewal_window_days: 30, timezone: "UTC" },
  cloudflare: { zone_id: "", token_configured: false },
  tailscale: { auth_key_configured: false, api_key_configured: false, control_url: "", default_ts_hostname_prefix: "edge" },
  docker: { socket_path: "" },
  paths: { generated_root: "", cert_root: "", tailscale_state_root: "" },
  setup_complete: false,
}

beforeEach(() => {
  vi.restoreAllMocks()
})

function mockFetch(data: unknown) {
  return vi.fn().mockImplementation((url: string) => {
    if (String(url).includes("/settings")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(data),
    })
  })
}

describe("OrphanDns page", () => {
  it("shows loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("shows error state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: "Server error" }),
      })
    )
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Server error")).toBeInTheDocument()
    })
  })

  it("shows empty state when no orphan jobs", async () => {
    vi.stubGlobal("fetch", mockFetch({ jobs: [], total: 0 }))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(
        screen.getByText("No orphaned DNS records. All clean!")
      ).toBeInTheDocument()
    })
  })

  it("renders orphan job list with data", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(
        screen.getByText("nextcloud.example.com")
      ).toBeInTheDocument()
    })
    expect(screen.getByText("vaultwarden.example.com")).toBeInTheDocument()
  })

  it("shows service names", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    expect(screen.getByText("Vaultwarden")).toBeInTheDocument()
  })

  it("shows record IDs", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("cf_r1")).toBeInTheDocument()
    })
    expect(screen.getByText("cf_r2")).toBeInTheDocument()
  })

  it("shows IP values", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("100.64.0.1")).toBeInTheDocument()
    })
    expect(screen.getByText("100.64.0.2")).toBeInTheDocument()
  })

  it("shows status badges", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("pending")).toBeInTheDocument()
    })
    expect(screen.getByText("failed")).toBeInTheDocument()
  })

  it("shows total count", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("2 orphaned records")).toBeInTheDocument()
    })
  })

  it("shows singular count for one record", async () => {
    const singleJob = { jobs: [mockJobs.jobs[0]], total: 1 }
    vi.stubGlobal("fetch", mockFetch(singleJob))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("1 orphaned record")).toBeInTheDocument()
    })
  })

  it("has retry and dismiss buttons for each job", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })
    const retryButtons = screen.getAllByText("Retry Deletion")
    const dismissButtons = screen.getAllByText("Dismiss")
    expect(retryButtons).toHaveLength(2)
    expect(dismissButtons).toHaveLength(2)
  })

  it("shows page heading and description", async () => {
    vi.stubGlobal("fetch", mockFetch({ jobs: [], total: 0 }))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    expect(screen.getByText("Orphaned DNS Records")).toBeInTheDocument()
    expect(
      screen.getByText(/DNS records left in Cloudflare/)
    ).toBeInTheDocument()
  })

  it("shows failure message on failed jobs", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(
        screen.getByText("Retry failed: API timeout")
      ).toBeInTheDocument()
    })
  })

  it("shows success message after retry", async () => {
    let jobCallCount = 0
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ success: true, message: "DNS record for 'nextcloud.example.com' cleaned up" }),
        })
      }
      // GET /jobs calls
      jobCallCount++
      if (jobCallCount === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockJobs) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ jobs: [mockJobs.jobs[1]], total: 1 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })

    const retryButtons = screen.getAllByText("Retry Deletion")
    fireEvent.click(retryButtons[0])

    await waitFor(() => {
      expect(
        screen.getByText(
          "DNS record for 'nextcloud.example.com' cleaned up"
        )
      ).toBeInTheDocument()
    })
  })

  it("shows success message after dismiss", async () => {
    // Mock window.confirm to return true
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true))

    let jobCallCount = 0
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "DELETE") {
        return Promise.resolve({ ok: true, status: 204, json: () => Promise.resolve(undefined) })
      }
      // GET /jobs calls
      jobCallCount++
      if (jobCallCount === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockJobs) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ jobs: [mockJobs.jobs[1]], total: 1 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })

    const dismissButtons = screen.getAllByText("Dismiss")
    fireEvent.click(dismissButtons[0])

    await waitFor(() => {
      expect(
        screen.getByText(
          "Orphan record for 'nextcloud.example.com' dismissed"
        )
      ).toBeInTheDocument()
    })
  })

  it("shows error message when retry fails", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: false,
          status: 502,
          json: () => Promise.resolve({ detail: "Cloudflare API error: connection refused" }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockJobs) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })

    const retryButtons = screen.getAllByText("Retry Deletion")
    fireEvent.click(retryButtons[0])

    await waitFor(() => {
      expect(
        screen.getByText("Cloudflare API error: connection refused")
      ).toBeInTheDocument()
    })
  })

  it("fetches with kind=dns_orphan_cleanup filter", async () => {
    const fetchMock = mockFetch({ jobs: [], total: 0 })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    render(
      <MemoryRouter>
        <OrphanDns />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const jobsCall = fetchMock.mock.calls.find(
      (c: unknown[]) => String(c[0]).includes("/jobs")
    )
    expect(jobsCall).toBeDefined()
    expect(String(jobsCall![0])).toContain("kind=dns_orphan_cleanup")
  })
})
