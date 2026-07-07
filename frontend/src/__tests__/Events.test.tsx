import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, screen, waitFor, fireEvent } from "@testing-library/react"
import { renderRoute, mockApi } from "./testkit"
import { makeEvent } from "./factories"

const mockEvents = {
  events: [
    makeEvent(),
    makeEvent({
      id: "evt_2",
      service_id: "svc_2",
      kind: "reconcile_failed",
      level: "error",
      message: "Edge container failed to start",
      details: null,
      created_at: "2026-04-05T11:00:00Z",
    }),
    makeEvent({
      id: "evt_3",
      service_id: null,
      kind: "dns_updated",
      level: "warning",
      message: "DNS record drifted, corrected",
      details: { old_ip: "1.2.3.4", new_ip: "5.6.7.8" },
      created_at: "2026-04-05T10:00:00Z",
    }),
  ],
  total: 3,
}

beforeEach(() => {
  vi.restoreAllMocks()
})

const MOCK_EVENT_KINDS = [
  "service_created", "service_updated", "service_disabled", "service_deleted",
  "service_snippet_changed",
  "edge_started", "edge_restarted", "edge_recreated", "edge_updated",
  "caddy_reloaded", "tailscale_ip_acquired",
  "cert_issued", "cert_renewed", "cert_failed",
  "dns_created", "dns_updated", "dns_removed", "dns_update_failed",
  "dns_cleanup_failed",
  "dns_orphan_created", "dns_orphan_resolved", "dns_orphan_retry_failed",
  "dns_orphan_dismissed",
  "probe_retry_phase_change",
  "reconcile_completed", "reconcile_failed",
]

// Route-aware fetch stub: GET /events/kinds returns the kind registry the
// Events dropdown is built from; every other URL returns `data` (the events
// list payload the test under exercise cares about).
function mockFetch(data: unknown) {
  return mockApi([
    { url: "/events/kinds", json: { kinds: MOCK_EVENT_KINDS } },
    { json: data },
  ])
}

