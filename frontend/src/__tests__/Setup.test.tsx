import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

beforeEach(() => {
  vi.restoreAllMocks()
})

/** Mock fetch that returns setup-progress first, then all subsequent calls return `data`. */
function mockFetchWithProgress(
  progress: Record<string, boolean>,
  data: unknown = { user: { id: "usr_1", username: "admin", display_name: null, role: "admin" }, success: true, message: "OK" }
) {
  return vi.fn().mockImplementation((url: string) => {
    if (typeof url === "string" && url.includes("/auth/setup-progress")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(progress),
      })
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(data),
    })
  })
}

const FRESH_PROGRESS = {
  user_exists: false,
  base_domain_set: false,
  cloudflare_configured: false,
  acme_email_set: false,
  tailscale_configured: false,
  docker_configured: false,
}

describe("Setup wizard", () => {
  it("shows loading state while fetching progress", async () => {
    // setup-progress never resolves
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    expect(screen.getByText("Loading setup progress...")).toBeInTheDocument()
  })

  it("renders first step with account fields on fresh install", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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
    const { container } = render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Next")).toBeInTheDocument()
    })
    const nextBtn = screen.getByText("Next").closest("button")!
    expect(nextBtn).toBeDisabled()
  })

  it("disables Next when password too short", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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

  it("disables Next when passwords do not match", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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

  it("Back button is disabled on first step", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Back")).toBeInTheDocument()
    })
    const backBtn = screen.getByText("Back").closest("button")!
    expect(backBtn).toBeDisabled()
  })

  it("advances to Domain step after account creation", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

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
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

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

describe("Setup wizard resume", () => {
  it("skips to Domain step when user already exists", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
    }))
    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )
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

  it("skips account creation API call when user already exists", async () => {
    const fetchMock = mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: Setup } = await import("@/pages/Setup")
    render(
      <MemoryRouter>
        <Setup />
      </MemoryRouter>
    )

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
})
