import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, act } from "@testing-library/react"
import { TailscaleTab } from "@/pages/settings/TailscaleTab"
import { mockSettings, mockFetch, renderSettings, describedByText } from "./settingsTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsTailscaleTab dirty-state guards", () => {
  const tailscale = mockSettings.tailscale

  it("guards an edited Tailscale field on refresh while an untouched one updates (TailscaleTab)", async () => {
    // TailscaleTab tracks two fields (control_url + default_ts_hostname_prefix)
    // through useDirtyForm. A mis-wired extract/bind key (e.g. binding the wrong
    // field, or dropping one from `extract`) would let a background refresh
    // clobber a live edit or fail to adopt an untouched server change — the exact
    // regression the hook was extracted to prevent. Drive the real component.
    const { TailscaleTab } = await import("@/pages/settings/TailscaleTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <TailscaleTab settings={tailscale} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )

    // Edit ONLY the hostname prefix; leave control_url untouched.
    fireEvent.change(screen.getByDisplayValue("edge"), { target: { value: "user-prefix" } })

    rerender(
      <TailscaleTab
        settings={{ ...tailscale, control_url: "https://server.example.com", default_ts_hostname_prefix: "server-prefix" }}
        onSave={onSave}
        onTest={() => {}}
        saving={false}
        testing={false}
        testResult={null}
      />
    )

    // Edited prefix keeps the user value; untouched control_url adopts the server value.
    expect(screen.getByDisplayValue("user-prefix")).toBeInTheDocument()
    expect(screen.queryByDisplayValue("server-prefix")).not.toBeInTheDocument()
    expect(screen.getByDisplayValue("https://server.example.com")).toBeInTheDocument()
  })

  it("keeps the typed secret keys and edited control URL when a Tailscale save rejects", async () => {
    // Auth/API keys are write-only state (never seeded from settings) and must be
    // retained on a failed save so the user can retry, while the dirty control_url
    // must not be clobbered by a subsequent background refresh.
    const { TailscaleTab } = await import("@/pages/settings/TailscaleTab")
    const onSave = vi.fn().mockRejectedValue(new Error("save failed"))
    const { rerender } = render(
      <TailscaleTab settings={tailscale} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )

    fireEvent.change(screen.getByDisplayValue("https://controlplane.tailscale.com"), { target: { value: "https://edited.example.com" } })
    fireEvent.change(screen.getByPlaceholderText("tskey-auth-..."), { target: { value: "tskey-auth-secret" } })
    fireEvent.change(screen.getByPlaceholderText("tskey-api-..."), { target: { value: "tskey-api-secret" } })
    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({ control_url: "https://edited.example.com", auth_key: "tskey-auth-secret", api_key: "tskey-api-secret" })
    )

    // Failed save retains the secrets for a retry.
    expect(screen.getByPlaceholderText("tskey-auth-...")).toHaveValue("tskey-auth-secret")
    expect(screen.getByPlaceholderText("tskey-api-...")).toHaveValue("tskey-api-secret")

    // And a background refresh must not clobber the still-dirty control URL.
    rerender(
      <TailscaleTab settings={{ ...tailscale, control_url: "https://server.example.com" }} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )
    expect(screen.getByDisplayValue("https://edited.example.com")).toBeInTheDocument()
    expect(screen.queryByDisplayValue("https://server.example.com")).not.toBeInTheDocument()
  })

  it("clears the Tailscale secret keys after a successful save while adopting the server value", async () => {
    const { TailscaleTab } = await import("@/pages/settings/TailscaleTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <TailscaleTab settings={tailscale} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )

    fireEvent.change(screen.getByPlaceholderText("tskey-auth-..."), { target: { value: "tskey-auth-secret" } })
    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })

    // Secret cleared on success (existing write-only behavior preserved).
    expect(screen.getByPlaceholderText("tskey-auth-...")).toHaveValue("")

    // Dirty cleared: a server-normalized prefix is now reflected on refresh.
    rerender(
      <TailscaleTab settings={{ ...tailscale, default_ts_hostname_prefix: "normalized-prefix" }} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )
    expect(screen.getByDisplayValue("normalized-prefix")).toBeInTheDocument()
  })
})

describe("SettingsTailscaleTab required text-field validation", () => {
  it("disables Tailscale Save when Control URL is blank and re-enables once refilled", async () => {
    const fetchMock = mockFetch()
    await renderSettings(fetchMock)

    fireEvent.click(screen.getByText("Tailscale"))
    const controlUrl = screen.getByPlaceholderText("https://controlplane.tailscale.com")
    expect(screen.getByText("Save")).not.toBeDisabled()

    // Whitespace-only is blank server-side (the schema strips before min_length).
    fireEvent.change(controlUrl, { target: { value: "   " } })
    expect(screen.getByText("Required — cannot be blank")).toBeInTheDocument()
    expect(screen.getByText("Save")).toBeDisabled()

    fireEvent.change(controlUrl, { target: { value: "https://login.example.com" } })
    expect(screen.queryByText("Required — cannot be blank")).not.toBeInTheDocument()
    expect(screen.getByText("Save")).not.toBeDisabled()
  })
})

describe("SettingsTailscaleTab a11y contract", () => {
  const tailscale = mockSettings.tailscale

  it("describes both Tailscale secret inputs with their SecretStatus (Configured / Not set)", () => {
    render(<TailscaleTab settings={tailscale} onSave={vi.fn()} onTest={vi.fn()} saving={false} testing={false} testResult={null} />)
    // auth_key_configured: true, api_key_configured: false in the fixture.
    expect(describedByText(screen.getByLabelText("Auth Key"))).toContain("Configured")
    expect(describedByText(screen.getByLabelText("API Key"))).toContain("Not set")
  })
})