describe("Events page", () => {
  it("shows loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    expect(screen.getByText("Loading events...")).toBeInTheDocument()
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
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(screen.getByText("Server error")).toBeInTheDocument()
    })
  })

  it("shows empty state when no events", async () => {
    vi.stubGlobal("fetch", mockFetch({ events: [], total: 0 }))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(screen.getByText("No events found.")).toBeInTheDocument()
    })
  })

  it("renders event list with data", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(
        screen.getByText("Certificate issued for nextcloud.example.com")
      ).toBeInTheDocument()
    })
    expect(
      screen.getByText("Edge container failed to start")
    ).toBeInTheDocument()
    expect(
      screen.getByText("DNS record drifted, corrected")
    ).toBeInTheDocument()
  })

  it("shows level badges", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(screen.getByText("info")).toBeInTheDocument()
    })
    expect(screen.getByText("error")).toBeInTheDocument()
    expect(screen.getByText("warning")).toBeInTheDocument()
  })

  it("shows kind column", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(screen.getAllByText("cert_issued").length).toBeGreaterThanOrEqual(1)
    })
    // reconcile_failed and dns_updated appear in both dropdown options and table
    expect(screen.getAllByText("reconcile_failed").length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText("dns_updated").length).toBeGreaterThanOrEqual(1)
  })

  it("shows total count", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(screen.getByText("3 events")).toBeInTheDocument()
    })
  })

  it("has search input", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    expect(
      screen.getByPlaceholderText("Search messages...")
    ).toBeInTheDocument()
  })

  it("has level filter dropdown", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    const select = screen.getAllByRole("combobox")[0]
    expect(select).toBeInTheDocument()
    // Check option values
    const options = select.querySelectorAll("option")
    const values = Array.from(options).map((o) => o.textContent)
    expect(values).toContain("All levels")
    expect(values).toContain("Info")
    expect(values).toContain("Warning")
    expect(values).toContain("Error")
  })

  it("has kind filter dropdown", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    const kindSelect = screen.getAllByRole("combobox")[1]
    expect(kindSelect).toBeInTheDocument()
    // "All kinds" renders immediately; per-kind options arrive once
    // GET /events/kinds resolves.
    await waitFor(() => {
      const values = Array.from(kindSelect.querySelectorAll("option")).map((o) => o.textContent)
      expect(values).toContain("service_created")
    })
    const values = Array.from(kindSelect.querySelectorAll("option")).map((o) => o.textContent)
    expect(values).toContain("All kinds")
    expect(values).toContain("reconcile_failed")
  })

  it("builds the kind filter from GET /events/kinds, not a hardcoded mirror", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    const kindSelect = screen.getAllByRole("combobox")[1]
    // The dropdown is populated from the kinds the backend reports: the mocked
    // GET /events/kinds returns MOCK_EVENT_KINDS, so exactly those (plus the
    // "All kinds" empty-value sentinel) must appear as filter options. No
    // longer self-referential against an in-component copy.
    await waitFor(() => {
      const values = Array.from(kindSelect.querySelectorAll("option")).map((o) => o.getAttribute("value"))
      expect(values).toContain("reconcile_completed")
    })
    const values = Array.from(kindSelect.querySelectorAll("option")).map((o) => o.getAttribute("value"))
    for (const kind of MOCK_EVENT_KINDS) {
      expect(values).toContain(kind)
    }
    const kindValues = values.filter((v) => v !== "")
    expect([...kindValues].sort()).toEqual([...MOCK_EVENT_KINDS].sort())
  })

  it("uses a singular noun when there is exactly one event", async () => {
    vi.stubGlobal("fetch", mockFetch({ events: [mockEvents.events[0]], total: 1 }))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(screen.getByText("1 event")).toBeInTheDocument()
    })
    expect(screen.queryByText("1 events")).not.toBeInTheDocument()
  })

  it("expands event details on click", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(
        screen.getByText("Certificate issued for nextcloud.example.com")
      ).toBeInTheDocument()
    })

    // Click the first event row (which has details)
    const row = screen
      .getByText("Certificate issued for nextcloud.example.com")
      .closest("tr")!
    fireEvent.click(row)

    await waitFor(() => {
      // Details should be visible as JSON
      expect(screen.getByText(/"hostname"/)).toBeInTheDocument()
    })
  })

  it("exposes event details through an accessible expand button", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(
        screen.getByText("Certificate issued for nextcloud.example.com")
      ).toBeInTheDocument()
    })

    const expand = screen.getByRole("button", {
      name: "Expand details for Certificate issued for nextcloud.example.com",
    })
    expect(expand).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(expand)

    expect(
      screen.getByRole("button", {
        name: "Collapse details for Certificate issued for nextcloud.example.com",
      })
    ).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByText(/"hostname"/)).toBeInTheDocument()
  })

  it("shows pagination when total exceeds limit", async () => {
    const manyEvents = {
      events: mockEvents.events,
      total: 100, // exceeds limit of 50
    }
    vi.stubGlobal("fetch", mockFetch(manyEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(screen.getByText("Previous")).toBeInTheDocument()
    })
    expect(screen.getByText("Next")).toBeInTheDocument()
    expect(screen.getByText("1–50 of 100")).toBeInTheDocument()
    // Previous should be disabled on first page
    expect(screen.getByText("Previous")).toBeDisabled()
  })

  it("does not show pagination when all events fit", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(
        screen.getByText("Certificate issued for nextcloud.example.com")
      ).toBeInTheDocument()
    })
    expect(screen.queryByText("Previous")).not.toBeInTheDocument()
    expect(screen.queryByText("Next")).not.toBeInTheDocument()
    // And no "Page 1 of 1" style range indicator when everything fits.
    expect(screen.queryByText(/of 3/)).not.toBeInTheDocument()
  })

  it("debounces the search input instead of fetching on every keystroke", async () => {
    vi.useFakeTimers()
    try {
      const fetchMock = mockFetch(mockEvents)
      vi.stubGlobal("fetch", fetchMock)
      const { default: Events } = await import("@/pages/Events")
      renderRoute(<Events />)
      // Settle the initial mount load and any pending debounce timer.
      await act(async () => { await vi.runAllTimersAsync() })
      const baseline = fetchMock.mock.calls.length

      const input = screen.getByPlaceholderText("Search messages...")
      await act(async () => {
        fireEvent.change(input, { target: { value: "a" } })
        fireEvent.change(input, { target: { value: "ab" } })
        fireEvent.change(input, { target: { value: "abc" } })
      })
      // Still within the debounce window: no extra request fired yet.
      expect(fetchMock.mock.calls.length).toBe(baseline)

      // Once typing settles, exactly one request goes out with the final query.
      await act(async () => { await vi.advanceTimersByTimeAsync(300) })
      const after = fetchMock.mock.calls.slice(baseline)
      expect(after).toHaveLength(1)
      expect(String(after[0][0])).toContain("search=abc")
    } finally {
      vi.useRealTimers()
    }
  })

  it("clamps back to a populated page when retention shrinks total off the current page", async () => {
    // Events never clamped offset-on-shrink (a latent bug): the new retention
    // cleanup can delete the events on a later page, shrinking `total` so the
    // offset points past the end. The shared usePagination clamp now corrects it
    // instead of stranding the user on an empty "No events found." page.
    let shrunk = false
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      const offset = Number(new URL(String(url), "http://localhost").searchParams.get("offset") ?? "0")
      if (offset >= 50) {
        // Page 2's events were cleaned up: empty page, total already shrank.
        shrunk = true
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ events: [], total: 50 }) })
      }
      // Page 1 shows >limit until the shrink, then reports the smaller total.
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ events: mockEvents.events, total: shrunk ? 50 : 51 }),
      })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)

    // Page 1: events visible and a Next button (51 > page size 50).
    await waitFor(() => expect(screen.getByText("Next")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Next"))
    })

    // The offset=50 fetch fired (clamp path exercised), but the UI clamps back
    // to page 1 instead of falsely reporting an empty list.
    await waitFor(() =>
      expect(screen.getByText("Certificate issued for nextcloud.example.com")).toBeInTheDocument()
    )
    expect(screen.queryByText("No events found.")).not.toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some((c: unknown[]) => String(c[0]).includes("offset=50"))
    ).toBe(true)
  })

  it("gives the search box an accessible name", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    expect(
      screen.getByLabelText("Search event messages")
    ).toBeInTheDocument()
  })

  it("gives the level and kind filters accessible names", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    expect(screen.getByLabelText("Filter by level")).toBeInTheDocument()
    expect(screen.getByLabelText("Filter by kind")).toBeInTheDocument()
  })

  it("marks every table column header with scope=col", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    renderRoute(<Events />)
    await waitFor(() => {
      expect(
        screen.getByText("Certificate issued for nextcloud.example.com")
      ).toBeInTheDocument()
    })
    const headers = screen.getAllByRole("columnheader")
    expect(headers).toHaveLength(5)
    headers.forEach((h) => expect(h).toHaveAttribute("scope", "col"))
  })
})
