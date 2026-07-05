import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import { renderRoute } from "./testkit"
import { makeService } from "./factories"

const mockService = makeService()

beforeEach(() => {
  vi.restoreAllMocks()
})

function renderWithRoute(path: string) {
  return import("@/pages/ServiceDetail").then(({ default: ServiceDetail }) => {
    renderRoute(<ServiceDetail />, { path: "/services/:id", initialEntries: [path] })
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

  // The Health Checks grid maps each backend check key to a human label via
  // CHECK_LABELS. The backend emits exactly these 12 keys (CRITICAL_CHECKS ∪
  // WARNING_CHECKS in backend/app/health/health_checker.py). If a label is ever
  // dropped/renamed, the page silently falls back to rendering the raw
  // snake_case key — this pins the full mapping so that drift fails loudly.
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
    expect(screen.getByText("Renew certificate")).toBeInTheDocument()
    expect(screen.getByText("Re-run Reconcile")).toBeInTheDocument()
  })

  it("hides edge mutation actions when disabled", async () => {
    const disabledService = {
      ...mockService,
      enabled: false,
      status: { ...mockService.status, phase: "disabled" },
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(disabledService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Enable")).toBeInTheDocument()
    })

    expect(screen.queryByText("Reload Caddy")).not.toBeInTheDocument()
    expect(screen.queryByText("Restart Edge")).not.toBeInTheDocument()
    expect(screen.queryByText("Recreate Edge")).not.toBeInTheDocument()
    expect(screen.queryByText("Update Edge")).not.toBeInTheDocument()
    expect(screen.getByText("Renew certificate")).toBeInTheDocument()
    expect(screen.getByText("Re-run Reconcile")).toBeInTheDocument()
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

  it("prevents edit saves that violate backend constraints", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Edit")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Edit"))
    const save = screen.getByText("Save")
    const port = screen.getByLabelText("Upstream Port")
    const name = screen.getByLabelText("Name")

    fireEvent.change(port, { target: { value: "70000" } })
    expect(save).toBeDisabled()

    fireEvent.change(port, { target: { value: "443" } })
    expect(save).toBeEnabled()

    fireEvent.change(name, { target: { value: "   " } })
    expect(save).toBeDisabled()
  })

  it("sends trimmed valid edit values with numeric port", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ...mockService, name: "Cloud", upstream_port: 443 }),
        })
      }
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockService),
      })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Edit")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Edit"))
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  Cloud  " } })
    fireEvent.change(screen.getByLabelText("Upstream Port"), { target: { value: "443" } })
    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/services/svc_abc123", expect.objectContaining({ method: "PUT" }))
    })
    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")
    expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({
      name: "Cloud",
      upstream_port: 443,
    })
  })

  it("ignores stale detail refreshes after navigating to another service", async () => {
    const oldService = { ...mockService, id: "svc_old", name: "Oldcloud" }
    const newService = { ...mockService, id: "svc_new", name: "Newcloud" }
    let resolveOld!: (value: { ok: boolean; json: () => Promise<typeof oldService> }) => void
    const edgeVersion = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string) => {
      if (url === "/api/services/svc_old") {
        return new Promise((resolve) => { resolveOld = resolve })
      }
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVersion) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(newService) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ServiceDetail } = await import("@/pages/ServiceDetail")
    render(
      <MemoryRouter initialEntries={["/services/svc_old"]}>
        <Link to="/services/svc_new">Go new</Link>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetail />} />
        </Routes>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByText("Go new"))
    await waitFor(() => {
      expect(screen.getByText("Newcloud")).toBeInTheDocument()
    })

    resolveOld({ ok: true, json: () => Promise.resolve(oldService) })
    await Promise.resolve()
    await Promise.resolve()
    expect(screen.queryByText("Oldcloud")).not.toBeInTheDocument()
    expect(screen.getByText("Newcloud")).toBeInTheDocument()
  })

  it("keeps newer detail action errors visible when an older clear timer expires", async () => {
    const edgeVersion = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (!init?.method || init.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(url.endsWith("/edge-version") ? edgeVersion : mockService),
        } as Response)
      }
      return Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({
          detail: String(url).includes("/reload") ? "first detail failure" : "second detail failure",
        }),
      } as Response)
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Reload Caddy")).toBeInTheDocument()
    })
    const timers: Array<{ handler: () => void; cleared: boolean }> = []
    vi.spyOn(globalThis, "setTimeout").mockImplementation((handler: TimerHandler) => {
      timers.push({ handler: handler as () => void, cleared: false })
      return (timers.length - 1) as unknown as ReturnType<typeof setTimeout>
    })
    vi.spyOn(globalThis, "clearTimeout").mockImplementation((id) => {
      const timer = timers[Number(id)]
      if (timer) timer.cleared = true
    })
    const flushAction = async () => {
      await act(async () => {
        for (let i = 0; i < 6; i++) await Promise.resolve()
      })
    }

    fireEvent.click(screen.getByText("Reload Caddy"))
    await flushAction()
    expect(screen.getByText("first detail failure")).toBeInTheDocument()
    const firstActionTimer = timers.at(-1)!

    fireEvent.click(screen.getByText("Restart Edge"))
    await flushAction()
    expect(screen.getByText("second detail failure")).toBeInTheDocument()

    const secondActionTimer = timers.at(-1)!
    expect(firstActionTimer.cleared).toBe(true)
    await act(async () => {
      if (!firstActionTimer.cleared) firstActionTimer.handler()
    })
    expect(screen.getByText("second detail failure")).toBeInTheDocument()

    await act(async () => {
      secondActionTimer.handler()
    })
    expect(screen.queryByText("second detail failure")).not.toBeInTheDocument()
  })

  it("closes confirmation dialogs when navigating between services", async () => {
    const newService = { ...mockService, id: "svc_new", name: "Newcloud" }
    const edgeVersion = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVersion) })
      }
      const service = url === "/api/services/svc_new" ? newService : mockService
      return Promise.resolve({ ok: true, json: () => Promise.resolve(service) })
    }))

    const { default: ServiceDetail } = await import("@/pages/ServiceDetail")
    render(
      <MemoryRouter initialEntries={["/services/svc_abc123"]}>
        <Link to="/services/svc_new">Go new</Link>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetail />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText("Delete"))
    expect(screen.getByText('Delete "Nextcloud"?')).toBeInTheDocument()

    fireEvent.click(screen.getByText("Go new"))
    await waitFor(() => {
      expect(screen.getByText("Newcloud")).toBeInTheDocument()
    })
    expect(screen.queryByText('Delete "Nextcloud"?')).not.toBeInTheDocument()
    expect(screen.queryByText('Delete "Newcloud"?')).not.toBeInTheDocument()
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
          ...mockService.status,
          health_checks: { ...mockService.status.health_checks, https_probe_ok: false },
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
    const { formatDateTime, _resetTimezoneCache } = await import("@/lib/useTimezone")
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

  it("discards an in-flight reload from a prior action so it cannot clobber a save", async () => {
    // Repro of the last-writer race: a slow reload kicked off by a prior action
    // (e.g. "Re-run Reconcile") must not overwrite the fresh service returned by
    // a save that resolves first.
    let resolveReload!: (value: { ok: boolean; json: () => Promise<unknown> }) => void
    let resolvePut!: (value: { ok: boolean; json: () => Promise<unknown> }) => void
    const fresh = { ...mockService, name: "Freshname" }
    const stale = { ...mockService, name: "Stalename" }
    const edgeVersion = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    // The reconcile action sets this so the detail GET it triggers is held
    // open; every mount / earlier detail GET resolves immediately. Keyed on the
    // action rather than call-count so it's robust to how many loads mount fires.
    let holdReload = false
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVersion) })
      }
      if (init?.method === "PUT") {
        return new Promise((resolve) => { resolvePut = resolve })
      }
      if (init?.method === "POST" && url.endsWith("/reconcile")) {
        holdReload = true
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      // GET of the detail endpoint: hold open only the reload the reconcile
      // action kicks off; the mount load(s) resolve immediately.
      if (holdReload) {
        return new Promise((resolve) => { resolveReload = resolve })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Edit")).toBeInTheDocument())

    // Begin a save (PUT left in flight).
    fireEvent.click(screen.getByText("Edit"))
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Freshname" } })
    fireEvent.click(screen.getByText("Save"))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true)
    )

    // While the save is pending, a prior-style action kicks off a reload.
    fireEvent.click(screen.getByText("Re-run Reconcile"))
    await waitFor(() => expect(resolveReload).toBeDefined())

    // The save resolves first with fresh data...
    await act(async () => {
      resolvePut({ ok: true, json: () => Promise.resolve(fresh) })
      await new Promise((r) => setTimeout(r, 0))
    })
    // ...then the stale reload resolves last and must be ignored.
    await act(async () => {
      resolveReload({ ok: true, json: () => Promise.resolve(stale) })
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(screen.getByText("Freshname")).toBeInTheDocument()
    expect(screen.queryByText("Stalename")).not.toBeInTheDocument()
  })

  it("keeps the page and an open edit form intact across a background post-action refresh", async () => {
    const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
      }
      if (init?.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Edit")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Edit"))
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "WorkInProgress" } })

    // A post-action refresh (Re-run Reconcile -> POST then a background reload)
    // must not blank the page to the spinner nor reseed the open edit form.
    await act(async () => {
      fireEvent.click(screen.getByText("Re-run Reconcile"))
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(screen.queryByText("Loading...")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Name")).toHaveValue("WorkInProgress")
  })

  it("disables Save and shows inline feedback when the edited name exceeds 128 chars", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Edit")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Edit"))
    const save = screen.getByText("Save")
    expect(save).toBeEnabled()

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "a".repeat(129) } })
    expect(save).toBeDisabled()
    expect(screen.getByText("Service name must be 128 characters or fewer.")).toBeInTheDocument()
  })

  it("deletes with DNS cleanup and navigates to the services list on success", async () => {
    const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
      }
      if (init?.method === "DELETE") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: ServiceDetail } = await import("@/pages/ServiceDetail")
    render(
      <MemoryRouter initialEntries={["/services/svc_abc123"]}>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetail />} />
          <Route path="/services" element={<div>Services List</div>} />
        </Routes>
      </MemoryRouter>
    )
    await waitFor(() => expect(screen.getByText("Delete")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Delete")) // open confirm (cleanup checked by default)
    await act(async () => {
      fireEvent.click(screen.getByText("Delete Service"))
      await new Promise((r) => setTimeout(r, 0))
    })

    await waitFor(() => expect(screen.getByText("Services List")).toBeInTheDocument())
    const delCall = fetchMock.mock.calls.find(([, init]) => init?.method === "DELETE")
    expect(delCall?.[0]).toBe("/api/services/svc_abc123?cleanup_dns=true")
  })

  it("disables an enabled service via POST /disable", async () => {
    const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
      }
      if (init?.method === "POST" && url.endsWith("/disable")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...mockService, enabled: false }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Disable")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Disable")) // open confirm
    await act(async () => {
      fireEvent.click(screen.getByText("Disable")) // confirm -> handleToggleEnabled
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/services/svc_abc123/disable",
      expect.objectContaining({ method: "POST" })
    )
  })

  it("enables a disabled service via PUT { enabled: true }", async () => {
    const disabledService = { ...mockService, enabled: false }
    const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
      }
      if (init?.method === "PUT") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...disabledService, enabled: true }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(disabledService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Enable")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Enable")) // no confirm when enabling
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/services/svc_abc123",
      expect.objectContaining({ method: "PUT" })
    )
    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")
    expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({ enabled: true })
  })

  it("requires confirmation before recreating the edge (no immediate POST)", async () => {
    const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
      }
      if (init?.method === "POST" && url.endsWith("/recreate-edge")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Recreate Edge")).toBeInTheDocument())

    const recreatePosts = () =>
      fetchMock.mock.calls.filter(
        ([url, init]) =>
          String(url).endsWith("/recreate-edge") && (init as RequestInit | undefined)?.method === "POST"
      )

    // Recreate causes downtime, so the first click only opens the confirm and
    // must NOT fire the destructive POST (unlike the Services row menu, which
    // intentionally fires immediately).
    fireEvent.click(screen.getByText("Recreate Edge"))
    expect(screen.getByText(/Recreate edge\? This will cause brief downtime\./)).toBeInTheDocument()
    expect(recreatePosts()).toHaveLength(0)

    // Confirming fires exactly one POST and closes the prompt.
    await act(async () => {
      fireEvent.click(screen.getByText("Recreate"))
    })
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/services/svc_abc123/recreate-edge",
        expect.objectContaining({ method: "POST" })
      )
    )
    expect(recreatePosts()).toHaveLength(1)
    expect(screen.queryByText(/Recreate edge\? This will cause brief downtime\./)).not.toBeInTheDocument()
  })

  // Renew cert: shared fetch mock that answers the renew endpoint based on the
  // `force` query param, mirroring the backend contract.
  function renewFetchMock(messages: { refused: string; forced: string }) {
    const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    return vi.fn((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
      }
      if (init?.method === "POST" && String(url).includes("/renew-cert")) {
        const forced = String(url).includes("force=true")
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              success: true,
              performed: forced,
              needs_force: !forced,
              message: forced ? messages.forced : messages.refused,
              expires_at: forced ? "2026-09-01T00:00:00" : null,
              last_failure: null,
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
  }

  const renewPosts = (mock: ReturnType<typeof vi.fn>) =>
    mock.mock.calls.filter(
      ([url, init]) => String(url).includes("/renew-cert") && (init as RequestInit | undefined)?.method === "POST"
    )

  it("opens the force-renew modal (without forcing) when a healthy cert refuses renewal", async () => {
    const fetchMock = renewFetchMock({ refused: "Certificate is healthy; not renewed.", forced: "Renewal triggered." })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Renew certificate")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Renew certificate"))
      await new Promise((r) => setTimeout(r, 0))
    })

    // Exactly one renew POST so far, and it carried no force flag.
    const posts = renewPosts(fetchMock)
    expect(posts).toHaveLength(1)
    expect(String(posts[0][0])).toBe("/api/services/svc_abc123/renew-cert")
    // The scary modal is shown instead of silently forcing.
    expect(screen.getByText("Force certificate renewal?")).toBeInTheDocument()
    expect(screen.getByText(/Let's Encrypt/)).toBeInTheDocument()
  })

  it("posts force=true and closes the modal when the user confirms a force renew", async () => {
    const fetchMock = renewFetchMock({ refused: "Certificate is healthy; not renewed.", forced: "Renewal triggered." })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Renew certificate")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Renew certificate"))
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(screen.getByText("Force certificate renewal?")).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(screen.getByText("Force renew"))
      await new Promise((r) => setTimeout(r, 0))
    })

    const forced = fetchMock.mock.calls.filter(
      ([url, init]) => String(url).includes("/renew-cert?force=true") && (init as RequestInit | undefined)?.method === "POST"
    )
    expect(forced).toHaveLength(1)
    expect(renewPosts(fetchMock)).toHaveLength(2) // initial refused + forced
    await waitFor(() => expect(screen.queryByText("Force certificate renewal?")).not.toBeInTheDocument())
    expect(screen.getByText("Renewal triggered.")).toBeInTheDocument()
  })

  it("does not post force=true when the force-renew modal is cancelled", async () => {
    const fetchMock = renewFetchMock({ refused: "Certificate is healthy; not renewed.", forced: "Renewal triggered." })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Renew certificate")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Renew certificate"))
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(screen.getByText("Force certificate renewal?")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Cancel"))
    expect(screen.queryByText("Force certificate renewal?")).not.toBeInTheDocument()

    // Still just the single non-force POST; cancelling fired nothing further.
    const posts = renewPosts(fetchMock)
    expect(posts).toHaveLength(1)
    expect(posts.some(([url]) => String(url).includes("force=true"))).toBe(false)
  })

  it("renews immediately with no modal when the cert is near expiry (performed)", async () => {
    const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
      }
      if (init?.method === "POST" && String(url).includes("/renew-cert")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              success: true,
              performed: true,
              needs_force: false,
              message: "Certificate renewal started.",
              expires_at: "2026-09-01T00:00:00",
              last_failure: null,
            }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Renew certificate")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Renew certificate"))
      await new Promise((r) => setTimeout(r, 0))
    })

    // No modal, success message shown, exactly one (non-force) POST.
    expect(screen.queryByText("Force certificate renewal?")).not.toBeInTheDocument()
    expect(screen.getByText("Certificate renewal started.")).toBeInTheDocument()
    const posts = renewPosts(fetchMock)
    expect(posts).toHaveLength(1)
    expect(String(posts[0][0])).toBe("/api/services/svc_abc123/renew-cert")
  })

  it("shows the outdated-edge banner and updates the edge container", async () => {
    const outdated = { orchestrator_version: "2.0.0", edge_version: "1.0.0", up_to_date: false }
    const updated = { orchestrator_version: "2.0.0", edge_version: "2.0.0", up_to_date: true }
    let edgeCalls = 0
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/edge-version")) {
        edgeCalls += 1
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeCalls === 1 ? outdated : updated) })
      }
      if (init?.method === "POST" && String(url).endsWith("/update-edge")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Update Edge")).toBeInTheDocument())
    expect(screen.getByText(/Edge container is outdated/)).toBeInTheDocument()

    await act(async () => {
      fireEvent.click(screen.getByText("Update Edge"))
      await new Promise((r) => setTimeout(r, 0))
    })

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/services/svc_abc123/update-edge",
      expect.objectContaining({ method: "POST" })
    )
    // After updating, the edge reports up-to-date and the button/banner clear.
    await waitFor(() => expect(screen.queryByText("Update Edge")).not.toBeInTheDocument())
  })

  it("reseeds the edit form from the new service after navigating away from an open edit form", async () => {
    // Guards the useServiceDetail id-change seed race: navigating with an edit
    // form OPEN must (a) close the form and (b) let the next service's response
    // reseed the fields — i.e. the editingRef guard must have flipped back to
    // false before the new id's detail response lands. If the guard stayed
    // stuck true, re-opening Edit would show the PREVIOUS service's values.
    const alpha = { ...mockService, id: "svc_alpha", name: "Alpha", upstream_port: 80 }
    const beta = { ...mockService, id: "svc_beta", name: "Beta", upstream_port: 8443 }
    const edgeVersion = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    vi.stubGlobal("fetch", vi.fn((url: string) => {
      if (String(url).endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVersion) })
      }
      const svc = String(url) === "/api/services/svc_beta" ? beta : alpha
      return Promise.resolve({ ok: true, json: () => Promise.resolve(svc) })
    }))

    const { default: ServiceDetail } = await import("@/pages/ServiceDetail")
    render(
      <MemoryRouter initialEntries={["/services/svc_alpha"]}>
        <Link to="/services/svc_beta">Go beta</Link>
        <Routes>
          <Route path="/services/:id" element={<ServiceDetail />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => expect(screen.getByText("Alpha")).toBeInTheDocument())
    // Open the edit form on Alpha and dirty the name so a stale reseed would be
    // visible; the field is seeded from svc_alpha.
    fireEvent.click(screen.getByText("Edit"))
    expect(screen.getByLabelText("Name")).toHaveValue("Alpha")
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "DirtyLocalEdit" } })

    // Navigate to Beta while the form is open.
    fireEvent.click(screen.getByText("Go beta"))
    await waitFor(() => expect(screen.getByText("Beta")).toBeInTheDocument())

    // The form closed on navigation (editing reset); re-open it. The fields MUST
    // reflect Beta, never the stale Alpha edit ("DirtyLocalEdit") nor "Alpha".
    fireEvent.click(screen.getByText("Edit"))
    expect(screen.getByLabelText("Name")).toHaveValue("Beta")
    expect(screen.getByLabelText("Upstream Port")).toHaveValue(8443)
  })

  it("allows saving a name valid by code points but >128 UTF-16 units (emoji), and sends it", async () => {
    // Regression (post lib/validation code-point migration): handleSave and the
    // inline hint must delegate to the shared code-point isServiceName, not a
    // raw String.length (UTF-16) check. A 65-emoji name is 65 code points
    // (backend-accepted) but 130 UTF-16 units — the old `.length > 128` guard
    // would block the PUT and flash a false "too long" error, disagreeing with
    // nameValid (which enables Save).
    const emojiName = "\u{1F600}".repeat(65) // 65 code points, 130 UTF-16 units
    expect(emojiName.length).toBeGreaterThan(128) // UTF-16 units
    expect([...emojiName].length).toBe(65) // code points
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }) })
      }
      if (init?.method === "PUT") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...mockService, name: emojiName }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Edit")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Edit"))
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: emojiName } })

    // No false "too long" hint, and Save stays enabled.
    expect(screen.queryByText("Service name must be 128 characters or fewer.")).not.toBeInTheDocument()
    expect(screen.getByText("Save")).toBeEnabled()

    fireEvent.click(screen.getByText("Save"))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true)
    )
    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")
    expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({ name: emojiName })
  })
})
