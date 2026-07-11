import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, fireEvent, act } from "@testing-library/react"
import { renderRoute, mockApi } from "./testkit"
import { makeJob, makeJobDetails, makeSettings } from "./factories"

const mockJobs = {
  jobs: [
    makeJob(),
    makeJob({
      id: "job_def456",
      status: "failed",
      message: "Retry failed: API timeout",
      details: makeJobDetails({
        record_id: "cf_r2",
        hostname: "vaultwarden.example.com",
        value: "100.64.0.2",
        service_name: "Vaultwarden",
      }),
      created_at: "2026-04-07T10:00:00Z",
      updated_at: "2026-04-08T09:00:00Z",
    }),
  ],
  total: 2,
}

const mockSettings = makeSettings()

beforeEach(() => {
  vi.restoreAllMocks()
})

function mockFetch(data: unknown) {
  return mockApi([
    { url: "/settings", json: mockSettings },
    { json: data },
  ])
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

describe("OrphanDns page", () => {
  it("shows loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
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
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("Server error")).toBeInTheDocument()
    })
  })

  it("shows empty state when no orphan jobs", async () => {
    vi.stubGlobal("fetch", mockFetch({ jobs: [], total: 0 }))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(
        screen.getByText("No orphaned DNS records. All clean!")
      ).toBeInTheDocument()
    })
  })

  it("renders orphan job list with data", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(
        screen.getByText("nextcloud.example.com")
      ).toBeInTheDocument()
    })
    expect(screen.getByText("vaultwarden.example.com")).toBeInTheDocument()
  })

  it("shows service names", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("Nextcloud")).toBeInTheDocument()
    })
    expect(screen.getByText("Vaultwarden")).toBeInTheDocument()
  })

  it("shows record IDs", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("cf_r1")).toBeInTheDocument()
    })
    expect(screen.getByText("cf_r2")).toBeInTheDocument()
  })

  it("shows IP values", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("100.64.0.1")).toBeInTheDocument()
    })
    expect(screen.getByText("100.64.0.2")).toBeInTheDocument()
  })

  it("shows status badges", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("pending")).toBeInTheDocument()
    })
    expect(screen.getByText("failed")).toBeInTheDocument()
  })

  it("shows total count", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("2 orphaned records")).toBeInTheDocument()
    })
  })

  it("shows singular count for one record", async () => {
    const singleJob = { jobs: [mockJobs.jobs[0]], total: 1 }
    vi.stubGlobal("fetch", mockFetch(singleJob))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("1 orphaned record")).toBeInTheDocument()
    })
  })

  it("shows pagination controls only when total exceeds the page size", async () => {
    // Backend caps the jobs list; pagination must make every record reachable.
    const data = { jobs: mockJobs.jobs, total: 120 }
    vi.stubGlobal("fetch", mockFetch(data))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("Previous")).toBeInTheDocument()
    })
    expect(screen.getByText("Next")).toBeInTheDocument()
    expect(screen.getByText("1–50 of 120")).toBeInTheDocument()
    // First page: Previous disabled, Next enabled.
    expect(screen.getByText("Previous")).toBeDisabled()
    expect(screen.getByText("Next")).not.toBeDisabled()
  })

  it("does not show pagination controls when all records fit on one page", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs)) // total: 2 <= pageSize 50
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })
    expect(screen.queryByText("Previous")).not.toBeInTheDocument()
    expect(screen.queryByText("Next")).not.toBeInTheDocument()
    // No "Page 1 of 1" style range indicator either.
    expect(screen.queryByText(/of 2/)).not.toBeInTheDocument()
  })

  it("requests the right offset/limit when navigating pages", async () => {
    const fetchMock = mockFetch({ jobs: mockJobs.jobs, total: 120 })
    vi.stubGlobal("fetch", fetchMock)
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("Next")).toBeInTheDocument()
    })
    // Initial page requests offset 0, limit 50, with the kind filter intact.
    const firstCall = fetchMock.mock.calls.find((c: unknown[]) => String(c[0]).includes("/jobs"))
    expect(firstCall).toBeDefined()
    expect(String(firstCall![0])).toContain("kind=dns_orphan_cleanup")
    expect(String(firstCall![0])).toContain("limit=50")
    expect(String(firstCall![0])).toContain("offset=0")

    await act(async () => {
      fireEvent.click(screen.getByText("Next"))
    })
    await waitFor(() => {
      expect(screen.getByText("51–100 of 120")).toBeInTheDocument()
    })
    const nextCall = fetchMock.mock.calls.find((c: unknown[]) => String(c[0]).includes("offset=50"))
    expect(nextCall).toBeDefined()
    expect(String(nextCall![0])).toContain("kind=dns_orphan_cleanup")
    expect(String(nextCall![0])).toContain("limit=50")
  })

  it("has retry and dismiss buttons for each job", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })
    const retryButtons = screen.getAllByText("Retry Deletion")
    const dismissButtons = screen.getAllByText("Dismiss")
    expect(retryButtons).toHaveLength(2)
    expect(dismissButtons).toHaveLength(2)
  })

  it("shows page heading and description", async () => {
    vi.stubGlobal("fetch", mockFetch({ jobs: [], total: 0 }))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    expect(screen.getByText("Orphaned DNS Records")).toBeInTheDocument()
    expect(
      screen.getByText(/DNS records left in Cloudflare/)
    ).toBeInTheDocument()
  })

  it("shows failure message on failed jobs", async () => {
    vi.stubGlobal("fetch", mockFetch(mockJobs))
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(
        screen.getByText("Retry failed: API timeout")
      ).toBeInTheDocument()
    })
  })

  it("shows success message after retry", async () => {
    let jobCallCount = 0
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ success: true, message: "DNS record for 'nextcloud.example.com' cleaned up" }),
        })
      }
      // GET /jobs calls
      jobCallCount++
      if (jobCallCount === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockJobs) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ jobs: [mockJobs.jobs[1]], total: 1 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)

    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })

    const retryButtons = screen.getAllByText("Retry Deletion")
    fireEvent.click(retryButtons[0])

    await waitFor(() => {
      expect(
        screen.getByText(
          "DNS record for 'nextcloud.example.com' cleaned up"
        )
      ).toBeInTheDocument()
    })
  })

  it("shows success message after dismiss", async () => {
    // Mock window.confirm to return true
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true))

    let jobCallCount = 0
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "DELETE") {
        return Promise.resolve({ ok: true, status: 204, json: () => Promise.resolve(undefined) })
      }
      // GET /jobs calls
      jobCallCount++
      if (jobCallCount === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockJobs) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ jobs: [mockJobs.jobs[1]], total: 1 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)

    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })

    const dismissButtons = screen.getAllByText("Dismiss")
    fireEvent.click(dismissButtons[0])

    await waitFor(() => {
      expect(
        screen.getByText(
          "Orphan record for 'nextcloud.example.com' dismissed"
        )
      ).toBeInTheDocument()
    })
  })

  it("shows error message when retry fails", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: false,
          status: 502,
          json: () => Promise.resolve({ detail: "Cloudflare API error: connection refused" }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockJobs) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)

    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })

    const retryButtons = screen.getAllByText("Retry Deletion")
    fireEvent.click(retryButtons[0])

    await waitFor(() => {
      expect(
        screen.getByText("Cloudflare API error: connection refused")
      ).toBeInTheDocument()
    })
    // The action error is injected asynchronously and must announce to
    // assistive tech via a live region (role="alert").
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Cloudflare API error: connection refused"
    )
  })


  it("keeps the newest orphan job reload when an older action reload finishes later", async () => {
    const firstPost = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const secondPost = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const firstReload = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    let jobGetCalls = 0
    let postCalls = 0
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "POST") {
        postCalls++
        return postCalls === 1 ? firstPost.promise : secondPost.promise
      }
      jobGetCalls++
      if (jobGetCalls === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockJobs) })
      }
      if (jobGetCalls === 2) {
        return firstReload.promise
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ jobs: [], total: 0 }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)

    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })

    const retryButtons = screen.getAllByText("Retry Deletion")
    fireEvent.click(retryButtons[0])
    fireEvent.click(retryButtons[1])

    await act(async () => {
      firstPost.resolve({ ok: true, json: () => Promise.resolve({ success: true, message: "First cleanup retry queued" }) })
      await firstPost.promise
      await Promise.resolve()
    })
    await waitFor(() => {
      expect(jobGetCalls).toBe(2)
    })

    await act(async () => {
      secondPost.resolve({ ok: true, json: () => Promise.resolve({ success: true, message: "Second cleanup retry queued" }) })
      await secondPost.promise
      await Promise.resolve()
    })
    await waitFor(() => {
      expect(screen.getByText("No orphaned DNS records. All clean!")).toBeInTheDocument()
    })

    await act(async () => {
      firstReload.resolve({ ok: true, json: () => Promise.resolve({ jobs: [mockJobs.jobs[1]], total: 1 }) })
      await firstReload.promise
      await Promise.resolve()
    })

    expect(screen.getByText("No orphaned DNS records. All clean!")).toBeInTheDocument()
    expect(screen.queryByText("vaultwarden.example.com")).not.toBeInTheDocument()
  })
  it("fetches with kind=dns_orphan_cleanup filter", async () => {
    const fetchMock = mockFetch({ jobs: [], total: 0 })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled()
    })

    const jobsCall = fetchMock.mock.calls.find(
      (c: unknown[]) => String(c[0]).includes("/jobs")
    )
    expect(jobsCall).toBeDefined()
    expect(String(jobsCall![0])).toContain("kind=dns_orphan_cleanup")
  })

  it("does not claim 'All clean!' when the initial load fails", async () => {
    // Regression: the empty-state and the error banner used to render together,
    // telling the user everything is clean on the same screen as a load error.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: "Server error" }),
      })
    )
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("Server error")).toBeInTheDocument()
    })
    expect(
      screen.queryByText("No orphaned DNS records. All clean!")
    ).not.toBeInTheDocument()
  })

  it("clamps back to a populated page when the last record on a later page is dismissed", async () => {
    // Regression: dismissing the only record on page 2 left offset past the end,
    // so the reload returned an empty page and the UI falsely reported
    // "All clean!" while the page-1 records stayed unreachable.
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true))
    const page1Job = mockJobs.jobs[0] // nextcloud.example.com
    const page2Job = mockJobs.jobs[1] // vaultwarden.example.com
    let dismissed = false
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      const u = String(url)
      if (u.includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "DELETE") {
        dismissed = true
        return Promise.resolve({ ok: true, status: 204, json: () => Promise.resolve(undefined) })
      }
      const offset = Number(new URL(u, "http://localhost").searchParams.get("offset") ?? "0")
      const total = dismissed ? 50 : 51
      let jobs: unknown[] = []
      if (offset === 0) jobs = [page1Job]
      else if (offset === 50 && !dismissed) jobs = [page2Job]
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ jobs, total }) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)

    // Page 1 (offset 0): first record + a Next button (51 > page size 50).
    await waitFor(() => expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByText("Next"))
    })
    // Page 2 (offset 50): the lone trailing record.
    await waitFor(() => expect(screen.getByText("vaultwarden.example.com")).toBeInTheDocument())

    // Dismiss it: total drops to 50, so offset 50 is now past the end.
    await act(async () => {
      fireEvent.click(screen.getByText("Dismiss"))
    })

    // Must clamp back to page 1 instead of falsely reporting "All clean!".
    await waitFor(() => expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument())
    expect(
      screen.queryByText("No orphaned DNS records. All clean!")
    ).not.toBeInTheDocument()
    expect(screen.queryByText("vaultwarden.example.com")).not.toBeInTheDocument()
  })

  it("jumps to a page typed into the page input", async () => {
    const fetchMock = mockFetch({ jobs: mockJobs.jobs, total: 120 })
    vi.stubGlobal("fetch", fetchMock)
    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("1–50 of 120")).toBeInTheDocument()
    })

    const input = screen.getByLabelText("Go to page")
    await act(async () => {
      fireEvent.change(input, { target: { value: "3" } })
      fireEvent.keyDown(input, { key: "Enter" })
    })

    // Page 3 (offset 100): range updates and the fetch carries offset=100.
    await waitFor(() => {
      expect(screen.getByText("101–120 of 120")).toBeInTheDocument()
    })
    const jumpCall = fetchMock.mock.calls.find((c: unknown[]) => String(c[0]).includes("offset=100"))
    expect(jumpCall).toBeDefined()
    expect(String(jumpCall![0])).toContain("kind=dns_orphan_cleanup")
  })

  it("disables only the acting job's buttons while its retry is in flight", async () => {
    // Double-submit guard on a destructive DNS cleanup: the busy state is
    // per-job, so the acting row's Retry+Dismiss disable (no duplicate POST)
    // while a sibling job stays fully actionable.
    const post = deferred<{ ok: boolean; json: () => Promise<unknown> }>()
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (opts?.method === "POST") return post.promise
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockJobs) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: OrphanDns } = await import("@/pages/OrphanDns")
    renderRoute(<OrphanDns />)
    await waitFor(() => {
      expect(screen.getByText("nextcloud.example.com")).toBeInTheDocument()
    })

    const retryButtons = screen.getAllByText("Retry Deletion").map((el) => el.closest("button")!)
    const dismissButtons = screen.getAllByText("Dismiss").map((el) => el.closest("button")!)

    await act(async () => {
      fireEvent.click(retryButtons[0])
      await Promise.resolve()
    })

    // The acting job's buttons are disabled; the sibling job is untouched.
    expect(retryButtons[0]).toBeDisabled()
    expect(dismissButtons[0]).toBeDisabled()
    expect(retryButtons[1]).not.toBeDisabled()
    expect(dismissButtons[1]).not.toBeDisabled()
    // A second click while in flight must not fire another POST.
    fireEvent.click(retryButtons[0])
    expect(
      fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "POST"),
    ).toHaveLength(1)

    await act(async () => {
      post.resolve({ ok: true, json: () => Promise.resolve({ success: true, message: "queued" }) })
      await post.promise
      await Promise.resolve()
    })
    // Once settled, the guard releases and the row is actionable again.
    await waitFor(() => expect(retryButtons[0]).not.toBeDisabled())
  })
})
