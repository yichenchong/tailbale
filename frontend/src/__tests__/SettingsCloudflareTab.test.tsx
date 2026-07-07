import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { CloudflareTab } from "@/pages/settings/CloudflareTab"
import { mockSettings, mockFetch, renderSettings, firedPut, describedByText } from "./settingsTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsCloudflareTab", () => {
  it("shows Not set when secrets not configured", async () => {
    const noSecrets = {
      ...mockSettings,
      cloudflare: { zone_id: "", token_configured: false },
      tailscale: {
        ...mockSettings.tailscale,
        auth_key_configured: false,
      },
    }
    vi.stubGlobal("fetch", mockFetch(noSecrets))
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
    expect(screen.getByText("Not set")).toBeInTheDocument()
  })

  it("clears saved Cloudflare secret input and refreshes returned settings", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (opts?.method === "PUT" && String(url).includes("/settings/cloudflare")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            ...mockSettings,
            cloudflare: { zone_id: "normalized-zone", token_configured: true },
          }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          ...mockSettings,
          cloudflare: { zone_id: "", token_configured: false },
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
      expect(screen.getByText("Settings")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Cloudflare"))
    fireEvent.change(screen.getByPlaceholderText("Cloudflare zone ID"), { target: { value: "zone123" } })
    fireEvent.change(screen.getByPlaceholderText("Enter new token to update"), { target: { value: "secret-token" } })
    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => {
      expect(screen.getByDisplayValue("normalized-zone")).toBeInTheDocument()
    })
    expect(screen.getByPlaceholderText("Enter new token to update")).toHaveValue("")
    expect(screen.getByText("Configured")).toBeInTheDocument()
  })
})

describe("SettingsCloudflareTab dirty-state guards", () => {
  const cloudflare = mockSettings.cloudflare

  it("clears the secret token and dirty zone after a successful Cloudflare save", async () => {
    const { CloudflareTab } = await import("@/pages/settings/CloudflareTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <CloudflareTab settings={cloudflare} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )

    fireEvent.change(screen.getByPlaceholderText("Cloudflare zone ID"), { target: { value: "edited-zone" } })
    fireEvent.change(screen.getByPlaceholderText("Enter new token to update"), { target: { value: "secret-token" } })
    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })
    expect(onSave).toHaveBeenCalledWith({ zone_id: "edited-zone", token: "secret-token" })

    // Secret input cleared on success (existing behavior preserved).
    expect(screen.getByPlaceholderText("Enter new token to update")).toHaveValue("")

    // Dirty cleared: the server-normalized zone is now reflected on refresh.
    rerender(
      <CloudflareTab settings={{ ...cloudflare, zone_id: "normalized-zone" }} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )
    expect(screen.getByDisplayValue("normalized-zone")).toBeInTheDocument()
  })

  it("keeps the typed secret and edited zone when a Cloudflare save rejects", async () => {
    const { CloudflareTab } = await import("@/pages/settings/CloudflareTab")
    const onSave = vi.fn().mockRejectedValue(new Error("save failed"))
    const { rerender } = render(
      <CloudflareTab settings={cloudflare} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )

    fireEvent.change(screen.getByPlaceholderText("Cloudflare zone ID"), { target: { value: "edited-zone" } })
    fireEvent.change(screen.getByPlaceholderText("Enter new token to update"), { target: { value: "secret-token" } })
    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })

    // Failed save retains the secret so the user can retry it.
    expect(screen.getByPlaceholderText("Enter new token to update")).toHaveValue("secret-token")

    // And a background refresh must not clobber the still-dirty zone.
    rerender(
      <CloudflareTab settings={{ ...cloudflare, zone_id: "server-zone" }} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )
    expect(screen.getByDisplayValue("edited-zone")).toBeInTheDocument()
    expect(screen.queryByDisplayValue("server-zone")).not.toBeInTheDocument()
  })
})

describe("SettingsCloudflareTab required text-field validation", () => {
  it("disables Cloudflare Save and shows an inline error when Zone ID is cleared, firing no PUT", async () => {
    const fetchMock = mockFetch()
    await renderSettings(fetchMock)

    fireEvent.click(screen.getByText("Cloudflare"))
    expect(screen.getByText("Save")).not.toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("Cloudflare zone ID"), { target: { value: "" } })

    expect(screen.getByText("Required — cannot be blank")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()

    fireEvent.click(saveBtn)
    expect(firedPut(fetchMock)).toBe(false)
  })
})

describe("SettingsCloudflareTab a11y contract", () => {
  const cloudflare = mockSettings.cloudflare

  it("labels the Cloudflare fields via htmlFor so getByLabelText resolves each input", () => {
    render(<CloudflareTab settings={cloudflare} onSave={vi.fn()} onTest={vi.fn()} saving={false} testing={false} testResult={null} />)
    expect(screen.getByLabelText("Zone ID")).toHaveValue("zone123")
    expect(screen.getByLabelText("API Token")).toHaveAttribute("type", "password")
  })

  it("describes the Cloudflare secret input with its SecretStatus + hint via aria-describedby", () => {
    render(<CloudflareTab settings={cloudflare} onSave={vi.fn()} onTest={vi.fn()} saving={false} testing={false} testResult={null} />)
    const token = screen.getByLabelText("API Token")
    const described = describedByText(token)
    // token_configured: true -> SecretStatus renders "Configured"; hint is also announced.
    expect(described).toContain("Configured")
    expect(described).toContain("Write-only")
  })

  it("exposes aria-busy on the connection Test button while a test runs", () => {
    render(<CloudflareTab settings={cloudflare} onSave={vi.fn()} onTest={vi.fn()} saving={false} testing={true} testResult={null} />)
    const testBtn = screen.getByRole("button", { name: "Testing..." })
    expect(testBtn).toHaveAttribute("aria-busy", "true")
    expect(testBtn).toBeDisabled()
  })

  it("exposes aria-busy on the Save button while saving", () => {
    render(<CloudflareTab settings={cloudflare} onSave={vi.fn()} onTest={vi.fn()} saving={true} testing={false} testResult={null} />)
    expect(screen.getByRole("button", { name: "Saving..." })).toHaveAttribute("aria-busy", "true")
  })
})
