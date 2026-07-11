import { expect, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

/**
 * Shared render/setup helpers + fixtures for the split Settings tab test files
 * (formerly the top-of-file scaffolding of SettingsPage.test.tsx). Imported by
 * SettingsPage.test.tsx and every Settings*Tab.test.tsx so there is a single,
 * non-diverging source of truth for the settings fixture and fetch mock.
 */
export const mockSettings = {
  general: {
    base_domain: "example.com",
    acme_email: "admin@example.com",
    reconcile_interval_seconds: 60,
    health_check_interval_seconds: 45,
    cert_renewal_window_days: 30,
    event_retention_days: 90,
    timezone: "UTC",
    developer_mode: false,
  },
  cloudflare: { zone_id: "zone123", token_configured: true },
  tailscale: {
    auth_key_configured: true,
    api_key_configured: false,
    control_url: "https://controlplane.tailscale.com",
    default_ts_hostname_prefix: "edge",
  },
  docker: { socket_path: "unix:///var/run/docker.sock" },
  paths: {
    generated_root: "data/generated",
    cert_root: "data/certs",
    tailscale_state_root: "data/tailscale",
  },
  setup_complete: true,
}

/** Builds a fetch mock that returns settings for /settings, version for /version, and optional overrides. */
export function mockFetch(data: unknown = mockSettings) {
  return vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
    if (String(url).includes("/version")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ version: "1.2.3" }),
      })
    }
    if (String(url).includes("/auth/change-password")) {
      if (opts?.body) {
        const body = JSON.parse(String(opts.body))
        if (body.current_password === "wrongpassword") {
          return Promise.resolve({
            ok: false,
            status: 401,
            json: () => Promise.resolve({ detail: "Current password is incorrect" }),
          })
        }
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ ok: true }),
      })
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve(data),
    })
  })
}

/** Render the full SettingsPage with a stubbed fetch and wait for the shell. */
export async function renderSettings(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock)
  const { default: SettingsPage } = await import("@/pages/SettingsPage")
  render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>
  )
  await waitFor(() => {
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })
}

/** Whether any recorded fetch call was a PUT (i.e. a save actually fired). */
export function firedPut(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.some(
    (c: unknown[]) => typeof c[1] === "object" && (c[1] as RequestInit).method === "PUT"
  )
}

/** Concatenated text of the elements referenced by an element's aria-describedby. */
export function describedByText(el: HTMLElement): string {
  return (el.getAttribute("aria-describedby") ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .join(" ")
}
