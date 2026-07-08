import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { cachedTimezone, _resetTimezoneCache } from "@/lib/useTimezone"
import { mockSettings, mockFetch } from "./settingsTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsPage", () => {
  it("shows loading state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    expect(screen.getByText("Loading settings...")).toBeInTheDocument()
  })

  it("renders page title and tabs", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })
    expect(screen.getByText("General")).toBeInTheDocument()
    expect(screen.getByText("Cloudflare")).toBeInTheDocument()
    expect(screen.getByText("Tailscale")).toBeInTheDocument()
    expect(screen.getByText("Docker")).toBeInTheDocument()
    expect(screen.getByText("Paths")).toBeInTheDocument()
    expect(screen.queryByText("Developer")).not.toBeInTheDocument()

  })

  it("shows Developer tab when developer mode is enabled", async () => {
    vi.stubGlobal("fetch", mockFetch({
      ...mockSettings,
      general: { ...mockSettings.general, developer_mode: true },
    }))
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })
    expect(screen.getByText("Developer")).toBeInTheDocument()
  })

  it("switches to Cloudflare tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Cloudflare"))
    expect(screen.getByText("Zone ID")).toBeInTheDocument()
    expect(screen.getByText("API Token")).toBeInTheDocument()
    expect(screen.getByDisplayValue("zone123")).toBeInTheDocument()
    expect(screen.getByText("Configured")).toBeInTheDocument()
    expect(screen.getByText("Test Connection")).toBeInTheDocument()
  })

  it("switches to Tailscale tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Tailscale"))
    expect(screen.getByText("Auth Key")).toBeInTheDocument()
    expect(screen.getByText("Control URL")).toBeInTheDocument()
    expect(screen.getByText("Default TS Hostname Prefix")).toBeInTheDocument()
    expect(screen.getByText("Configured")).toBeInTheDocument()
    expect(screen.getByText("Validate Key")).toBeInTheDocument()
  })

  it("switches to Docker tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Docker"))
    expect(screen.getByText("Docker Socket Path")).toBeInTheDocument()
    expect(
      screen.getByDisplayValue("unix:///var/run/docker.sock")
    ).toBeInTheDocument()
    expect(screen.getByText("Test Connection")).toBeInTheDocument()
  })

  it("switches to Paths tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Paths"))
    expect(screen.getByText("Generated Config Root")).toBeInTheDocument()
    expect(screen.getByText("Certificate Root")).toBeInTheDocument()
    expect(screen.getByText("Tailscale State Root")).toBeInTheDocument()
    expect(screen.getByDisplayValue("data/generated")).toBeInTheDocument()
    expect(screen.getByDisplayValue("data/certs")).toBeInTheDocument()
    expect(screen.getByDisplayValue("data/tailscale")).toBeInTheDocument()
  })

  it("does not show a stale connection test result after switching tabs", async () => {
    let resolveDockerTest: ((value: { ok: boolean; json: () => Promise<unknown> }) => void) | undefined
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (String(url).includes("/settings/test/docker")) {
        return new Promise((resolve) => {
          resolveDockerTest = resolve
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockSettings),
      })
    })
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

    fireEvent.click(screen.getByText("Docker"))
    fireEvent.click(screen.getByText("Test Connection"))
    fireEvent.click(screen.getByText("Cloudflare"))
    resolveDockerTest?.({
      ok: true,
      json: () => Promise.resolve({ success: true, message: "Docker is reachable" }),
    })

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test Connection" })).toBeInTheDocument()
    })
    expect(screen.queryByText("Docker is reachable")).not.toBeInTheDocument()
  })

  it("keeps saved settings when a stale load resolves afterwards (last writer wins)", async () => {
    // Regression for the applySettingsUpdate stale-load guard. A save is the
    // authoritative latest write: it bumps the load-sequence so any settings
    // load still in flight (mount / future poll / refresh) is discarded instead
    // of clobbering the freshly-saved values, and the server response — not the
    // values typed locally — is what persists. Also verifies the timezone cache
    // is synced from the saved response.
    _resetTimezoneCache()
    const savedSettings = {
      ...mockSettings,
      general: {
        ...mockSettings.general,
        base_domain: "saved.example.com",
        timezone: "America/New_York",
        developer_mode: true,
      },
    }
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ version: "1.0.0" }) })
      }
      if (opts?.method === "PUT" && String(url).includes("/settings/general")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(savedSettings) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByDisplayValue("example.com")).toBeInTheDocument()
    })

    // Type a local edit, then save: the server's response must win, not the typed value.
    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "typed.example.com" } })
    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => {
      expect(screen.getByDisplayValue("saved.example.com")).toBeInTheDocument()
    })
    expect(screen.queryByDisplayValue("typed.example.com")).not.toBeInTheDocument()
    expect(screen.getByText("Developer")).toBeInTheDocument()
    expect(cachedTimezone).toBe("America/New_York")

    // Flush any pending microtasks: the saved values must persist (a stale load,
    // had one been in flight, is discarded by the bumped load sequence).
    await Promise.resolve()
    await Promise.resolve()
    expect(screen.getByDisplayValue("saved.example.com")).toBeInTheDocument()
    expect(cachedTimezone).toBe("America/New_York")
    _resetTimezoneCache()
  })
})

