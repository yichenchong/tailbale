import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import { ALL_DONE_PROGRESS, FRESH_PROGRESS, mockFetchWithProgress, mockResumeAllDone, renderSetup } from "./setupTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Setup wizard - resume", () => {
  it("skips to Domain step when user already exists", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      ...FRESH_PROGRESS,
      user_exists: true,
    }))
    await renderSetup()
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
    await renderSetup()
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
    await renderSetup()
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
    await renderSetup()
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
    await renderSetup()
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
    await renderSetup()
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
    await renderSetup()
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
    await renderSetup()
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

    await renderSetup()

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
    await renderSetup()
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
    await renderSetup()
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
    await renderSetup()
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
