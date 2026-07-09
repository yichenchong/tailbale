import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ServiceDetail from "@/pages/ServiceDetail"
import { mockService, renderWithRoute, renewFetchMock, renewPosts } from "./serviceDetailTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("ServiceDetail page - actions", () => {
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

  it("moves focus into the force-renew dialog when it opens", async () => {
    const fetchMock = renewFetchMock({ refused: "Certificate is healthy; not renewed.", forced: "Renewal triggered." })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Renew certificate")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Renew certificate"))
      const { promise, resolve } = Promise.withResolvers<void>()
      setTimeout(resolve, 0)
      await promise
    })
    expect(screen.getByText("Force certificate renewal?")).toBeInTheDocument()

    // Focus must land inside the dialog (on its first focusable = Cancel).
    const dialog = screen.getByRole("dialog")
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
  })

  it("closes the force-renew dialog on Escape and restores focus to the trigger", async () => {
    const fetchMock = renewFetchMock({ refused: "Certificate is healthy; not renewed.", forced: "Renewal triggered." })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Renew certificate")).toBeInTheDocument())

    // jsdom clicks don't move focus, so focus the trigger explicitly first.
    const trigger = screen.getByText("Renew certificate").closest("button")!
    trigger.focus()

    await act(async () => {
      fireEvent.click(trigger)
      const { promise, resolve } = Promise.withResolvers<void>()
      setTimeout(resolve, 0)
      await promise
    })
    expect(screen.getByText("Force certificate renewal?")).toBeInTheDocument()

    fireEvent.keyDown(document, { key: "Escape" })
    await waitFor(() =>
      expect(screen.queryByText("Force certificate renewal?")).not.toBeInTheDocument()
    )
    expect(document.activeElement).toBe(trigger)
  })

  it("traps Tab focus within the force-renew dialog", async () => {
    const fetchMock = renewFetchMock({ refused: "Certificate is healthy; not renewed.", forced: "Renewal triggered." })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Renew certificate")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Renew certificate"))
      const { promise, resolve } = Promise.withResolvers<void>()
      setTimeout(resolve, 0)
      await promise
    })
    expect(screen.getByText("Force certificate renewal?")).toBeInTheDocument()

    const dialog = screen.getByRole("dialog")
    const cancel = within(dialog).getByRole("button", { name: "Cancel" })
    const force = within(dialog).getByRole("button", { name: "Force renew" })

    force.focus()
    expect(document.activeElement).toBe(force)
    fireEvent.keyDown(document, { key: "Tab" })
    expect(document.activeElement).toBe(cancel)

    cancel.focus()
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(force)
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

  it("restores focus to the Renew button after the force-renew dialog closes even when renewing blurred the trigger", async () => {
    // In a real browser the async renew disables (and so blurs) the focused
    // "Renew certificate" trigger before the modal opens, moving activeElement
    // to <body>. jsdom doesn't blur on disable, so we simulate it. The dialog
    // must still return focus to the Renew button — not <body> — on close.
    const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
    const renewGate = Promise.withResolvers<{ ok: boolean; json: () => Promise<unknown> }>()
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
      }
      if (init?.method === "POST" && String(url).includes("/renew-cert")) {
        return renewGate.promise
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Renew certificate")).toBeInTheDocument())

    const trigger = screen.getByText("Renew certificate").closest("button")!
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    // Start the renew: the button disables while the request is in flight.
    fireEvent.click(trigger)
    expect(trigger).toBeDisabled()
    // Simulate the browser moving focus off the now-disabled trigger (to <body>
    // in a real browser; jsdom won't blur a disabled element, so move focus to
    // another control explicitly).
    const other = screen.getByText("Re-run Reconcile").closest("button")!
    act(() => other.focus())
    expect(document.activeElement).not.toBe(trigger)

    // The refused (needs_force) response opens the modal.
    await act(async () => {
      renewGate.resolve({
        ok: true,
        json: () =>
          Promise.resolve({ success: true, performed: false, needs_force: true, message: "healthy", expires_at: null, last_failure: null }),
      })
      const settle = Promise.withResolvers<void>()
      setTimeout(settle.resolve, 0)
      await settle.promise
    })
    expect(screen.getByText("Force certificate renewal?")).toBeInTheDocument()

    fireEvent.keyDown(document, { key: "Escape" })
    await waitFor(() =>
      expect(screen.queryByText("Force certificate renewal?")).not.toBeInTheDocument()
    )
    expect(document.activeElement).toBe(trigger)
  })
})
