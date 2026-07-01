import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import { renderRoute } from "./testkit"

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("Login page", () => {
  it("renders username and password fields", async () => {
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)
    expect(screen.getByText("tailBale")).toBeInTheDocument()
    expect(screen.getByText("Sign in to continue.")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Username")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Password")).toBeInTheDocument()
    expect(screen.getByText("Sign In")).toBeInTheDocument()
  })

  it("sets the login favicon to an existing bundled asset", async () => {
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)

    await waitFor(() => {
      const icon = document.querySelector<HTMLLinkElement>("link[rel='icon']")
      expect(icon?.href).toMatch(/\/favicon-healthy\.svg$/)
    })
  })

  it("disables button when fields empty", async () => {
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)
    const btn = screen.getByText("Sign In").closest("button")!
    expect(btn).toBeDisabled()
  })

  it("enables button when fields filled", async () => {
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)
    fireEvent.change(screen.getByPlaceholderText("Username"), {
      target: { value: "admin" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    })
    const btn = screen.getByText("Sign In").closest("button")!
    expect(btn).not.toBeDisabled()
  })

  it("shows error on failed login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: "Invalid credentials" }),
      })
    )
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)

    fireEvent.change(screen.getByPlaceholderText("Username"), {
      target: { value: "admin" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "wrong" },
    })
    fireEvent.click(screen.getByText("Sign In").closest("button")!)

    await waitFor(() => {
      expect(screen.getByText("Invalid credentials")).toBeInTheDocument()
    })
  })

  it("announces the login error to assistive tech via role=alert", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: "Invalid credentials" }),
      })
    )
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)

    fireEvent.change(screen.getByPlaceholderText("Username"), {
      target: { value: "admin" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "wrong" },
    })
    fireEvent.click(screen.getByText("Sign In").closest("button")!)

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("Invalid credentials")
  })

  it("surfaces the rate-limit message when login returns 429 (too many attempts)", async () => {
    // Mirrors backend/app/routers/auth.py:_reject_failed_login: a locked-out
    // client gets 429 with a detail body. The generic error path must surface
    // that message verbatim rather than a bare "Request failed: 429".
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        json: () =>
          Promise.resolve({ detail: "Too many failed login attempts. Try again later." }),
      })
    )
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)

    fireEvent.change(screen.getByPlaceholderText("Username"), {
      target: { value: "admin" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "wrong" },
    })
    fireEvent.click(screen.getByText("Sign In").closest("button")!)

    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("Too many failed login attempts. Try again later.")
  })

  it("shows loading state during submission", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)

    fireEvent.change(screen.getByPlaceholderText("Username"), {
      target: { value: "admin" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByText("Sign In").closest("button")!)

    await waitFor(() => {
      expect(screen.getByText("Signing in...")).toBeInTheDocument()
    })
  })

  it("ignores a synchronous double-fire of the login submit (ref-based in-flight guard)", async () => {
    // A state-based `if (loading) return` guard only blocks a second submit once
    // React has committed the `loading=true` re-render. Two submit events
    // dispatched within one batch (before that commit) both close over
    // `loading=false` and slip through -> two POSTs. On wrong credentials that
    // burns two of the 5 rate-limit attempts per user action. A ref set
    // synchronously at the top of the handler closes that window.
    let postCount = 0
    const { promise, resolve } = Promise.withResolvers<{ ok: boolean; status: number; json: () => Promise<unknown> }>()
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes("/auth/login")) {
          postCount += 1
          return promise
        }
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) })
      })
    )
    const { default: Login } = await import("@/pages/Login")
    renderRoute(<Login />)

    fireEvent.change(screen.getByPlaceholderText("Username"), { target: { value: "admin" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })

    // Dispatch both submits inside ONE act() so no re-render lands between them.
    const form = screen.getByText("Sign In").closest("button")!.closest("form")!
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))
    })

    expect(postCount).toBe(1)

    // Settle the in-flight POST so no promise dangles past the test.
    await act(async () => {
      resolve({ ok: true, status: 200, json: () => Promise.resolve({ user: { id: "u1", username: "admin", display_name: null, role: "admin" } }) })
    })
  })
})

describe("Login page does not fetch dashboard summary", () => {
  it("never calls /api/dashboard/summary when unauthenticated", async () => {
    // Track all fetched URLs
    const fetchedUrls: string[] = []
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      fetchedUrls.push(String(url))
      if (String(url).includes("/auth/status")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({ setup_complete: true, authenticated: false }),
        })
      }
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              general: { timezone: "UTC" },
            }),
        })
      }
      // Return 401 for everything else (simulates unauthenticated)
      return Promise.resolve({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: "Not authenticated" }),
      })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: App } = await import("@/App")
    render(<App />)

    // Wait for auth check to complete and login page to render
    await waitFor(() => {
      expect(screen.getByText("Sign in to continue.")).toBeInTheDocument()
    })

    // Wait a bit longer to catch any async effects that fire after render
    await new Promise((r) => setTimeout(r, 200))

    const summaryRequests = fetchedUrls.filter((u) =>
      u.includes("dashboard/summary")
    )
    expect(summaryRequests).toEqual([])
  })
})
