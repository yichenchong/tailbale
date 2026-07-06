import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, fireEvent, screen, waitFor } from "@testing-library/react"
import { renderRoute } from "./testkit"

const mockServiceData = {
  services: [
    {
      id: "svc_abc123",
      name: "Nextcloud",
      enabled: true,
      upstream_container_name: "nextcloud",
      upstream_port: 80,
      hostname: "nextcloud.example.com",
      status: {
        phase: "healthy",
        message: null,
        tailscale_ip: "100.64.0.1",
        edge_container_id: null,
        last_reconciled_at: null,
        health_checks: null,
        cert_expires_at: "2026-08-01T00:00:00",
      },
      base_domain: "example.com",
      upstream_container_id: "c123",
      upstream_scheme: "http",
      edge_container_name: "edge_nextcloud",
      network_name: "edge_net_nextcloud",
      ts_hostname: "edge-nextcloud",
      preserve_host_header: true,
      created_at: "2026-04-05T00:00:00",
      updated_at: "2026-04-05T00:00:00",
    },
  ],
  total: 1,
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Services page", () => {
  it("shows loading state initially", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    expect(screen.getByText("Loading services...")).toBeInTheDocument()
  })

  it("renders empty state when no services", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ services: [], total: 0 }),
    }))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByText("No services exposed yet.")).toBeInTheDocument()
    })
  })

  it("shows a load error instead of an empty state when services fail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.resolve({ detail: "database unavailable" }),
    }))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByText("Unable to load services: database unavailable")).toBeInTheDocument()
    })
    expect(screen.queryByText("No services exposed yet.")).not.toBeInTheDocument()
    // The load error is injected asynchronously and must announce to assistive
    // tech via a live region (role="alert").
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Unable to load services: database unavailable"
    )
  })

  it("renders service list with data", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockServiceData),
    }))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    expect(screen.getByText("healthy")).toBeInTheDocument()
  })

  it("renders edge IP column", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockServiceData),
    }))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByText("100.64.0.1")).toBeInTheDocument()
    })
  })

  it("renders cert expiry column", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockServiceData),
    }))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      // The date is formatted by toLocaleDateString(), just verify the column header exists
      expect(screen.getByText("Cert Expiry")).toBeInTheDocument()
    })
  })

  it("renders actions menu trigger", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockServiceData),
    }))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByLabelText("Actions")).toBeInTheDocument()
    })
  })

  it("hides edge actions for disabled services", async () => {
    const disabledData = {
      ...mockServiceData,
      services: [
        {
          ...mockServiceData.services[0],
          enabled: false,
          status: { ...mockServiceData.services[0].status, phase: "disabled" },
        },
      ],
    }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(disabledData),
    }))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByLabelText("Actions")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText("Actions"))

    expect(screen.queryByText("Reload Caddy")).not.toBeInTheDocument()
    expect(screen.queryByText("Restart Edge")).not.toBeInTheDocument()
    expect(screen.queryByText("Recreate Edge")).not.toBeInTheDocument()
    expect(screen.getByText("Enable")).toBeInTheDocument()
  })

  it("encodes service ids when linking and running row actions", async () => {
    const data = {
      ...mockServiceData,
      services: [{ ...mockServiceData.services[0], id: "svc/abc 123" }],
    }
    const fetchMock = vi.fn((url: string, init?: RequestInit) => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(init?.method === "POST" ? { success: true } : data),
    }))
    vi.stubGlobal("fetch", fetchMock)
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByLabelText("Actions")).toBeInTheDocument()
    })

    expect(screen.getByRole("link", { name: "Nextcloud" })).toHaveAttribute(
      "href",
      "/services/svc%2Fabc%20123",
    )
    fireEvent.click(screen.getByLabelText("Actions"))
    expect(screen.getByRole("link", { name: "View Details" })).toHaveAttribute(
      "href",
      "/services/svc%2Fabc%20123",
    )
    fireEvent.click(screen.getByText("Reload Caddy"))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/services/svc%2Fabc%20123/reload", expect.objectContaining({ method: "POST" }))
    })
  })

  it("shows Expose New Service button", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ services: [], total: 0 }),
    }))
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByText("Discover Containers")).toBeInTheDocument()
    })
  })

  it("ignores stale service reload responses after a newer action refresh wins", async () => {
    let resolveFirstPost!: (value: Response) => void
    let resolveSecondPost!: (value: Response) => void
    let resolveFirstReload!: (value: Response) => void
    let resolveSecondReload!: (value: Response) => void
    const oldData = mockServiceData
    const newData = {
      ...mockServiceData,
      services: [{ ...mockServiceData.services[0], name: "Newcloud" }],
    }
    let actionPhase = false
    let getRequestCount = 0
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (!init?.method || init.method === "GET") {
        if (!actionPhase) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(oldData),
          } as Response)
        }
        getRequestCount += 1
        if (getRequestCount === 1) {
          return new Promise<Response>((resolve) => {
            resolveFirstReload = resolve
          })
        }
        return new Promise<Response>((resolve) => {
          resolveSecondReload = resolve
        })
      }
      if (String(url).includes("/reload")) {
        return new Promise<Response>((resolve) => {
          resolveFirstPost = resolve
        })
      }
      return new Promise<Response>((resolve) => {
        resolveSecondPost = resolve
      })
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    actionPhase = true

    fireEvent.click(screen.getByLabelText("Actions"))
    fireEvent.click(screen.getByText("Reload Caddy"))
    fireEvent.click(screen.getByLabelText("Actions"))
    fireEvent.click(screen.getByText("Restart Edge"))

    await act(async () => {
      resolveFirstPost({
        ok: true,
        json: () => Promise.resolve({ success: true }),
      } as Response)
    })
    await waitFor(() => expect(getRequestCount).toBe(1))

    await act(async () => {
      resolveSecondPost({
        ok: true,
        json: () => Promise.resolve({ success: true }),
      } as Response)
    })
    await waitFor(() => expect(getRequestCount).toBe(2))

    await act(async () => {
      resolveSecondReload({
        ok: true,
        json: () => Promise.resolve(newData),
      } as Response)
    })
    await waitFor(() => {
      expect(screen.getByText("Newcloud")).toBeInTheDocument()
    })

    await act(async () => {
      resolveFirstReload({
        ok: true,
        json: () => Promise.resolve(oldData),
      } as Response)
    })
    expect(screen.getByText("Newcloud")).toBeInTheDocument()
    expect(screen.queryByText("Nextcloud")).not.toBeInTheDocument()
  })

  it("keeps newer action errors visible when an older clear timer expires", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (!init?.method || init.method === "GET") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockServiceData),
        } as Response)
      }
      return Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({
          detail: String(url).includes("/reload") ? "first failure" : "second failure",
        }),
      } as Response)
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByLabelText("Actions")).toBeInTheDocument()
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

    fireEvent.click(screen.getByLabelText("Actions"))
    fireEvent.click(screen.getByText("Reload Caddy"))
    await flushAction()
    expect(screen.getByText("first failure")).toBeInTheDocument()
    // The action-feedback banner (carrying failure messages) is injected
    // asynchronously and must announce via a polite live region (role="status").
    expect(screen.getByRole("status")).toHaveTextContent("first failure")
    const firstActionTimer = timers.at(-1)!

    fireEvent.click(screen.getByLabelText("Actions"))
    fireEvent.click(screen.getByText("Restart Edge"))
    await flushAction()
    expect(screen.getByText("second failure")).toBeInTheDocument()

    const secondActionTimer = timers.at(-1)!
    expect(firstActionTimer.cleared).toBe(true)
    await act(async () => {
      if (!firstActionTimer.cleared) firstActionTimer.handler()
    })
    expect(screen.getByText("second failure")).toBeInTheDocument()

    await act(async () => {
      secondActionTimer.handler()
    })
    expect(screen.queryByText("second failure")).not.toBeInTheDocument()
  })

  it("buckets cert expiry days using UTC for offset-less timestamps", async () => {
    // Backend cert_expires_at serializes naive (no offset) but means UTC.
    // Forcing a +09:00 host makes a raw `new Date()` parse drop the count by a
    // day and cross the 14-day warning threshold; parseBackendDate must not.
    const originalTz = process.env.TZ
    process.env.TZ = "Asia/Tokyo"
    try {
      const { formatCertExpiry } = await import("@/lib/certStatus")
      const naive = new Date(Date.now() + 14.25 * 86400000)
        .toISOString()
        .replace("Z", "")
      // 14.25 days out -> ceil = 15 -> outside the <=14 warning bucket.
      expect(formatCertExpiry(naive, "UTC").style).toBe("text-zinc-500")
    } finally {
      if (originalTz === undefined) delete process.env.TZ
      else process.env.TZ = originalTz
    }
  })

  it("marks a cert expired within the last 24h as expired, not 'expiring soon'", async () => {
    // A cert that lapsed <24h ago has Math.ceil(diffMs/day) === 0; gating
    // "expired" on the day count alone would mis-bucket it as yellow.
    const { formatCertExpiry } = await import("@/lib/certStatus")
    const justExpired = new Date(Date.now() - 12 * 3600000).toISOString()
    expect(formatCertExpiry(justExpired, "UTC").style).toBe("text-red-600 font-medium")
  })

  it("keeps the live list visible when a post-action reload fails", async () => {
    // Regression: load() is reused for post-action reloads; a transient failure
    // there must NOT wipe the already-rendered table (only the initial mount
    // load clears the list). The error surfaces non-destructively instead.
    let reloadShouldFail = false
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (!init?.method || init.method === "GET") {
        if (reloadShouldFail) {
          return Promise.resolve({
            ok: false,
            status: 500,
            json: () => Promise.resolve({ detail: "reload failed" }),
          } as Response)
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockServiceData),
        } as Response)
      }
      // The action POST itself succeeds; the follow-up reload is what fails.
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ success: true }),
      } as Response)
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })

    reloadShouldFail = true
    fireEvent.click(screen.getByLabelText("Actions"))
    fireEvent.click(screen.getByText("Reload Caddy"))

    await waitFor(() => {
      expect(screen.getByText(/Unable to refresh services: reload failed/)).toBeInTheDocument()
    })
    // The row survives the failed background reload, and we never fall back to
    // the empty/full-error takeover states.
    expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    expect(screen.queryByText("No services exposed yet.")).not.toBeInTheDocument()
    expect(screen.queryByText(/Unable to load services/)).not.toBeInTheDocument()
  })

  it("cleans up the DNS record when deleting a service from the row menu", async () => {
    // Regression: the row-menu delete used to omit cleanup_dns, silently leaving
    // an orphaned Cloudflare DNS record (the detail page defaults to cleanup).
    const fetchMock = vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(init?.method === "DELETE" ? {} : mockServiceData),
      } as Response)
    )
    vi.stubGlobal("fetch", fetchMock)
    vi.spyOn(window, "confirm").mockReturnValue(true)
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByLabelText("Actions")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText("Actions"))
    await act(async () => {
      fireEvent.click(screen.getByText("Delete"))
      await new Promise((r) => setTimeout(r, 0))
    })

    const delCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "DELETE"
    )
    expect(delCall?.[0]).toBe("/api/services/svc_abc123?cleanup_dns=true")
  })

  it("does not fire a DELETE when the row-menu delete confirmation is declined", async () => {
    // The window.confirm gate guards a destructive, irreversible delete: when the
    // user cancels, handleDelete must bail before touching the API and leave the
    // row in place.
    const fetchMock = vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve(init?.method === "DELETE" ? {} : mockServiceData),
      } as Response)
    )
    vi.stubGlobal("fetch", fetchMock)
    vi.spyOn(window, "confirm").mockReturnValue(false)
    const { default: Services } = await import("@/pages/Services")
    renderRoute(<Services />)
    await waitFor(() => {
      expect(screen.getByLabelText("Actions")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByLabelText("Actions"))
    fireEvent.click(screen.getByText("Delete"))

    expect(window.confirm).toHaveBeenCalled()
    expect(
      fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "DELETE")
    ).toBe(false)
    // The row survives a cancelled delete.
    expect(screen.getByText("Nextcloud")).toBeInTheDocument()
  })
})
