import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { mockSettings, mockFetch } from "./settingsTestUtils"
import { GeneralTab } from "@/pages/settings/GeneralTab"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsGeneralTab", () => {
  it("saves developer mode from General tab", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (opts?.method === "PUT" && String(url).includes("/settings/general")) {
        const body = JSON.parse(String(opts.body))
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            ...mockSettings,
            general: { ...mockSettings.general, developer_mode: body.developer_mode },
          }),
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
      expect(screen.getByText("Developer Mode")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("checkbox"))
    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => {
      expect(screen.getByText("Developer")).toBeInTheDocument()
    })
    const putCall = fetchMock.mock.calls.find(
      (c: unknown[]) =>
        typeof c[1] === "object" &&
        (c[1] as RequestInit).method === "PUT"
    )
    expect(putCall).toBeDefined()
    expect(JSON.parse(String((putCall![1] as RequestInit).body))).toMatchObject({ developer_mode: true })

  })

  it("hides Developer tab immediately after developer mode is disabled", async () => {
    const enabledSettings = {
      ...mockSettings,
      general: { ...mockSettings.general, developer_mode: true },
    }
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (opts?.method === "PUT" && String(url).includes("/settings/general")) {
        const body = JSON.parse(String(opts.body))
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            ...mockSettings,
            general: { ...mockSettings.general, developer_mode: body.developer_mode },
          }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(enabledSettings),
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

    fireEvent.click(screen.getByRole("checkbox"))
    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => {
      expect(screen.queryByText("Developer")).not.toBeInTheDocument()
    })
  })

  it("shows General tab fields by default", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Base Domain")).toBeInTheDocument()
    })
    expect(screen.getByText("ACME Email")).toBeInTheDocument()
    expect(screen.getByText("Full reconciliation interval (seconds)")).toBeInTheDocument()
    expect(screen.getByText("Cert Renewal Window (days)")).toBeInTheDocument()
    expect(screen.getByDisplayValue("example.com")).toBeInTheDocument()
    expect(screen.getByDisplayValue("admin@example.com")).toBeInTheDocument()
  })

  it("has Save button on General tab", async () => {
    vi.stubGlobal("fetch", mockFetch())
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Save")).toBeInTheDocument()
    })
  })

  it("calls save on General tab Save click", async () => {
    const fetchMock = mockFetch()
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
      // Should have called fetch for initial load + save
      const calls = fetchMock.mock.calls
      const putCall = calls.find(
        (c: unknown[]) =>
          typeof c[1] === "object" &&
          (c[1] as RequestInit).method === "PUT"
      )
      expect(putCall).toBeDefined()
      expect(String(putCall![0])).toContain("/settings/general")
    })
  })

  it("shows API save errors without leaving the user guessing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
        if (String(url).includes("/version")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ version: "1.2.3" }),
          })
        }
        if (opts?.method === "PUT" && String(url).includes("/settings/general")) {
          return Promise.resolve({
            ok: false,
            status: 400,
            json: () => Promise.resolve({ detail: "Base domain is required" }),
          })
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      })
    )
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
    // The save error is injected asynchronously and must announce to assistive
    // tech via a live region (role="alert"), matching the load-error banner.
    expect(screen.getByRole("alert")).toHaveTextContent("Base domain is required")
  })

  it("shows version in General tab", async () => {
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

    // General tab is the default, no click needed
    await waitFor(() => {
      expect(screen.getByText("tailBale")).toBeInTheDocument()
    })
    expect(screen.getByText("v1.2.3")).toBeInTheDocument()
  })

  it("shows version unknown when version endpoint fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes("/version")) {
          return Promise.reject(new Error("Not found"))
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      })
    )
    const { default: SettingsPage } = await import("@/pages/SettingsPage")
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    // General tab is the default
    expect(screen.getByText("version unknown")).toBeInTheDocument()
  })
})

