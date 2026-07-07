import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { mockFetch } from "./settingsTestUtils"

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
