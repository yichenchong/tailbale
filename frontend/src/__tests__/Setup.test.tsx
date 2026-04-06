import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Setup wizard", () => {
  it("renders first step with account fields", async () => {
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    expect(screen.getByText("Welcome to tailBale")).toBeInTheDocument()
    expect(screen.getByText("Step 1 of 6: Account")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Password")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Confirm password")).toBeInTheDocument()
  })

  it("shows all step progress bars", async () => {
    const { default: Setup } = await import("@/pages/Setup")
    const { container } = render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    // 6 progress bar segments
    const bars = container.querySelectorAll(".rounded-full")
    expect(bars.length).toBe(6)
  })

  it("disables Next when account fields incomplete", async () => {
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    const nextBtn = screen.getByText("Next").closest("button")!
    expect(nextBtn).toBeDisabled()
  })

  it("disables Next when password too short", async () => {
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    fireEvent.change(screen.getByPlaceholderText("admin"), {
      target: { value: "testuser" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "short" },
    })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), {
      target: { value: "short" },
    })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()
  })

  it("disables Next when passwords do not match", async () => {
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    fireEvent.change(screen.getByPlaceholderText("admin"), {
      target: { value: "testuser" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), {
      target: { value: "password456" },
    })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()
    expect(screen.getByText("Passwords do not match.")).toBeInTheDocument()
  })

  it("enables Next when account fields valid", async () => {
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    fireEvent.change(screen.getByPlaceholderText("admin"), {
      target: { value: "testuser" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), {
      target: { value: "password123" },
    })
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled()
  })

  it("Back button is disabled on first step", async () => {
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    const backBtn = screen.getByText("Back").closest("button")!
    expect(backBtn).toBeDisabled()
  })

  it("advances to Domain step after account creation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            user: { id: "usr_1", username: "admin", display_name: null, role: "admin" },
          }),
      })
    )
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

    fireEvent.change(screen.getByPlaceholderText("admin"), {
      target: { value: "testuser" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByText("Next").closest("button")!)

    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText("mydomain.com")).toBeInTheDocument()
  })

  it("advances through Domain to Cloudflare step", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            user: { id: "usr_1", username: "admin", display_name: null, role: "admin" },
            success: true,
            message: "OK",
          }),
      })
    )
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

    // Step 1: Account
    fireEvent.change(screen.getByPlaceholderText("admin"), {
      target: { value: "testuser" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), {
      target: { value: "password123" },
    })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })

    // Step 2: Domain
    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), {
      target: { value: "example.com" },
    })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => {
      expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    })
    expect(screen.getByText("Cloudflare Zone ID")).toBeInTheDocument()
  })

  it("validates ACME email requires @", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ user: { id: "usr_1", username: "admin", display_name: null, role: "admin" } }),
      })
    )
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

    // Navigate to step 4 (ACME)
    // Step 1: Account
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "u" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument() })
    // Step 2: Domain
    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument() })
    // Step 3: Cloudflare
    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument() })

    // Type email without @
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "invalid" } })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()

    // Type valid email
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "admin@example.com" } })
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled()
  })

  it("shows Back button navigating to previous step", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ user: { id: "usr_1", username: "admin", display_name: null, role: "admin" } }),
      })
    )
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

    // Go to step 2
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "u" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument() })

    // Go back
    fireEvent.click(screen.getByText("Back").closest("button")!)
    expect(screen.getByText("Step 1 of 6: Account")).toBeInTheDocument()
  })

  it("shows error when save fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ detail: "A user already exists" }),
      })
    )
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "admin" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)

    await waitFor(() => {
      expect(screen.getByText("A user already exists")).toBeInTheDocument()
    })
    // Should stay on step 1
    expect(screen.getByText("Step 1 of 6: Account")).toBeInTheDocument()
  })

  it("shows Complete Setup on last step", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ user: { id: "usr_1", username: "admin", display_name: null, role: "admin" }, success: true, message: "OK" }),
      })
    )
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

    // Navigate through all steps
    // Step 1: Account
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "u" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument() })
    // Step 2
    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument() })
    // Step 3
    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument() })
    // Step 4
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "a@b.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 5 of 6: Tailscale")).toBeInTheDocument() })
    // Step 5
    fireEvent.change(screen.getByPlaceholderText("tskey-auth-..."), { target: { value: "tskey-auth-abc" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument() })
    // Step 6 should show "Complete Setup"
    expect(screen.getByText("Complete Setup")).toBeInTheDocument()
  })

  it("Docker step has default socket path", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ user: { id: "usr_1", username: "admin", display_name: null, role: "admin" }, success: true, message: "OK" }),
      })
    )
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

    // Navigate to step 6 (Docker)
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "u" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "a@b.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 5 of 6: Tailscale")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("tskey-auth-..."), { target: { value: "tskey-auth-abc" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument() })

    const socketInput = screen.getByDisplayValue("unix:///var/run/docker.sock")
    expect(socketInput).toBeInTheDocument()
  })
})