describe("SettingsPage error banner reset on tab change", () => {
  it("clears a save error banner when switching to another tab", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ version: "1.2.3" }) })
      }
      if (opts?.method === "PUT" && String(url).includes("/settings/general")) {
        return Promise.resolve({
          ok: false,
          status: 400,
          json: () => Promise.resolve({ detail: "Base domain is required" }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
    })
    vi.stubGlobal("fetch", fetchMock)
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Save")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Save"))
    await waitFor(() => {
      expect(screen.getByText("Base domain is required")).toBeInTheDocument()
    })

    // Switching tabs must drop the stale banner (it referred to the General tab).
    fireEvent.click(screen.getByText("Cloudflare"))
    expect(screen.queryByText("Base domain is required")).not.toBeInTheDocument()
  })
})

describe("SettingsPage connection-test scoping", () => {
  it("keeps another tab's Test button enabled while one tab's test is in flight", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ version: "1.2.3" }) })
      }
      if (String(url).includes("/settings/test/docker")) {
        return new Promise(() => {}) // Docker test hangs forever.
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
    })
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

    fireEvent.click(screen.getByText("Docker"))
    fireEvent.click(screen.getByText("Test Connection"))
    expect(screen.getByRole("button", { name: "Testing..." })).toBeDisabled()

    // Switching tabs while Docker's test hangs must not disable Cloudflare's Test
    // button — the busy flag is scoped per service, not global.
    fireEvent.click(screen.getByText("Cloudflare"))
    const cfTest = screen.getByRole("button", { name: "Test Connection" })
    expect(cfTest).not.toBeDisabled()
    expect(screen.queryByRole("button", { name: "Testing..." })).not.toBeInTheDocument()
  })

  it("discards a late test result so it cannot overwrite a newer one", async () => {
    let resolveDocker: ((v: { ok: boolean; json: () => Promise<unknown> }) => void) | undefined
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ version: "1.2.3" }) })
      }
      if (String(url).includes("/settings/test/docker")) {
        return new Promise((resolve) => { resolveDocker = resolve })
      }
      if (String(url).includes("/settings/test/cloudflare")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, message: "Cloudflare reachable" }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
    })
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

    // Start a slow Docker test, switch to Cloudflare, run a test that resolves first.
    fireEvent.click(screen.getByText("Docker"))
    fireEvent.click(screen.getByText("Test Connection"))
    fireEvent.click(screen.getByText("Cloudflare"))
    fireEvent.click(screen.getByText("Test Connection"))
    await waitFor(() => {
      expect(screen.getByText("Cloudflare reachable")).toBeInTheDocument()
    })

    // The stale Docker test resolving afterwards must not clobber the Cloudflare banner.
    await act(async () => {
      resolveDocker?.({ ok: true, json: () => Promise.resolve({ success: true, message: "Docker reachable" }) })
      await Promise.resolve()
    })
    expect(screen.getByText("Cloudflare reachable")).toBeInTheDocument()
    expect(screen.queryByText("Docker reachable")).not.toBeInTheDocument()
  })

  it("clears a prior save error banner when a connection test is run", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ version: "1.2.3" }) })
      }
      if (opts?.method === "PUT" && String(url).includes("/settings/cloudflare")) {
        return Promise.resolve({ ok: false, status: 400, json: () => Promise.resolve({ detail: "Zone rejected" }) })
      }
      if (String(url).includes("/settings/test/cloudflare")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, message: "Cloudflare reachable" }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
    })
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

    fireEvent.click(screen.getByText("Cloudflare"))
    fireEvent.click(screen.getByText("Save"))
    await waitFor(() => {
      expect(screen.getByText("Zone rejected")).toBeInTheDocument()
    })

    // Running a test must drop the stale save-error banner.
    fireEvent.click(screen.getByText("Test Connection"))
    await waitFor(() => {
      expect(screen.getByText("Cloudflare reachable")).toBeInTheDocument()
    })
    expect(screen.queryByText("Zone rejected")).not.toBeInTheDocument()
  })
})

