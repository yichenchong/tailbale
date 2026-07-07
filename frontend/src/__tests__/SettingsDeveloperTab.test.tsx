import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { DeveloperTab } from "@/pages/settings/DeveloperTab"
import { mockSettings, describedByText } from "./settingsTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsDeveloperTab", () => {
  it("runs reset setup_complete only after confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (String(url).includes("/settings/developer/reset-setup-complete")) {
        return new Promise(() => {})
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          ...mockSettings,
          general: { ...mockSettings.general, developer_mode: true },
        }),
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
      expect(screen.getByText("Developer")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Developer"))
    fireEvent.click(screen.getByRole("button", { name: "Reset setup_complete" }))

    expect(confirm).toHaveBeenCalled()
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/settings/developer/reset-setup-complete"))).toBe(true)
    expect(screen.getByRole("button", { name: "Working..." })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reset all" })).toBeDisabled()
  })

  it("does not run developer reset when warning is cancelled", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false)
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          ...mockSettings,
          general: { ...mockSettings.general, developer_mode: true },
        }),
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
      expect(screen.getByText("Developer")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Developer"))
    fireEvent.click(screen.getByRole("button", { name: "Reset all" }))

    expect(confirm).toHaveBeenCalled()
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/settings/developer/reset-all"))).toBe(false)
  })

  it("loads main tailBale logs from Developer tab", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (String(url).includes("/settings/developer/main-logs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ container: "tailbale", logs: "line one\nline two\n" }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          ...mockSettings,
          general: { ...mockSettings.general, developer_mode: true },
        }),
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
      expect(screen.getByText("Developer")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Developer"))
    fireEvent.click(screen.getByRole("button", { name: "Refresh logs" }))

    await waitFor(() => {
      expect(screen.getByText("Container: tailbale")).toBeInTheDocument()
      expect(screen.getByText(/line one/)).toBeInTheDocument()
    })
  })

  it("shows an accessible loading state while main logs are pending", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (String(url).includes("/settings/developer/main-logs")) {
        return new Promise(() => {})
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          ...mockSettings,
          general: { ...mockSettings.general, developer_mode: true },
        }),
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
      expect(screen.getByText("Developer")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Developer"))
    fireEvent.click(screen.getByRole("button", { name: "Refresh logs" }))

    expect(screen.getByRole("button", { name: "Loading logs..." })).toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent("Loading main container logs...")
  })

  it("shows main log API errors in the Developer tab", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (String(url).includes("/settings/developer/main-logs")) {
        return Promise.resolve({
          ok: false,
          status: 502,
          json: () => Promise.resolve({ detail: "Could not read tailBale logs: missing container" }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          ...mockSettings,
          general: { ...mockSettings.general, developer_mode: true },
        }),
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
      expect(screen.getByText("Developer")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Developer"))
    fireEvent.click(screen.getByRole("button", { name: "Refresh logs" }))

    await waitFor(() => {
      expect(screen.getByText("Could not read tailBale logs: missing container")).toBeInTheDocument()
    })
    expect(screen.queryByText(/Container:/)).not.toBeInTheDocument()
  })
})

describe("SettingsDeveloperTab a11y contract", () => {
  it("labels and describes the DeveloperTab destructive reset actions", () => {
    render(<DeveloperTab />)
    const resetAll = screen.getByRole("button", { name: "Reset all" })
    const resetSetup = screen.getByRole("button", { name: "Reset setup_complete" })
    expect(describedByText(resetAll)).toContain("clears the current user")
    expect(describedByText(resetSetup)).toContain("setup wizard")
  })

  it("marks a DeveloperTab reset button aria-busy while the reset is in flight", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true)
    const { promise } = Promise.withResolvers<Response>() // never resolves: reset stays in flight
    vi.stubGlobal("fetch", vi.fn(() => promise))
    render(<DeveloperTab />)
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reset all" }))
    })
    const working = screen.getByRole("button", { name: "Working..." })
    expect(working).toHaveAttribute("aria-busy", "true")
    expect(working).toBeDisabled()
    // The sibling destructive action is also disabled while a reset runs.
    expect(screen.getByRole("button", { name: "Reset setup_complete" })).toBeDisabled()
  })
})
