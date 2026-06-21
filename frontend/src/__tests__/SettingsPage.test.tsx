import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

const mockSettings = {
  general: {
    base_domain: "example.com",
    acme_email: "admin@example.com",
    reconcile_interval_seconds: 60,
    cert_renewal_window_days: 30,
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

beforeEach(() => {
  vi.restoreAllMocks()
})

/** Builds a fetch mock that returns settings for /settings, version for /version, and optional overrides. */
function mockFetch(data: unknown = mockSettings) {
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
    expect(screen.getByText("Reconcile Interval (seconds)")).toBeInTheDocument()
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

  it("shows test result on connection test", async () => {
    let callCount = 0
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        callCount++
        if (callCount <= 2) {
          if (callCount === 2) {
            return Promise.resolve({
              ok: true,
              json: () => Promise.resolve({ version: "1.2.3" }),
            })
          }
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockSettings),
          })
        }
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({ success: true, message: "Docker is reachable" }),
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

    fireEvent.click(screen.getByText("Docker"))
    fireEvent.click(screen.getByText("Test Connection"))

    await waitFor(() => {
      expect(screen.getByText("Docker is reachable")).toBeInTheDocument()
    })
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

  it("keeps connection test loading button accessible", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ version: "1.2.3" }),
        })
      }
      if (String(url).includes("/settings/test/docker")) {
        return new Promise(() => {})
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

    expect(screen.getByRole("button", { name: "Testing..." })).toBeDisabled()
  })

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