describe("SettingsGeneralTab dirty-state guards", () => {
  const general = mockSettings.general

  it("keeps an edited field when a background refresh pushes new props (GeneralTab)", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <GeneralTab settings={general} onSave={onSave} saving={false} version={null} />
    )

    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "user-typed.com" } })
    expect(screen.getByDisplayValue("user-typed.com")).toBeInTheDocument()

    // Background refresh: parent pushes a fresh settings object with a new value.
    rerender(
      <GeneralTab
        settings={{ ...general, base_domain: "server-pushed.com" }}
        onSave={onSave}
        saving={false}
        version={null}
      />
    )

    expect(screen.getByDisplayValue("user-typed.com")).toBeInTheDocument()
    expect(screen.queryByDisplayValue("server-pushed.com")).not.toBeInTheDocument()
  })

  it("adopts the new server value for an untouched field on that same refresh (GeneralTab)", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <GeneralTab settings={general} onSave={onSave} saving={false} version={null} />
    )

    // Only base_domain is edited; acme_email stays untouched.
    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "user-typed.com" } })

    rerender(
      <GeneralTab
        settings={{ ...general, base_domain: "server.com", acme_email: "new@server.com" }}
        onSave={onSave}
        saving={false}
        version={null}
      />
    )

    // Dirty field keeps the user value; untouched field adopts the server value.
    expect(screen.getByDisplayValue("user-typed.com")).toBeInTheDocument()
    expect(screen.getByDisplayValue("new@server.com")).toBeInTheDocument()
  })

  it("reflects a server-normalized value after a successful save clears dirty (GeneralTab)", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <GeneralTab settings={general} onSave={onSave} saving={false} version={null} />
    )

    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "user-typed.com" } })
    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ base_domain: "user-typed.com" }))

    // Parent applies the server-normalized response; dirty was cleared on save.
    rerender(
      <GeneralTab
        settings={{ ...general, base_domain: "normalized.com" }}
        onSave={onSave}
        saving={false}
        version={null}
      />
    )
    expect(screen.getByDisplayValue("normalized.com")).toBeInTheDocument()
  })

  it("keeps the edited value when a save rejects so the user can retry (GeneralTab)", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockRejectedValue(new Error("save failed"))
    const { rerender } = render(
      <GeneralTab settings={general} onSave={onSave} saving={false} version={null} />
    )

    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "user-typed.com" } })
    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })
    expect(onSave).toHaveBeenCalled()

    // Dirty was NOT cleared (save threw): a later refresh must not clobber the edit.
    rerender(
      <GeneralTab
        settings={{ ...general, base_domain: "server.com" }}
        onSave={onSave}
        saving={false}
        version={null}
      />
    )
    expect(screen.getByDisplayValue("user-typed.com")).toBeInTheDocument()
    expect(screen.queryByDisplayValue("server.com")).not.toBeInTheDocument()
  })
})

