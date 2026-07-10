import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import { formatDateTime, _resetTimezoneCache } from "@/lib/useTimezone"
import { mockService, renderWithRoute } from "./serviceDetailTestUtils"
import { makeService, makeServiceStatus } from "./factories"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("ServiceDetail page - render", () => {
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
    expect(screen.getAllByText("Pending").length).toBeGreaterThanOrEqual(1)
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

  it("renders a human label for every backend health-check key (no raw snake_case leaks)", async () => {
    const KEY_TO_LABEL: Record<string, string> = {
      upstream_container_present: "Upstream Container",
      upstream_network_connected: "Network Connected",
      edge_container_present: "Edge Container",
      edge_container_running: "Edge Running",
      tailscale_ready: "Tailscale Ready",
      tailscale_ip_present: "Tailscale IP",
      cert_present: "Certificate",
      cert_not_expiring: "Cert Valid",
      dns_record_present: "DNS Record",
      dns_matches_ip: "DNS Matches IP",
      caddy_config_present: "Caddy Config",
      https_probe_ok: "HTTPS Probe",
    }
    // All checks pass: each label renders exactly once (no failing-suggestions
    // box), so a leaked raw key would be unambiguous.
    const allPass = Object.fromEntries(Object.keys(KEY_TO_LABEL).map((k) => [k, true]))
    const svc = { ...mockService, status: { ...mockService.status, health_checks: allPass } }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(svc),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Health Checks")).toBeInTheDocument()
    })
    // Scope label lookups to the Health Checks card: a few labels ("Edge
    // Container", "Tailscale IP") also appear verbatim as Runtime-section rows.
    const section = screen.getByText("Health Checks").closest("div") as HTMLElement
    for (const [key, label] of Object.entries(KEY_TO_LABEL)) {
      expect(within(section).getByText(label)).toBeInTheDocument()
      // The raw key must never reach the user anywhere when a label exists for it.
      expect(screen.queryByText(key)).not.toBeInTheDocument()
    }
  })

  it("falls back to the raw key for an unrecognized health check", async () => {
    // Graceful degradation: a key with no CHECK_LABELS entry renders verbatim
    // rather than blanking out, so a newly-added backend check is still visible.
    const svc = {
      ...mockService,
      status: { ...mockService.status, health_checks: { future_unknown_check: true } },
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(svc),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("future_unknown_check")).toBeInTheDocument()
    })
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
    // The error is injected asynchronously after a failed load, so it must be
    // announced to assistive tech via a live region (role="alert").
    expect(screen.getByRole("alert")).toHaveTextContent("Service not found")
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

  it("shows a live countdown for an offset-less probe retry timestamp", async () => {
    // probe_retry_at serializes naive (no offset) but means UTC. On a +09:00
    // host a raw `new Date()` parse would shift it ~9h into the past and the
    // banner would read "any moment now" instead of the real countdown.
    const originalTz = process.env.TZ
    process.env.TZ = "Asia/Tokyo"
    try {
      const retryNaive = new Date(Date.now() + 30_000).toISOString().replace("Z", "")
      const svc = {
        ...mockService,
        status: {
          ...mockService.status!,
          health_checks: { ...mockService.status!.health_checks, https_probe_ok: false },
          probe_retry_at: retryNaive,
          probe_retry_attempt: 2,
        },
      }
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(svc),
      }))
      await renderWithRoute("/services/svc_abc123")
      const banner = await screen.findByText(/HTTPS probe retry/)
      expect(banner.textContent).toMatch(/in \d+s/)
      expect(banner.textContent).not.toMatch(/any moment now/)
    } finally {
      if (originalTz === undefined) delete process.env.TZ
      else process.env.TZ = originalTz
    }
  })

  it("localizes a non-null Last Reconciled timestamp instead of showing the raw ISO string", async () => {
    // last_reconciled_at serializes naive (no offset) but means UTC. The
    // Runtime row used to render it verbatim, leaking a raw "2026-06-21T12:00:00"
    // string that ignored the configured timezone unlike every sibling row.
    _resetTimezoneCache()
    const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone
    const naive = "2026-06-21T12:00:00"
    const svc = {
      ...mockService,
      status: { ...mockService.status, last_reconciled_at: naive },
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(svc),
    }))
    await renderWithRoute("/services/svc_abc123")
    const expected = formatDateTime(naive, browserTz)
    expect(await screen.findByText(expected)).toBeInTheDocument()
    // The raw, un-localized ISO string must never reach the user.
    expect(screen.queryByText(naive)).not.toBeInTheDocument()
  })

  it("color-codes the Cert Expiry row by urgency via formatCertExpiry", async () => {
    // The Runtime "Cert Expiry" row now flows through lib/certStatus'
    // formatCertExpiry instead of a bare formatDate, so a cert expiring within
    // the 14-day "soon" band renders yellow/emphasized rather than plain.
    const soon = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000)
      .toISOString()
      .replace("Z", "")
    const svc = {
      ...mockService,
      status: { ...mockService.status, cert_expires_at: soon },
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(svc),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Cert Expiry")).toBeInTheDocument()
    })
    const value = screen.getByText("Cert Expiry").closest("div")!.querySelector("dd")!
    expect(value.className).toBe("text-yellow-600 font-medium")
  })

  it("conveys health-check status with a text alternative, not color alone", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Nextcloud")).toBeInTheDocument())

    expect(screen.getAllByRole("img", { name: "Passing" }).length).toBeGreaterThan(0)
    expect(screen.getByRole("img", { name: "Failing" })).toBeInTheDocument()
  })

  it("exposes the logs tabs with ARIA tab semantics", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Nextcloud")).toBeInTheDocument())

    const edgeTab = screen.getByRole("tab", { name: "Edge Logs" })
    const eventsTab = screen.getByRole("tab", { name: "Events" })
    expect(edgeTab).toHaveAttribute("aria-selected", "true")
    expect(eventsTab).toHaveAttribute("aria-selected", "false")
    expect(screen.getByRole("tabpanel")).toBeInTheDocument()

    fireEvent.click(eventsTab)
    expect(eventsTab).toHaveAttribute("aria-selected", "true")
    expect(edgeTab).toHaveAttribute("aria-selected", "false")
  })

  it("supports roving tabindex and arrow-key navigation on the logs tablist", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Nextcloud")).toBeInTheDocument())

    const edgeTab = screen.getByRole("tab", { name: "Edge Logs" })
    const eventsTab = screen.getByRole("tab", { name: "Events" })

    // Only the selected tab is in the tab sequence (roving tabindex).
    expect(edgeTab).toHaveAttribute("tabindex", "0")
    expect(eventsTab).toHaveAttribute("tabindex", "-1")

    // ArrowRight moves selection/focus to the next tab.
    edgeTab.focus()
    fireEvent.keyDown(edgeTab, { key: "ArrowRight" })
    expect(eventsTab).toHaveAttribute("aria-selected", "true")
    expect(edgeTab).toHaveAttribute("aria-selected", "false")
    expect(eventsTab).toHaveAttribute("tabindex", "0")
    expect(eventsTab).toHaveFocus()

    // ArrowRight wraps around to the first tab.
    fireEvent.keyDown(eventsTab, { key: "ArrowRight" })
    expect(edgeTab).toHaveAttribute("aria-selected", "true")
    expect(edgeTab).toHaveFocus()

    // ArrowLeft wraps back to the last tab; Home/End jump to the ends.
    fireEvent.keyDown(edgeTab, { key: "ArrowLeft" })
    expect(eventsTab).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(eventsTab, { key: "Home" })
    expect(edgeTab).toHaveAttribute("aria-selected", "true")
    fireEvent.keyDown(edgeTab, { key: "End" })
    expect(eventsTab).toHaveAttribute("aria-selected", "true")
  })

  it("humanizes an in-progress reconcile phase with an in-progress pill (ARCH-UX-3)", async () => {
    const inProgress = makeService({
      status: makeServiceStatus({ phase: "ensuring_cert", message: "Checking certificate" }),
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(inProgress),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Nextcloud")).toBeInTheDocument())

    // The humanized label (not raw snake_case) appears both in the header badge
    // and in the Runtime "Phase" row.
    const labels = screen.getAllByText("Ensuring certificate")
    expect(labels.length).toBeGreaterThanOrEqual(1)
    // The header status badge is styled as the blue "working" pill, not the
    // neutral grey fallback.
    const badge = labels.find((el) => el.classList.contains("bg-blue-100"))
    expect(badge).toBeDefined()
    expect(badge).toHaveClass("bg-blue-100", "text-blue-700")
  })
})
