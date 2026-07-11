import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import { FRESH_PROGRESS, mockFetchWithProgress, renderSetup } from "./setupTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Setup wizard - fresh install", () => {
  it("shows loading state while fetching progress", async () => {
    // setup-progress never resolves
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    await renderSetup()
    expect(screen.getByText("Loading setup progress...")).toBeInTheDocument()
  })

  it("shows setup progress load errors instead of silently assuming a fresh install", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByText("network down")).toBeInTheDocument()
    })
    // The progress-load error is injected asynchronously and must announce to
    // assistive tech via a live region (role="alert").
    expect(screen.getByRole("alert")).toHaveTextContent("network down")
  })

  it("renders first step with account fields on fresh install", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByText("Step 1 of 6: Account")).toBeInTheDocument()
    })
    expect(screen.getByText("Welcome to tailBale")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Password")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Confirm password")).toBeInTheDocument()
  })

  it("shows all step progress bars", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { container } = await renderSetup()
    await waitFor(() => {
      expect(screen.getByText("Step 1 of 6: Account")).toBeInTheDocument()
    })
    // 6 progress bar segments
    const bars = container.querySelectorAll(".rounded-full")
    expect(bars.length).toBe(6)
  })

  it("pressing Enter moves to the next field", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })

    const username = screen.getByPlaceholderText("admin")
    const password = screen.getByPlaceholderText("Password")
    username.focus()
    fireEvent.keyDown(username, { key: "Enter", code: "Enter" })

    expect(password).toHaveFocus()
  })

  it("pressing Enter on the last field advances to the next step", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText("admin"), {
      target: { value: "testuser" },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    })
    const confirm = screen.getByPlaceholderText("Confirm password")
    fireEvent.change(confirm, {
      target: { value: "password123" },
    })
    fireEvent.keyDown(confirm, { key: "Enter", code: "Enter" })

    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })
  })

  it("Back button is disabled on first step", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByText("Back")).toBeInTheDocument()
    })
    const backBtn = screen.getByText("Back").closest("button")!
    expect(backBtn).toBeDisabled()
  })

  it("disables Back while the current step is saving", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/auth/setup-progress")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ...FRESH_PROGRESS, user_exists: true }),
        })
      }
      if (opts?.method === "PUT" && String(url).includes("/settings/general")) {
        return new Promise(() => {})
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ success: true, message: "OK" }),
      })
    }))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)

    await waitFor(() => {
      expect(screen.getByText("Saving...")).toBeInTheDocument()
    })
    expect(screen.getByText("Back").closest("button")).toBeDisabled()
    expect(screen.getByText("Saving...").closest("button")).toHaveAttribute("aria-busy", "true")
  })

  it("advances to Domain step after account creation", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()

    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
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
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()

    // Step 1: Account
    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
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
    expect(screen.getByText("Needs Zone:Read and DNS:Edit permissions for your zone.")).toBeInTheDocument()
  })

  it("shows Complete Setup on last step", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()

    // Navigate through all steps
    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
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
    fireEvent.change(screen.getByPlaceholderText("API token..."), { target: { value: "cf-token" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument() })
    // Step 4
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "a@b.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 5 of 6: Tailscale")).toBeInTheDocument() })
    // Step 5
    fireEvent.change(screen.getByPlaceholderText("tskey-auth-..."), { target: { value: "tskey-auth-abc" } })
    fireEvent.change(screen.getByPlaceholderText("tskey-api-..."), { target: { value: "tskey-api-abc" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument() })
    // Step 6 should show "Complete Setup"
    expect(screen.getByText("Complete Setup")).toBeInTheDocument()
  })

  it("renders the wizard inside a main landmark", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()
    await waitFor(() =>
      expect(screen.getByText("Welcome to tailBale")).toBeInTheDocument()
    )
    expect(screen.getByRole("main")).toBeInTheDocument()
  })
})