describe("SettingsGeneralTab numeric validation", () => {
  const general = mockSettings.general

  it("shows an error and disables Save when the reconcile interval is cleared, firing no save", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    // Save is enabled with the seeded valid values.
    expect(screen.getByText("Save")).not.toBeDisabled()

    // Clear the reconcile interval (value "60" -> "").
    fireEvent.change(screen.getByDisplayValue("60"), { target: { value: "" } })

    expect(screen.getByText("Must be a whole number of at least 1")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()

    // A click on the disabled button must not fire a save (no PUT).
    fireEvent.click(saveBtn)
    expect(onSave).not.toHaveBeenCalled()
  })

  it("shows an error and disables Save when the cert renewal window is zeroed", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    // Zero the renewal window (value "30" -> "0"); 0 < 1 is invalid.
    fireEvent.change(screen.getByDisplayValue("30"), { target: { value: "0" } })

    expect(screen.getByText("Must be a whole number from 1 to 10000")).toBeInTheDocument()
    expect(screen.getByText("Save")).toBeDisabled()
  })

  it("shows an error and disables Save when the cert renewal window exceeds the backend cap (le=10000)", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    // The backend caps cert_renewal_window_days at Field(le=10000); a larger value
    // passes a naive ">= 1" check but 422s server-side ("less than or equal to
    // 10000"). The client must mirror the upper bound and block the doomed PUT.
    fireEvent.change(screen.getByDisplayValue("30"), { target: { value: "20000" } })

    expect(screen.getByText("Must be a whole number from 1 to 10000")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()
    fireEvent.click(saveBtn)
    expect(onSave).not.toHaveBeenCalled()
  })

  it("shows an error and disables Save for a fractional value the int field would reject", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    // A decimal passes a naive ">= 1" check but the backend `int` field 422s on
    // it ("Input should be a valid integer"). The client must block the save.
    fireEvent.change(screen.getByDisplayValue("60"), { target: { value: "60.5" } })

    expect(screen.getByText("Must be a whole number of at least 1")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()
    fireEvent.click(saveBtn)
    expect(onSave).not.toHaveBeenCalled()
  })

  it("saves normally once both numeric fields are valid (>= 1)", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    fireEvent.change(screen.getByDisplayValue("60"), { target: { value: "120" } })
    fireEvent.change(screen.getByDisplayValue("30"), { target: { value: "15" } })

    expect(screen.queryByText("Must be a whole number of at least 1")).not.toBeInTheDocument()
    expect(screen.getByText("Save")).not.toBeDisabled()

    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ reconcile_interval_seconds: 120, cert_renewal_window_days: 15 })
    )
  })

  it("gives the numeric fields native min/max/step bounds matching the backend (FN1)", () => {
    // The peer number inputs (ExposeService, ServiceEditForm) set native
    // min/max/step; the settings numeric fields must do the same so the spinner
    // cannot step to 0/negative/fractional values and AT can announce the range.
    // cert_renewal_window_days additionally mirrors the backend Field(le=10000).
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    for (const displayValue of ["60", "45", "90"]) {
      const input = screen.getByDisplayValue(displayValue)
      expect(input).toHaveAttribute("type", "number")
      expect(input).toHaveAttribute("min", "1")
      expect(input).toHaveAttribute("step", "1")
      expect(input).not.toHaveAttribute("max")
    }

    const renewal = screen.getByDisplayValue("30")
    expect(renewal).toHaveAttribute("min", "1")
    expect(renewal).toHaveAttribute("max", "10000")
    expect(renewal).toHaveAttribute("step", "1")
  })
})

describe("SettingsGeneralTab retention & health-check fields", () => {
  const general = mockSettings.general

  it("renders the new fields with their current values and the relabeled reconcile interval", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    expect(screen.getByText("Health check interval (seconds)")).toBeInTheDocument()
    expect(screen.getByDisplayValue("45")).toBeInTheDocument()
    expect(screen.getByText("Keep event log for (days)")).toBeInTheDocument()
    expect(screen.getByDisplayValue("90")).toBeInTheDocument()
    expect(screen.getByText("Full reconciliation interval (seconds)")).toBeInTheDocument()
  })

  it("includes an edited event_retention_days (and the other fields) in the save payload", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    fireEvent.change(screen.getByDisplayValue("90"), { target: { value: "120" } })
    expect(screen.getByText("Save")).not.toBeDisabled()

    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        event_retention_days: 120,
        health_check_interval_seconds: 45,
        reconcile_interval_seconds: 60,
      })
    )
  })

  it("blocks the save when event_retention_days is cleared", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    fireEvent.change(screen.getByDisplayValue("90"), { target: { value: "" } })

    expect(screen.getByText("Must be a whole number of at least 1")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()
    fireEvent.click(saveBtn)
    expect(onSave).not.toHaveBeenCalled()
  })

  it("blocks the save when the health check interval is zeroed", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    fireEvent.change(screen.getByDisplayValue("45"), { target: { value: "0" } })

    expect(screen.getByText("Must be a whole number of at least 1")).toBeInTheDocument()
    expect(screen.getByText("Save")).toBeDisabled()
    fireEvent.click(screen.getByText("Save"))
    expect(onSave).not.toHaveBeenCalled()
  })
})

