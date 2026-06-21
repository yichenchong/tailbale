import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

const mockSummary = {
  services: { total: 5, healthy: 3, warning: 1, error: 1 },
  expiring_certs: [
    {
      service_id: "svc_1",
      service_name: "Nextcloud",
      hostname: "nextcloud.example.com",
      expires_at: new Date(Date.now() + 10 * 86400000).toISOString(),
    },
  ],
  recent_errors: [
    {
      id: "evt_1",
      service_id: "svc_2",
      kind: "reconcile_failed",
      message: "Edge container crashed",
      created_at: new Date().toISOString(),
    },
  ],
  recent_events: [
    {
      id: "evt_2",
      service_id: "svc_1",
      kind: "cert_issued",
      level: "info",
      message: "Certificate issued for nextcloud.example.com",
      created_at: new Date().toISOString(),
    },
    {
      id: "evt_3",
      service_id: null,
      kind: "reconcile_completed",
      level: "warning",
      message: "Reconcile completed with warnings",
      created_at: new Date().toISOString(),
    },
  ],
}

beforeEach(() => {
  vi.restoreAllMocks()
})

function mockFetch(data: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(data),
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe("Dashboard page", () => {
  it("shows loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    expect(screen.getByText("Loading dashboard...")).toBeInTheDocument()
  })

  it("shows error state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: "Internal server error" }),
      })
    )
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Internal server error")).toBeInTheDocument()
    })
  })

  it("renders summary cards with correct counts", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Total Services")).toBeInTheDocument()
    })
    expect(screen.getByText("5")).toBeInTheDocument()
    expect(screen.getByText("Healthy")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()
    expect(screen.getByText("Warning")).toBeInTheDocument()
    // Both warning and error have value "1", so use getAllByText
    expect(screen.getAllByText("1")).toHaveLength(2)
    expect(screen.getByText("Error")).toBeInTheDocument()
  })

  it("renders expiring certificates section", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Upcoming Cert Expiries")).toBeInTheDocument()
    })
    expect(screen.getAllByText(/Nextcloud/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/nextcloud\.example\.com/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/\d+d left/)).toBeInTheDocument()
  })

  it("shows empty certs message when none expiring", async () => {
    const data = { ...mockSummary, expiring_certs: [] }
    vi.stubGlobal("fetch", mockFetch(data))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(
        screen.getByText("No certificates expiring within 30 days.")
      ).toBeInTheDocument()
    })
  })

  it("renders recent errors section", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Recent Errors")).toBeInTheDocument()
    })
    expect(screen.getByText("Edge container crashed")).toBeInTheDocument()
  })

  it("shows empty errors message when no errors", async () => {
    const data = { ...mockSummary, recent_errors: [] }
    vi.stubGlobal("fetch", mockFetch(data))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("No recent errors.")).toBeInTheDocument()
    })
  })

  it("renders recent events timeline", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Recent Events")).toBeInTheDocument()
    })
    expect(
      screen.getByText("Certificate issued for nextcloud.example.com")
    ).toBeInTheDocument()
    expect(
      screen.getByText("Reconcile completed with warnings")
    ).toBeInTheDocument()
  })

  it("shows level badges on events", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("info")).toBeInTheDocument()
    })
    expect(screen.getByText("warning")).toBeInTheDocument()
  })

  it("shows empty events message when no events", async () => {
    const data = { ...mockSummary, recent_events: [] }
    vi.stubGlobal("fetch", mockFetch(data))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("No events yet.")).toBeInTheDocument()
    })
  })

  it("renders service links for expiring certs", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText(/Nextcloud/)).toBeInTheDocument()
    })
    const link = screen.getByText(/Nextcloud/).closest("a")
    expect(link).toHaveAttribute("href", "/services/svc_1")
  })

  it("shows Refresh button", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })
  })

  it("shows last refresh timestamp after load", async () => {
    vi.stubGlobal("fetch", mockFetch(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText(/Updated/)).toBeInTheDocument()
    })
  })

  it("reloads data when Refresh button clicked", async () => {
    const fetchMock = mockFetch(mockSummary)
    vi.stubGlobal("fetch", fetchMock)
    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })
    const initialCallCount = fetchMock.mock.calls.length

    fireEvent.click(screen.getByText("Refresh"))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(initialCallCount)
    })
  })

  it("keeps the newest dashboard refresh when an older request finishes later", async () => {
    const initialSummary = {
      ...mockSummary,
      services: { total: 1, healthy: 0, warning: 0, error: 0 },
      expiring_certs: [],
      recent_errors: [],
      recent_events: [],
    }
    const staleSummary = {
      ...initialSummary,
      services: { total: 7, healthy: 0, warning: 0, error: 0 },
    }
    const newestSummary = {
      ...initialSummary,
      services: { total: 42, healthy: 0, warning: 0, error: 0 },
    }
    const staleRefresh = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const intervalCallbacks: Array<() => void> = []
    vi.spyOn(globalThis, "setInterval").mockImplementation((handler: TimerHandler) => {
      if (typeof handler === "function") intervalCallbacks.push(handler as () => void)
      return 1 as unknown as ReturnType<typeof setInterval>
    })
    vi.spyOn(globalThis, "clearInterval").mockImplementation(() => undefined)

    let dashboardCalls = 0
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ general: { timezone: "UTC" } }) })
      }
      dashboardCalls++
      if (dashboardCalls === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(initialSummary) })
      }
      if (dashboardCalls === 2) {
        return staleRefresh.promise
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(newestSummary) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: Dashboard } = await import("@/pages/Dashboard")
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Refresh"))
    await act(async () => {
      intervalCallbacks[0]()
    })

    await waitFor(() => {
      expect(screen.getByText("42")).toBeInTheDocument()
    })

    await act(async () => {
      staleRefresh.resolve({ ok: true, json: () => Promise.resolve(staleSummary) })
      await staleRefresh.promise
      await Promise.resolve()
    })

    expect(screen.getByText("42")).toBeInTheDocument()
    expect(screen.queryByText("7")).not.toBeInTheDocument()
  })
})
