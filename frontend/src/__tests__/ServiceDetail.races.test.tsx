import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import ServiceDetail from "@/pages/ServiceDetail"
import { mockService, renderWithRoute } from "./serviceDetailTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("ServiceDetail page - races", () => {
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
    // The action-feedback banner (carrying failure messages) is injected
    // asynchronously and must announce via a polite live region (role="status").
    expect(screen.getByRole("status")).toHaveTextContent("first detail failure")
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
})