describe("SettingsGeneralTab required text-field validation", () => {
  const general = mockSettings.general

  it("disables Save and shows an inline error when Base Domain is cleared, firing no save", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    expect(screen.getByText("Save")).not.toBeDisabled()

    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "" } })

    expect(screen.getByText("Required — cannot be blank")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()
    fireEvent.click(saveBtn)
    expect(onSave).not.toHaveBeenCalled()
  })

  it("treats a whitespace-only Base Domain as blank (matching the backend strip)", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "   " } })

    expect(screen.getByText("Required — cannot be blank")).toBeInTheDocument()
    expect(screen.getByText("Save")).toBeDisabled()
  })

  it("disables Save and shows an inline error when ACME Email is cleared", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    fireEvent.change(screen.getByDisplayValue("admin@example.com"), { target: { value: "" } })

    expect(screen.getByText("Required — cannot be blank")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()
    fireEvent.click(saveBtn)
    expect(onSave).not.toHaveBeenCalled()
  })

  it("disables Save and shows a format error for a malformed (non-blank) ACME email (FSA2)", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    // Non-blank but not an email shape: the backend acme_email validator 422s
    // ("Invalid email address"), so the client must block the save with a
    // format-specific message rather than the blank "Required" one.
    fireEvent.change(screen.getByDisplayValue("admin@example.com"), { target: { value: "not-an-email" } })

    expect(screen.getByText("Enter a valid email address")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()
    fireEvent.click(saveBtn)
    expect(onSave).not.toHaveBeenCalled()

    // A well-formed address clears the error and re-enables Save.
    fireEvent.change(screen.getByDisplayValue("not-an-email"), { target: { value: "ops@new.io" } })
    expect(screen.queryByText("Enter a valid email address")).not.toBeInTheDocument()
    expect(screen.getByText("Save")).not.toBeDisabled()
  })

  it("re-enables Save and saves once the Base Domain is refilled", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "" } })
    expect(screen.getByText("Save")).toBeDisabled()

    fireEvent.change(screen.getByDisplayValue("admin@example.com"), { target: { value: "ops@new.com" } })
    // Base domain still blank — Save stays disabled.
    expect(screen.getByText("Save")).toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("mydomain.com"), { target: { value: "new.com" } })
    expect(screen.queryByText("Required — cannot be blank")).not.toBeInTheDocument()
    expect(screen.getByText("Save")).not.toBeDisabled()

    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ base_domain: "new.com", acme_email: "ops@new.com" })
    )
  })

  it("fires no PUT when Base Domain is cleared in the live page (no doomed 422)", async () => {
    const fetchMock = mockFetch()
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

    fireEvent.change(screen.getByDisplayValue("example.com"), { target: { value: "" } })
    fireEvent.click(screen.getByText("Save"))

    const firedPut = fetchMock.mock.calls.some(
      (c: unknown[]) => typeof c[1] === "object" && (c[1] as RequestInit).method === "PUT"
    )
    expect(firedPut).toBe(false)
  })
})

describe("SettingsGeneralTab timezone select (SPJ3)", () => {
  const general = mockSettings.general

  it("renders the timezone dropdown populated from Intl.supportedValuesOf", async () => {
    // Dynamic import is the established pattern in this file (module-load boundary).
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)

    expect(screen.getByText("Timezone")).toBeInTheDocument()
    // The single <select> is the only timezone input path, populated from the
    // full IANA list when the API is available, plus a guaranteed UTC + current
    // value so the stored zone is always selectable.
    const select = screen.getByRole("combobox") as HTMLSelectElement
    expect(select.options.length).toBeGreaterThanOrEqual(Intl.supportedValuesOf("timeZone").length)
    expect(screen.getByRole("option", { name: "UTC" })).toBeInTheDocument()
    expect(select.options.length).toBeGreaterThan(1)
  })

  it("still renders the current timezone option when Intl.supportedValuesOf is unavailable", async () => {
    const { GeneralTab } = await import("@/pages/settings/GeneralTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const intlRef = Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
    const original = intlRef.supportedValuesOf
    // Simulate a runtime without the API: without the guard this render throws
    // "Intl.supportedValuesOf is not a function" and crashes the General tab.
    intlRef.supportedValuesOf = undefined
    try {
      render(<GeneralTab settings={general} onSave={onSave} saving={false} version={null} />)
      expect(screen.getByText("Timezone")).toBeInTheDocument()
      const select = screen.getByRole("combobox") as HTMLSelectElement
      expect(select.value).toBe("UTC")
      expect(screen.getByRole("option", { name: "UTC" })).toBeInTheDocument()
    } finally {
      intlRef.supportedValuesOf = original
    }
  })
})
