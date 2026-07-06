import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, fireEvent, act } from "@testing-library/react"
import { renderRoute, mockApi } from "./testkit"
import { makeContainer } from "./factories"

/** Build a fetch mock that returns discovery data for /discovery/ and services data for /services. */
function mockFetchResponses(
  containers: unknown[] = [],
  services: unknown[] = [],
) {
  return mockApi([
    { url: "/services", json: { services, total: services.length } },
    { json: { containers, total: containers.length } },
  ])
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Discover page", () => {
  it("shows loading state initially", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    expect(screen.getByText("Loading containers...")).toBeInTheDocument()
  })

  it("renders empty state when no containers", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    await waitFor(() => {
      expect(screen.getByText(/No containers found/)).toBeInTheDocument()
    })
  })

  it("renders container list with data", async () => {
    vi.stubGlobal("fetch", mockFetchResponses(
      [makeContainer()],
      [],
    ))
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    await waitFor(() => {
      expect(screen.getByText("nextcloud")).toBeInTheDocument()
    })
    expect(screen.getByText("nextcloud:28")).toBeInTheDocument()
    expect(screen.getByText("running")).toBeInTheDocument()
    expect(screen.getByText("80/tcp")).toBeInTheDocument()
    expect(screen.getByText("Expose")).toBeInTheDocument()
  })

  it("shows exposure count badge for already-exposed container", async () => {
    vi.stubGlobal("fetch", mockFetchResponses(
      [makeContainer()],
      [
        { upstream_container_id: "c1", name: "Nextcloud Web", hostname: "nc.example.com", status: { phase: "healthy" } },
        { upstream_container_id: "c1", name: "Nextcloud Setup", hostname: "setup.example.com", status: { phase: "pending" } },
      ],
    ))
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    await waitFor(() => {
      expect(screen.getByText("2 svc")).toBeInTheDocument()
    })
  })

  it("shows search input", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    expect(screen.getByPlaceholderText("Search by name or image...")).toBeInTheDocument()
  })

  it("has running only checkbox checked by default", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    const checkbox = screen.getByLabelText("Running only")
    expect(checkbox).toBeChecked()
  })

  it("shows Refresh button", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })
  })

  it("shows last refresh timestamp after load", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    await waitFor(() => {
      expect(screen.getByText(/Updated/)).toBeInTheDocument()
    })
  })

  it("reloads data when Refresh button clicked", async () => {
    const fetchMock = mockFetchResponses([], [])
    vi.stubGlobal("fetch", fetchMock)
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })
    const initialCallCount = fetchMock.mock.calls.length

    fireEvent.click(screen.getByText("Refresh"))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(initialCallCount)
    })
  })

  it("does not apply typed search to refresh until Search is submitted", async () => {
    const fetchMock = mockFetchResponses([], [])
    vi.stubGlobal("fetch", fetchMock)
    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText("Search by name or image..."), { target: { value: "nextcloud" } })
    fireEvent.click(screen.getByText("Refresh"))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(4)
    })
    const discoveryUrls = fetchMock.mock.calls
      .map((call) => String(call[0]))
      .filter((url) => url.includes("/api/discovery/containers"))
    expect(discoveryUrls[discoveryUrls.length - 1]).not.toContain("search=nextcloud")

    fireEvent.click(screen.getByText("Search"))

    await waitFor(() => {
      const discoveryUrlsAfterSearch = fetchMock.mock.calls
        .map((call) => String(call[0]))
        .filter((url) => url.includes("/api/discovery/containers"))
      const latestDiscoveryUrl = discoveryUrlsAfterSearch[discoveryUrlsAfterSearch.length - 1]
      expect(latestDiscoveryUrl).toContain("search=nextcloud")
    })
  })
  it("keeps showing containers when a background poll fails", async () => {
    // A transient 30s poll failure must not wipe the already-rendered list.
    const container = makeContainer()

    const intervalCallbacks: Array<() => void> = []
    vi.spyOn(globalThis, "setInterval").mockImplementation((handler: TimerHandler) => {
      if (typeof handler === "function") intervalCallbacks.push(handler as () => void)
      return 1 as unknown as ReturnType<typeof setInterval>
    })
    vi.spyOn(globalThis, "clearInterval").mockImplementation(() => undefined)

    let discoveryCalls = 0
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes("/discovery/containers")) {
        discoveryCalls++
        if (discoveryCalls === 1) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ containers: [container], total: 1 }),
          })
        }
        // The background poll fails.
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })
      }
      // /services and /settings stay healthy.
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ services: [], total: 0 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)

    await waitFor(() => {
      expect(screen.getByText("nextcloud")).toBeInTheDocument()
    })

    // Fire the captured poll tick; its discovery request 500s.
    await act(async () => {
      intervalCallbacks[0]?.()
      await Promise.resolve()
    })

    // List is preserved; the transient error does not replace it.
    expect(screen.getByText("nextcloud")).toBeInTheDocument()
    expect(screen.queryByText("Request failed: 500")).not.toBeInTheDocument()
  })

  it("shows a stale-data warning when a background poll fails after a good load", async () => {
    // A failed 30s poll while containers are on screen must not be silent: the
    // last-good list stays, and a non-blocking inline warning makes the
    // staleness visible instead of presenting stale data as current.
    const container = makeContainer()

    const intervalCallbacks: Array<() => void> = []
    vi.spyOn(globalThis, "setInterval").mockImplementation((handler: TimerHandler) => {
      if (typeof handler === "function") intervalCallbacks.push(handler as () => void)
      return 1 as unknown as ReturnType<typeof setInterval>
    })
    vi.spyOn(globalThis, "clearInterval").mockImplementation(() => undefined)

    let discoveryCalls = 0
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes("/discovery/containers")) {
        discoveryCalls++
        if (discoveryCalls === 1) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ containers: [container], total: 1 }),
          })
        }
        // The background poll fails.
        return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ services: [], total: 0 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)

    await waitFor(() => {
      expect(screen.getByText("nextcloud")).toBeInTheDocument()
    })
    // No warning while the data is fresh.
    expect(screen.queryByText(/Couldn't refresh/)).not.toBeInTheDocument()

    // Fire the captured poll tick; its discovery request 500s.
    await act(async () => {
      intervalCallbacks[0]?.()
      await Promise.resolve()
    })

    // The list is preserved AND the staleness is now visible.
    await waitFor(() => {
      expect(screen.getByText(/Couldn't refresh/)).toBeInTheDocument()
    })
    expect(screen.getByText("nextcloud")).toBeInTheDocument()
  })

  it("does not flash the empty-state over a prior error during a background poll", async () => {
    // With the list empty AND a prior request errored, an in-flight 30s poll must
    // keep the last error visible rather than momentarily blanking it to the
    // "No containers found" empty-state. Regression: load() used to clear the
    // error at the START of every poll, producing a flicker.
    const intervalCallbacks: Array<() => void> = []
    vi.spyOn(globalThis, "setInterval").mockImplementation((handler: TimerHandler) => {
      if (typeof handler === "function") intervalCallbacks.push(handler as () => void)
      return 1 as unknown as ReturnType<typeof setInterval>
    })
    vi.spyOn(globalThis, "clearInterval").mockImplementation(() => undefined)

    let discoveryCalls = 0
    let resolvePoll: ((value: unknown) => void) | null = null
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes("/discovery/containers")) {
        discoveryCalls++
        if (discoveryCalls === 1) {
          // Initial mount load fails -> error shown, list empty.
          return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) })
        }
        // Background poll: stays pending so we can inspect the interim render.
        return new Promise((resolve) => { resolvePoll = resolve })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ services: [], total: 0 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)

    // Initial load errored: the error is shown, not the empty-state.
    await waitFor(() => {
      expect(screen.getByText("Request failed: 500")).toBeInTheDocument()
    })
    expect(screen.queryByText(/No containers found/)).not.toBeInTheDocument()
    // The load error is injected asynchronously and must announce to assistive
    // tech via a live region (role="alert").
    expect(screen.getByRole("alert")).toHaveTextContent("Request failed: 500")

    // Fire the poll tick; its discovery request is still pending.
    await act(async () => {
      intervalCallbacks[0]?.()
      await Promise.resolve()
    })

    // While the poll is in flight, the prior error must persist — no flash to
    // the empty-state.
    expect(screen.queryByText(/No containers found/)).not.toBeInTheDocument()
    expect(screen.getByText("Request failed: 500")).toBeInTheDocument()

    // Let the pending poll resolve to avoid an unhandled promise.
    await act(async () => {
      resolvePoll?.({ ok: true, json: () => Promise.resolve({ containers: [], total: 0 }) })
      await Promise.resolve()
    })
  })

  it("keeps the loaded list visible during a manual foreground refresh", async () => {
    // Regression (FP1): the content gate was a bare `loading` check, so clicking
    // Refresh (a foreground load) blanked the whole table to a "Loading
    // containers..." spinner even though a perfectly good list was on screen —
    // unlike Dashboard/Services, which keep their data visible. The gate must be
    // `loading && containers.length === 0` so only the *first* load spins.
    const container = makeContainer()

    let discoveryCalls = 0
    let pendingRefresh: { promise: Promise<unknown>; resolve: (value: unknown) => void } | null = null
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const u = String(url)
      if (u.includes("/discovery/containers")) {
        discoveryCalls++
        if (discoveryCalls === 1) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ containers: [container], total: 1 }),
          })
        }
        // The manual refresh stays pending so we can inspect the interim render.
        const { promise, resolve } = Promise.withResolvers<unknown>()
        pendingRefresh = { promise, resolve }
        return promise
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ services: [], total: 0 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: Discover } = await import("@/pages/Discover")
    renderRoute(<Discover />)

    await waitFor(() => {
      expect(screen.getByText("nextcloud")).toBeInTheDocument()
    })

    // Click Refresh: a foreground load that stays in flight.
    await act(async () => {
      fireEvent.click(screen.getByText("Refresh"))
      await Promise.resolve()
    })

    // The existing list stays put; no full-table spinner replaces it.
    expect(screen.getByText("nextcloud")).toBeInTheDocument()
    expect(screen.queryByText("Loading containers...")).not.toBeInTheDocument()

    // Let the refresh resolve to avoid an unhandled promise.
    await act(async () => {
      pendingRefresh?.resolve({ ok: true, json: () => Promise.resolve({ containers: [container], total: 1 }) })
      await Promise.resolve()
    })
  })
})
