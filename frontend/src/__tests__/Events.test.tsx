import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

const mockEvents = {
  events: [
    {
      id: "evt_1",
      service_id: "svc_1",
      kind: "cert_issued",
      level: "info",
      message: "Certificate issued for nextcloud.example.com",
      details: { hostname: "nextcloud.example.com", issuer: "letsencrypt" },
      created_at: "2026-04-05T12:00:00Z",
    },
    {
      id: "evt_2",
      service_id: "svc_2",
      kind: "reconcile_failed",
      level: "error",
      message: "Edge container failed to start",
      details: null,
      created_at: "2026-04-05T11:00:00Z",
    },
    {
      id: "evt_3",
      service_id: null,
      kind: "dns_updated",
      level: "warning",
      message: "DNS record drifted, corrected",
      details: { old_ip: "1.2.3.4", new_ip: "5.6.7.8" },
      created_at: "2026-04-05T10:00:00Z",
    },
  ],
  total: 3,
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

describe("Events page", () => {
  it("shows loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Events } = await import("@/pages/Events")
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Server error")).toBeInTheDocument()
    })
  })

  it("shows empty state when no events", async () => {
    vi.stubGlobal("fetch", mockFetch({ events: [], total: 0 }))
    const { default: Events } = await import("@/pages/Events")
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("No events found.")).toBeInTheDocument()
    })
  })

  it("renders event list with data", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("info")).toBeInTheDocument()
    })
    expect(screen.getByText("error")).toBeInTheDocument()
    expect(screen.getByText("warning")).toBeInTheDocument()
  })

  it("shows kind column", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("3 events")).toBeInTheDocument()
    })
  })

  it("has search input", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
    expect(
      screen.getByPlaceholderText("Search messages...")
    ).toBeInTheDocument()
  })

  it("has level filter dropdown", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
    const selects = screen.getAllByRole("combobox")
    const kindSelect = selects[1]
    expect(kindSelect).toBeInTheDocument()
    const options = kindSelect.querySelectorAll("option")
    const values = Array.from(options).map((o) => o.textContent)
    expect(values).toContain("All kinds")
    expect(values).toContain("service_created")
    expect(values).toContain("reconcile_failed")
  })

  it("expands event details on click", async () => {
    vi.stubGlobal("fetch", mockFetch(mockEvents))
    const { default: Events } = await import("@/pages/Events")
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
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

  it("shows pagination when total exceeds limit", async () => {
    const manyEvents = {
      events: mockEvents.events,
      total: 100, // exceeds limit of 50
    }
    vi.stubGlobal("fetch", mockFetch(manyEvents))
    const { default: Events } = await import("@/pages/Events")
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Events />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(
        screen.getByText("Certificate issued for nextcloud.example.com")
      ).toBeInTheDocument()
    })
    expect(screen.queryByText("Previous")).not.toBeInTheDocument()
    expect(screen.queryByText("Next")).not.toBeInTheDocument()
  })
})
