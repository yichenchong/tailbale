import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

beforeEach(() => {
  vi.restoreAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe("Login page", () => {
  it("renders username and password fields", async () => {
    const { default: Login } = await import("@/pages/Login")
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    )
    expect(screen.getByText("tailBale")).toBeInTheDocument()
    expect(screen.getByText("Sign in to continue.")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Username")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Password")).toBeInTheDocument()
    expect(screen.getByText("Sign In")).toBeInTheDocument()
  })

  it("disables button when fields empty", async () => {
    const { default: Login } = await import("@/pages/Login")
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    )
    const btn = screen.getByText("Sign In").closest("button")!
    expect(btn).toBeDisabled()
  })

  it("enables button when fields filled", async () => {
    const { default: Login } = await import("@/pages/Login")
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    )

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

  it("shows loading state during submission", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Login } = await import("@/pages/Login")
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    )

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