describe("SettingsPage tablist keyboard navigation (FB-A11Y1)", () => {
  it("moves the active tab with Arrow keys, Home, and End", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    const generalTab = screen.getByRole("tab", { name: "General" })
    expect(generalTab).toHaveAttribute("aria-selected", "true")

    // ArrowRight advances selection + focus to the next tab and swaps panel content.
    fireEvent.keyDown(generalTab, { key: "ArrowRight" })
    const cloudflareTab = screen.getByRole("tab", { name: "Cloudflare" })
    expect(cloudflareTab).toHaveAttribute("aria-selected", "true")
    expect(generalTab).toHaveAttribute("aria-selected", "false")
    expect(cloudflareTab).toHaveFocus()
    expect(screen.getByText("Zone ID")).toBeInTheDocument()

    // ArrowLeft steps back.
    fireEvent.keyDown(cloudflareTab, { key: "ArrowLeft" })
    expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByRole("tab", { name: "General" })).toHaveFocus()

    // End jumps to the last visible tab, Home back to the first.
    fireEvent.keyDown(screen.getByRole("tab", { name: "General" }), { key: "End" })
    const accountTab = screen.getByRole("tab", { name: "Account" })
    expect(accountTab).toHaveAttribute("aria-selected", "true")
    expect(accountTab).toHaveFocus()

    fireEvent.keyDown(accountTab, { key: "Home" })
    expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("aria-selected", "true")

    // ArrowLeft from the first tab wraps to the last.
    fireEvent.keyDown(screen.getByRole("tab", { name: "General" }), { key: "ArrowLeft" })
    expect(screen.getByRole("tab", { name: "Account" })).toHaveAttribute("aria-selected", "true")
  })

  it("uses a roving tabindex so only the active tab is tabbable", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("tabindex", "0")
    expect(screen.getByRole("tab", { name: "Cloudflare" })).toHaveAttribute("tabindex", "-1")

    fireEvent.click(screen.getByRole("tab", { name: "Cloudflare" }))
    expect(screen.getByRole("tab", { name: "Cloudflare" })).toHaveAttribute("tabindex", "0")
    expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("tabindex", "-1")
  })

  it("wires aria-controls and aria-labelledby between tabs and the panel", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    const generalTab = screen.getByRole("tab", { name: "General" })
    const panel = screen.getByRole("tabpanel")
    const controls = generalTab.getAttribute("aria-controls")
    expect(controls).toBeTruthy()
    expect(generalTab.id).toBeTruthy()
    expect(panel).toHaveAttribute("id", controls)
    expect(panel).toHaveAttribute("aria-labelledby", generalTab.id)

    // Every tab points at the one shared panel.
    for (const t of screen.getAllByRole("tab")) {
      expect(t).toHaveAttribute("aria-controls", controls as string)
    }

    // The panel's label follows the active tab after switching.
    fireEvent.click(screen.getByRole("tab", { name: "Docker" }))
    const dockerTab = screen.getByRole("tab", { name: "Docker" })
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", dockerTab.id)
  })
})
