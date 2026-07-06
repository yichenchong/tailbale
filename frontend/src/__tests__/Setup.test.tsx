import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor, fireEvent, act } from "@testing-library/react"
import { renderRoute, mockApi } from "./testkit"

beforeEach(() => {
  vi.restoreAllMocks()
})

/** Mock fetch that returns setup-progress first, then all subsequent calls return `data`. */
function mockFetchWithProgress(
  progress: Record<string, boolean>,
  data: unknown = { user: { id: "usr_1", username: "admin", display_name: null, role: "admin" }, success: true, message: "OK" }
) {
  return mockApi([
    { url: "/auth/setup-progress", json: progress },
    { json: data },
  ])
}

const FRESH_PROGRESS = {
  user_exists: false,
  base_domain_set: false,
  cloudflare_configured: false,
  acme_email_set: false,
  tailscale_configured: false,
  docker_configured: false,
}

const ALL_DONE_PROGRESS = {
  user_exists: true,
  base_domain_set: true,
  cloudflare_configured: true,
  acme_email_set: true,
  tailscale_configured: true,
  docker_configured: true,
}

/** Fetch mock for a resumed setup where every step (including Docker) is already
 *  configured. Docker connection tests succeed so setup can complete. */
function mockResumeAllDone() {
  return mockApi([
    { url: "/auth/setup-progress", json: ALL_DONE_PROGRESS },
    { url: "/settings/test/docker", json: { success: true, message: "Docker connected" } },
    { json: {} },
  ])
}

