import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, fireEvent, screen, waitFor } from "@testing-library/react"
import { FRESH_PROGRESS, mockFetchWithProgress, renderSetup } from "./setupTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Setup wizard - validation", () => {
  it("disables Next when account fields incomplete", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByText("Next")).toBeInTheDocument()
    })
    const nextBtn = screen.getByText("Next").closest("button")!
    expect(nextBtn).toBeDisabled()
  })

  it("disables Next when password too short", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()
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
    await renderSetup()
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
      target: { value: "password456" },
    })
    expect(screen.getByText("Next").closest("button")).toBeDisabled()
    expect(screen.getByText("Passwords do not match.")).toBeInTheDocument()
  })

  it("enables Next when account fields valid", async () => {
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
    expect(screen.getByText("Next").closest("button")).not.toBeDisabled()
  })

  it("requires Cloudflare zone ID and API token before continuing", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()

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

  it("blocks advancing past Cloudflare and surfaces the error when the connection test fails", async () => {
    // Step 2 (Cloudflare) runs a connection test as a gate, but ONLY when both
    // the zone ID and a fresh token are present (unlike Docker's always-run
    // test). A failing test must keep the wizard on the Cloudflare step, show
    // the failure, and fire NO advance to the ACME step — the same block-on-fail
    // contract the Docker step has, on this conditional branch.
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/auth/setup-progress")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ...FRESH_PROGRESS, user_exists: true, base_domain_set: true }),
        })
      }
      if (String(url).includes("/settings/test/cloudflare")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ success: false, message: "Cloudflare token rejected" }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText("abc123..."), { target: { value: "zone123" } })
    fireEvent.change(screen.getByPlaceholderText("API token..."), { target: { value: "cf-token" } })
    fireEvent.click(screen.getByText("Next").closest("button")!)

    // The failed test result is surfaced to the user.
    await waitFor(() => {
      expect(screen.getByText("Cloudflare token rejected")).toBeInTheDocument()
    })
    // Still on the Cloudflare step — the wizard did not advance to ACME Email.
    expect(screen.getByText("Step 3 of 6: Cloudflare")).toBeInTheDocument()
    expect(screen.queryByText("Step 4 of 6: ACME Email")).not.toBeInTheDocument()
  })

  it("disables Next when the Cloudflare Zone ID is whitespace only (FSA1)", async () => {
    // The Zone ID is a plain identifier the backend strips before its
    // min_length=1 check, so a whitespace-only value 422s. The gate must trim
    // it (like Base Domain / username) instead of accepting length>0, otherwise
    // a doomed save fires. cfToken is filled so only the Zone ID gates Next.
    vi.stubGlobal("fetch", mockFetchWithProgress(FRESH_PROGRESS))
    await renderSetup()

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
    await renderSetup()
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
    await renderSetup()

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
    await renderSetup()

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
    await renderSetup()

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

  it("keeps Next disabled when Base Domain is whitespace only", async () => {
    // Backend strips then enforces min_length>=1, so an all-whitespace base_domain
    // would pass an untrimmed client gate but 422 on save. The gate must trim.
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/auth/setup-progress")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...FRESH_PROGRESS, user_exists: true }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    }))
    await renderSetup()
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
    await renderSetup()
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

  it("advertises only the tskey-auth- prefix the backend accepts", async () => {
    vi.stubGlobal("fetch", mockFetchWithProgress({
      user_exists: true,
      base_domain_set: true,
      cloudflare_configured: true,
      acme_email_set: true,
      tailscale_configured: false,
      docker_configured: false,
    }))
    await renderSetup()
    await waitFor(() => {
      expect(screen.getByText("Step 5 of 6: Tailscale")).toBeInTheDocument()
    })

    expect(screen.getByText(/Must start with tskey-auth-\./)).toBeInTheDocument()
    expect(screen.queryByText(/tskey-reusable-/)).not.toBeInTheDocument()
  })
})
