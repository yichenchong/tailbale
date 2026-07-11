import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { mockFetch } from "./settingsTestUtils"
import { AccountTab } from "@/pages/settings/AccountTab"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsAccountTab", () => {
  it("shows Account tab with password change form", async () => {
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

    expect(screen.getByText("Account")).toBeInTheDocument()
    fireEvent.click(screen.getByText("Account"))

    expect(screen.getByRole("button", { name: "Change Password" })).toBeInTheDocument()
    expect(screen.getByText("Current Password")).toBeInTheDocument()
    expect(screen.getByText("New Password")).toBeInTheDocument()
    expect(screen.getByText("Confirm New Password")).toBeInTheDocument()
  })

  it("disables Change Password button when fields are empty", async () => {
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

    fireEvent.click(screen.getByText("Account"))

    const btn = screen.getByRole("button", { name: "Change Password" })
    expect(btn).toBeDisabled()
  })

  it("shows mismatch warning when passwords differ", async () => {
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

    fireEvent.click(screen.getByText("Account"))

    // Fill in new password
    const newPwInput = screen.getByPlaceholderText("Minimum 8 characters")
    fireEvent.change(newPwInput, { target: { value: "newpassword1" } })

    // Fill in confirm with different value
    const confirmInput = screen.getByPlaceholderText("Confirm new password")
    fireEvent.change(confirmInput, { target: { value: "differentpass" } })

    expect(screen.getByText("Passwords do not match.")).toBeInTheDocument()
  })
})

describe("SettingsAccountTab password change submit", () => {
  // The submit path (clear-on-success, error retention on failure) was
  // previously uncovered even though settingsTestUtils.mockFetch already models
  // both the success and the wrong-current-password (401) branches. AccountTab
  // is self-contained (no router deps), so drive it directly.
  function fill(current: string, next: string, confirm: string) {
    fireEvent.change(screen.getByPlaceholderText("Enter current password"), { target: { value: current } })
    fireEvent.change(screen.getByPlaceholderText("Minimum 8 characters"), { target: { value: next } })
    fireEvent.change(screen.getByPlaceholderText("Confirm new password"), { target: { value: confirm } })
  }

  it("clears every field and shows the success status after a successful change", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<AccountTab />)

    fill("oldpassword", "newpassword1", "newpassword1")
    fireEvent.click(screen.getByRole("button", { name: "Change Password" }))

    await waitFor(() => {
      expect(screen.getByText("Password changed successfully")).toBeInTheDocument()
    })
    // Fields are reset ONLY on the success branch.
    expect((screen.getByPlaceholderText("Enter current password") as HTMLInputElement).value).toBe("")
    expect((screen.getByPlaceholderText("Minimum 8 characters") as HTMLInputElement).value).toBe("")
    expect((screen.getByPlaceholderText("Confirm new password") as HTMLInputElement).value).toBe("")
  })

  it("surfaces the server error and keeps the typed passwords when the current password is wrong", async () => {
    vi.stubGlobal("fetch", mockFetch())
    render(<AccountTab />)

    // mockFetch returns 401 { detail: "Current password is incorrect" } for this
    // value. A 401 on an /auth/ path must NOT bounce to /login (api.ts guards
    // paths under /auth/), so the specific server message reaches the banner
    // instead of the "Session expired" redirect error.
    fill("wrongpassword", "newpassword1", "newpassword1")
    fireEvent.click(screen.getByRole("button", { name: "Change Password" }))

    await waitFor(() => {
      expect(screen.getByText("Current password is incorrect")).toBeInTheDocument()
    })
    // A failed change retains the entered values so the user can retry.
    expect((screen.getByPlaceholderText("Enter current password") as HTMLInputElement).value).toBe("wrongpassword")
    expect((screen.getByPlaceholderText("Minimum 8 characters") as HTMLInputElement).value).toBe("newpassword1")
    expect((screen.getByPlaceholderText("Confirm new password") as HTMLInputElement).value).toBe("newpassword1")
  })
})
