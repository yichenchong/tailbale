import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

beforeEach(() => {
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