describe("Setup wizard", () => {
  it("shows loading state while fetching progress", async () => {
    // setup-progress never resolves
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    expect(screen.getByText("Loading setup progress...")).toBeInTheDocument()
  })

  it("shows setup progress load errors instead of silently assuming a fresh install", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("network down")).toBeInTheDocument()
    })
    // The progress-load error is injected asynchronously and must announce to
    // assistive tech via a live region (role="alert").
    expect(screen.getByRole("alert")).toHaveTextContent("network down")
  })

  it("renders first step with account fields on fresh install", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
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
    const { default: Setup } = await import("@/pages/Setup")
    const { container } = renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 1 of 6: Account")).toBeInTheDocument()
    })
    // 6 progress bar segments
    const bars = container.querySelectorAll(".rounded-full")
    expect(bars.length).toBe(6)
  })

  it("disables Next when account fields incomplete", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Next")).toBeInTheDocument()
    })
    const nextBtn = screen.getByText("Next").closest("button")!
    expect(nextBtn).toBeDisabled()
  })

  it("disables Next when password too short", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
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

  it("disables Next when username is whitespace only", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByPlaceholderText("admin"), {
      target: { value: "   " },
    })
    fireEvent.change(screen.getByPlaceholderText("Password"), {
      target: { value: "password123" },
    })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), {
      target: { value: "password123" },
    })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()
  })

  it("disables Next when passwords do not match", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
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
      target: { value: "password456" },
    })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()
    expect(screen.getByText("Passwords do not match.")).toBeInTheDocument()
  })

  it("enables Next when account fields valid", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
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
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled()
  })


  it("pressing Enter moves to the next field", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
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
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
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
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
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
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)

    await waitFor(() => {
      expect(screen.getByText("Saving...")).toBeInTheDocument()
    })
    expect(screen.getByText("Back").closest("button")).toBeDisabled()
  })

  it("advances to Domain step after account creation", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

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
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

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
  })

  it("requires Cloudflare zone ID and API token before continuing", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "testuser" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument() })

    const nextBtn = screen.getByText("Next").closest("button")!
    expect(nextBtn).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    expect(nextBtn).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("API token..."), { target: { value: "cf-token" } })
    expect(nextBtn).not.toBeDisabled()
  })

  it("disables Next when the Cloudflare Zone ID is whitespace only (FSA1)", async () => {
    // The Zone ID is a plain identifier the backend strips before its
    // min_length=1 check, so a whitespace-only value 422s. The gate must trim
    // it (like Base Domain / username) instead of accepting length>0, otherwise
    // a doomed save fires. cfToken is filled so only the Zone ID gates Next.
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "testuser" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument() })

    fireEvent.change(screen.getByPlaceholderText("API token..."), { target: { value: "cf-token" } })
    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "   " } })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()

    // A real Zone ID re-enables Next.
    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled()
  })

  it("disables Next for a malformed ACME email and enables it for a valid one (FSA2)", async () => {
    // Resume with user/domain/cloudflare done so setup opens on the ACME step.
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
      base_domain_set: true,
      cloudflare_configured: true,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument()
    })

    // "foo@bar" satisfies a naive includes("@") gate but the backend regex 422s
    // it (domain has no dot), so the gate must reject it.
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "foo@bar" } })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "foo@bar.com" } })
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled()
  })

  it("allows Cloudflare zone ID when token already exists", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
      base_domain_set: true,
      cloudflare_token_set: true,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

    await waitFor(() => {
      expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    })

    const nextBtn = screen.getByText("Next").closest("button")!
    expect(nextBtn).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    expect(nextBtn).not.toBeDisabled()
  })

  it("requires both Tailscale auth and API keys", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "u" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    fireEvent.change(screen.getByPlaceholderText("API token..."), { target: { value: "cf-token" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "a@b.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 5 of 6: Tailscale")).toBeInTheDocument() })

    const nextBtn = screen.getByText("Next").closest("button")!
    expect(nextBtn).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("tskey-auth-..."), { target: { value: "tskey-auth-abc" } })
    expect(nextBtn).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("tskey-api-..."), { target: { value: "tskey-api-abc" } })
    expect(nextBtn).not.toBeDisabled()
  })

  it("shows error when save fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (typeof url === "string" && url.includes("/auth/setup-progress")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(FRESH_PROGRESS),
          })
        }
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve({ detail: "A user already exists" }),
        })
      })
    )
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
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
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

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

  it("Docker step has default socket path", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

    // Navigate to step 6 (Docker)
    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "u" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "example.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    fireEvent.change(screen.getByPlaceholderText("API token..."), { target: { value: "cf-token" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("you@example.com"), { target: { value: "a@b.com" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 5 of 6: Tailscale")).toBeInTheDocument() })
    fireEvent.change(screen.getByPlaceholderText("tskey-auth-..."), { target: { value: "tskey-auth-abc" } })
    fireEvent.change(screen.getByPlaceholderText("tskey-api-..."), { target: { value: "tskey-api-abc" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => { expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument() })

    const socketInput = screen.getByDisplayValue("unix:///var/run/docker.sock")
    expect(socketInput).toBeInTheDocument()
  })

  it("keeps Next disabled when Base Domain is whitespace only", async () => {
    // Backend strips then enforces min_length>=1, so an all-whitespace base_domain
    // would pass an untrimmed client gate but 422 on save. The gate must trim.
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/auth/setup-progress")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...FRESH_PROGRESS, user_exists: true }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "   " } })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()
  })

  it("ignores a synchronous double-fire of the account submit (ref-based in-flight guard)", async () => {
    // Two submit events in one React batch (a synchronous double-Enter on the
    // last field, or assistive tech) both close over `saving=false` before the
    // re-render commits, so a state-only `if (saving) return` lets both through
    // -> two setup-user POSTs. On a fresh install the 2nd 409s ("user already
    // exists") and surfaces a confusing error even though the account was just
    // created. A ref set synchronously at the top of saveAndNext closes it.
    let postCount = 0
    const { promise, resolve } = Promise.withResolvers<{ ok: boolean; json: () => Promise<unknown> }>()
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/auth/setup-progress")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(FRESH_PROGRESS) })
      }
      if (String(url).includes("/auth/setup-user") && opts?.method === "POST") {
        postCount += 1
        return promise
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: "testuser" } })
    fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: "password123" } })
    fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: "password123" } })

    const form = screen.getByText("Next").closest("button")!.closest("form")!
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))
    })

    expect(postCount).toBe(1)

    await act(async () => {
      resolve({ ok: true, json: () => Promise.resolve({ user: { id: "u1", username: "testuser", display_name: null, role: "admin" } }) })
    })
  })
})

