import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, fireEvent, act } from "@testing-library/react"
import { renderRoute, jsonOk } from "./testkit"

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
    renderRoute(<Dashboard />)
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
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("Internal server error")).toBeInTheDocument()
    })
  })

  it("renders summary cards with correct counts", async () => {
    vi.stubGlobal("fetch", jsonOk(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
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
    vi.stubGlobal("fetch", jsonOk(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("Upcoming Cert Expiries")).toBeInTheDocument()
    })
    expect(screen.getAllByText(/Nextcloud/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/nextcloud\.example\.com/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/\d+d left/)).toBeInTheDocument()
  })

  it("shows empty certs message when none expiring", async () => {
    const data = { ...mockSummary, expiring_certs: [] }
    vi.stubGlobal("fetch", jsonOk(data))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(
        screen.getByText("No certificates approaching expiry.")
      ).toBeInTheDocument()
    })
  })

  it("renders recent errors section", async () => {
    vi.stubGlobal("fetch", jsonOk(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("Recent Errors")).toBeInTheDocument()
    })
    expect(screen.getByText("Edge container crashed")).toBeInTheDocument()
  })

  it("shows empty errors message when no errors", async () => {
    const data = { ...mockSummary, recent_errors: [] }
    vi.stubGlobal("fetch", jsonOk(data))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("No recent errors.")).toBeInTheDocument()
    })
  })

  it("links to all events when more than eight recent errors are truncated", async () => {
    // The backend returns up to 20 recent errors but the panel renders only 8;
    // without a "View all events" affordance the extra errors vanish silently,
    // unlike the sibling Recent Events panel. mockSummary keeps recent_events
    // short (2), so the single "View all events" link must come from this panel.
    const errors = Array.from({ length: 9 }, (_, i) => ({
      id: `err_${i}`,
      service_id: null,
      kind: "reconcile_failed",
      message: `Error number ${i}`,
      created_at: new Date().toISOString(),
    }))
    vi.stubGlobal("fetch", jsonOk({ ...mockSummary, recent_errors: errors }))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("Recent Errors")).toBeInTheDocument()
    })
    // Only eight rows render; the ninth is truncated...
    expect(screen.getByText("Error number 0")).toBeInTheDocument()
    expect(screen.getByText("Error number 7")).toBeInTheDocument()
    expect(screen.queryByText("Error number 8")).not.toBeInTheDocument()
    // ...so an honest affordance links to the full log.
    const link = screen.getByRole("link", { name: "View all events" })
    expect(link).toHaveAttribute("href", "/events")
  })

  it("omits the truncation link when eight or fewer recent errors fit", async () => {
    const errors = Array.from({ length: 8 }, (_, i) => ({
      id: `err_${i}`,
      service_id: null,
      kind: "reconcile_failed",
      message: `Error number ${i}`,
      created_at: new Date().toISOString(),
    }))
    vi.stubGlobal("fetch", jsonOk({ ...mockSummary, recent_errors: errors }))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("Error number 7")).toBeInTheDocument()
    })
    expect(screen.queryByText("View all events")).not.toBeInTheDocument()
  })

  it("renders recent events timeline", async () => {
    vi.stubGlobal("fetch", jsonOk(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
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
    vi.stubGlobal("fetch", jsonOk(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("info")).toBeInTheDocument()
    })
    expect(screen.getByText("warning")).toBeInTheDocument()
  })

  it("shows empty events message when no events", async () => {
    const data = { ...mockSummary, recent_events: [] }
    vi.stubGlobal("fetch", jsonOk(data))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("No events yet.")).toBeInTheDocument()
    })
  })

  it("renders service links for expiring certs", async () => {
    vi.stubGlobal("fetch", jsonOk(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText(/Nextcloud/)).toBeInTheDocument()
    })
    const link = screen.getByText(/Nextcloud/).closest("a")
    expect(link).toHaveAttribute("href", "/services/svc_1")
  })

  it("shows Refresh button", async () => {
    vi.stubGlobal("fetch", jsonOk(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })
  })

  it("shows last refresh timestamp after load", async () => {
    vi.stubGlobal("fetch", jsonOk(mockSummary))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText(/Updated/)).toBeInTheDocument()
    })
  })

  it("reloads data when Refresh button clicked", async () => {
    const fetchMock = jsonOk(mockSummary)
    vi.stubGlobal("fetch", fetchMock)
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
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
    renderRoute(<Dashboard />)

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

  it("computes expiring cert days in UTC for offset-less timestamps", async () => {
    // Backend expires_at serializes naive (no offset) but means UTC. On a
    // +09:00 host a raw `new Date()` parse loses ~9h and drops the day count.
    const originalTz = process.env.TZ
    process.env.TZ = "Asia/Tokyo"
    try {
      const naive = new Date(Date.now() + 10.25 * 86400000).toISOString().replace("Z", "")
      const data = {
        ...mockSummary,
        expiring_certs: [
          { service_id: "svc_1", service_name: "Nextcloud", hostname: "nextcloud.example.com", expires_at: naive },
        ],
      }
      vi.stubGlobal("fetch", jsonOk(data))
      const { default: Dashboard } = await import("@/pages/Dashboard")
      renderRoute(<Dashboard />)
      await waitFor(() => {
        expect(screen.getByText("11d left")).toBeInTheDocument()
      })
      expect(screen.queryByText("10d left")).not.toBeInTheDocument()
    } finally {
      if (originalTz === undefined) delete process.env.TZ
      else process.env.TZ = originalTz
    }
  })

  it("labels a cert that lapsed within the last 24h as Expired", async () => {
    // diffMs is negative but Math.ceil(diffMs/day) === 0 for the first 24h after
    // expiry; the label must read "Expired", not "0d left".
    const justExpired = new Date(Date.now() - 12 * 3600000).toISOString()
    const data = {
      ...mockSummary,
      expiring_certs: [
        { service_id: "svc_1", service_name: "Nextcloud", hostname: "nextcloud.example.com", expires_at: justExpired },
      ],
    }
    vi.stubGlobal("fetch", jsonOk(data))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("Expired")).toBeInTheDocument()
    })
    expect(screen.queryByText("0d left")).not.toBeInTheDocument()
  })

  it("renders a sentinel instead of NaN for an unparseable cert date", async () => {
    // Regression: a bad/absent expires_at made Math.ceil(NaN) render "NaNd left".
    // Mirror lib/certStatus.ts and render the em-dash sentinel instead.
    const data = {
      ...mockSummary,
      expiring_certs: [
        { service_id: "svc_1", service_name: "Nextcloud", hostname: "nextcloud.example.com", expires_at: "garbage" },
      ],
    }
    vi.stubGlobal("fetch", jsonOk(data))
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText(/Nextcloud/)).toBeInTheDocument()
    })
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
    expect(screen.getByText("\u2014")).toBeInTheDocument()
  })

  it("keeps prior data visible when a refresh fails", async () => {
    // A transient poll/refresh failure must not blank the dashboard: data is set
    // only on success, and the full error screen is reserved for !data.
    const initial = { ...mockSummary, services: { total: 13, healthy: 0, warning: 0, error: 0 } }
    let dashboardCalls = 0
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ general: { timezone: "UTC" } }) })
      }
      dashboardCalls++
      if (dashboardCalls === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(initial) })
      }
      return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ detail: "refresh failed" }) })
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("13")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Refresh"))
    await waitFor(() => {
      expect(dashboardCalls).toBeGreaterThan(1)
    })

    // Prior data persists; the error never takes over the page.
    expect(screen.getByText("13")).toBeInTheDocument()
    expect(screen.queryByText("refresh failed")).not.toBeInTheDocument()
  })

  it("announces a stale-data warning when a refresh fails while data is on screen", async () => {
    // A polling page that silently swallows a failed poll presents stale data as
    // current. Parity with Discover/Services: keep the last-good data AND show a
    // polite live-region (role="status") notice so the staleness is announced.
    const initial = { ...mockSummary, services: { total: 13, healthy: 0, warning: 0, error: 0 } }
    let dashboardCalls = 0
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ general: { timezone: "UTC" } }) })
      }
      dashboardCalls++
      if (dashboardCalls === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(initial) })
      }
      return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ detail: "refresh failed" }) })
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: Dashboard } = await import("@/pages/Dashboard")
    renderRoute(<Dashboard />)
    await waitFor(() => {
      expect(screen.getByText("13")).toBeInTheDocument()
    })
    // No warning while the data is fresh.
    expect(screen.queryByText(/Couldn't refresh/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByText("Refresh"))

    // The stale-data warning surfaces in a polite live region; data persists and
    // the raw error text never leaks into the page.
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent(/Couldn't refresh/)
    })
    expect(screen.getByText("13")).toBeInTheDocument()
    expect(screen.queryByText("refresh failed")).not.toBeInTheDocument()
  })
})