describe("Setup wizard resume", () => {
  it("skips to Domain step when user already exists", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText("mydomain.com")).toBeInTheDocument()
  })

  it("skips to Cloudflare step when user and domain done", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
      base_domain_set: true,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    })
  })

  it("skips to ACME step when user, domain, and cloudflare done", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
      base_domain_set: true,
      cloudflare_configured: true,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument()
    })
  })

  it("skips to Docker step when everything except Docker done", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      user_exists: true,
      base_domain_set: true,
      cloudflare_configured: true,
      acme_email_set: true,
      tailscale_configured: true,
      docker_configured: false,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument()
    })
  })

  it("shows already-completed banner when navigating back to done step", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
      base_domain_set: true,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    // Should start on step 3 (Cloudflare)
    await waitFor(() => {
      expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    })

    // Navigate back to step 2 (Domain) — already completed
    fireEvent.click(screen.getByText("Back").closest("button")!)
    expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    expect(
      screen.getByText(/This step was already completed/)
    ).toBeInTheDocument()
  })

  it("enables Next on already-completed step without filling fields", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
      base_domain_set: true,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    })

    // Go back to step 2 (Domain, already completed)
    fireEvent.click(screen.getByText("Back").closest("button")!)
    expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()

    // Next should be enabled even though domain input is empty
    // (because the step is already completed)
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled()
  })

  it("blocks Next on a completed ACME step when the email is re-edited to a malformed value (FSA3)", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
      base_domain_set: true,
      cloudflare_configured: true,
      acme_email_set: true,
    }))
    // dynamic import: stub fetch before the module's api client loads (the
    // module-loading-boundary pattern this whole suite uses).
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    // Resumes at the first incomplete step (Tailscale, step 5).
    await waitFor(() => {
      expect(screen.getByText("Step 5 of 6: Tailscale")).toBeInTheDocument()
    })

    // Back to the already-completed ACME Email step (step 4).
    fireEvent.click(screen.getByText("Back").closest("button")!)
    expect(screen.getByText("Step 4 of 6: ACME Email")).toBeInTheDocument()

    const nextButton = () => screen.getByText("Next").closest("button")
    const emailInput = screen.getByPlaceholderText("you@example.com")

    // Empty input on a completed step keeps the existing value -> Next enabled.
    expect(nextButton()).not.toBeDisabled()

    // Actively re-editing to a malformed value must block Next (no doomed PUT).
    fireEvent.change(emailInput, { target: { value: "not-an-email" } })
    expect(nextButton()).toBeDisabled()

    // A valid value re-enables Next.
    fireEvent.change(emailInput, { target: { value: "ops@new.io" } })
    expect(nextButton()).not.toBeDisabled()
  })

  it("does not overwrite completed blank steps when clicking Next", async () => {
    const fetchMock = mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
      base_domain_set: true,
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Back").closest("button")!)
    expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => {
      expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    })

    const calls = fetchMock.mock.calls.map((c: unknown[]) => c[0] as string)
    expect(calls.some((url: string) => url.includes("/settings/general"))).toBe(false)
  })

  it("skips account creation API call when user already exists", async () => {
    const fetchMock = mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)

    // Should start on step 2 (Domain)
    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })

    // Navigate back to step 1 (Account, already completed)
    fireEvent.click(screen.getByText("Back").closest("button")!)
    expect(screen.getByText("Step 1 of 6: Account")).toBeInTheDocument()

    // Click Next — should skip to Domain without calling setup-user
    fireEvent.click(screen.getByText("Next").closest("button")!)
    await waitFor(() => {
      expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
    })

    // Verify no setup-user call was made (only setup-progress was fetched)
    const calls = fetchMock.mock.calls.map((c: unknown[]) => c[0] as string)
    expect(calls.some((url: string) => url.includes("/auth/setup-user"))).toBe(false)
  })

  it("does not re-PUT the docker socket when completing an already-configured step", async () => {
    const fetchMock = mockResumeAllDone()
    vi.stubGlobal("fetch", fetchMock)
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Complete Setup").closest("button")!)

    // Setup must still verify Docker and mark itself complete...
    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c: unknown[]) => String(c[0]))
      expect(urls.some((u: string) => u.includes("/settings/setup-complete"))).toBe(true)
    })
    const urls = fetchMock.mock.calls.map((c: unknown[]) => String(c[0]))
    expect(urls.some((u: string) => u.includes("/settings/test/docker"))).toBe(true)
    // ...but must NOT overwrite the saved socket with the default value.
    const dockerPut = fetchMock.mock.calls.some(
      (c: unknown[]) =>
        String(c[0]).includes("/settings/docker") &&
        (c[1] as RequestInit | undefined)?.method === "PUT"
    )
    expect(dockerPut).toBe(false)
  })

  it("re-PUTs the docker socket when the field is edited on an already-configured step", async () => {
    const fetchMock = mockResumeAllDone()
    vi.stubGlobal("fetch", fetchMock)
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByDisplayValue("unix:///var/run/docker.sock"), {
      target: { value: "unix:///custom/docker.sock" },
    })
    fireEvent.click(screen.getByText("Complete Setup").closest("button")!)

    await waitFor(() => {
      const dockerPut = fetchMock.mock.calls.find(
        (c: unknown[]) =>
          String(c[0]).includes("/settings/docker") &&
          (c[1] as RequestInit | undefined)?.method === "PUT"
      )
      expect(dockerPut).toBeTruthy()
      expect(String((dockerPut?.[1] as RequestInit).body)).toContain("unix:///custom/docker.sock")
    })
  })

  it("blocks Complete Setup and surfaces the error when the Docker connection test fails", async () => {
    // The final Docker step ALWAYS runs a connection test; a failing test must
    // keep the wizard on step 6, show the failure, and fire NO setup-complete
    // PUT (a broken socket must not be able to finish setup).
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/auth/setup-progress")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(ALL_DONE_PROGRESS) })
      }
      if (String(url).includes("/settings/test/docker")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ success: false, message: "Cannot connect to Docker socket" }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Complete Setup").closest("button")!)

    // The failed test result is surfaced to the user.
    await waitFor(() => {
      expect(screen.getByText("Cannot connect to Docker socket")).toBeInTheDocument()
    })
    // Still on the Docker step — the wizard did not advance/finish.
    expect(screen.getByText("Step 6 of 6: Docker")).toBeInTheDocument()
    // No setup-complete PUT fired, so setup is NOT marked done on a failed probe.
    const urls = fetchMock.mock.calls.map((c: unknown[]) => String(c[0]))
    expect(urls.some((u: string) => u.includes("/settings/setup-complete"))).toBe(false)
  })
})

/**
 * The Tailscale auth-key hint must match backend validation, which accepts only
 * keys starting with `tskey-auth-` (see settings.py / setup_state.py). The hint
 * previously also advertised a `tskey-reusable-` prefix the backend rejects,
 * which would lead users into a confusing 400.
 */
describe("Setup wizard hints", () => {
  it("advertises only the tskey-auth- prefix the backend accepts", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      user_exists: true,
      base_domain_set: true,
      cloudflare_configured: true,
      acme_email_set: true,
      tailscale_configured: false,
      docker_configured: false,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    renderRoute(<Setup />)
    await waitFor(() => {
      expect(screen.getByText("Step 5 of 6: Tailscale")).toBeInTheDocument()
    })

    expect(screen.getByText(/Must start with tskey-auth-\./)).toBeInTheDocument()
    expect(screen.queryByText(/tskey-reusable-/)).not.toBeInTheDocument()
  })
})
