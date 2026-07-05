import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { cachedTimezone, _resetTimezoneCache } from "@/lib/useTimezone"

const mockSettings = {
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

  it("surfaces a thrown connection-test error as a failure banner", async () => {
    // When the test endpoint errors (not a server-returned {success:false}), the
    // request throws and runTest's catch must convert it into a failure result
    // so the user sees WHY the probe failed instead of a silent no-op.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes("/version")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve({ version: "1.2.3" }) })
        }
        if (String(url).includes("/settings/test/docker")) {
          return Promise.resolve({
            ok: false,
            status: 500,
            json: () => Promise.resolve({ detail: "Docker daemon unreachable" }),
          })
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
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
      expect(screen.getByText("Docker daemon unreachable")).toBeInTheDocument()
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

/**
 * Per-field dirty-state guards (refresh safety).
 *
 * A background settings refresh (focus-refetch / poll, to be added later) pushes
 * a new `settings` object into a tab. The tab's prop-sync effect must NOT clobber
 * a field the user is actively editing, while still adopting server values for
 * untouched fields. Dirty marks clear only after a SUCCESSFUL save.
 *
 * These drive the real tab components and push new props (simulating the parent
 * applying a refreshed `settings`). They fail on the old unconditional-resync code.
 */
describe("SettingsPage tab dirty-state guards", () => {
  const general = mockSettings.general
  const cloudflare = mockSettings.cloudflare
  const tailscale = mockSettings.tailscale
  const docker = mockSettings.docker
  const paths = mockSettings.paths

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

  it("guards edited Paths fields on refresh while untouched ones update (PathsTab)", async () => {
    const { PathsTab } = await import("@/pages/settings/PathsTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <PathsTab settings={paths} onSave={onSave} saving={false} />
    )

    fireEvent.change(screen.getByDisplayValue("data/generated"), { target: { value: "custom/generated" } })

    rerender(
      <PathsTab settings={{ ...paths, generated_root: "srv/generated", cert_root: "srv/certs" }} onSave={onSave} saving={false} />
    )

    expect(screen.getByDisplayValue("custom/generated")).toBeInTheDocument()
    expect(screen.getByDisplayValue("srv/certs")).toBeInTheDocument()
    expect(screen.queryByDisplayValue("srv/generated")).not.toBeInTheDocument()
  })

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

  it("guards an edited Docker socket path on refresh and adopts a server change once saved (DockerTab)", async () => {
    // DockerTab tracks its single socket_path field through useDirtyForm.
    const { DockerTab } = await import("@/pages/settings/DockerTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <DockerTab settings={docker} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )

    fireEvent.change(screen.getByDisplayValue("unix:///var/run/docker.sock"), { target: { value: "tcp://user:2375" } })

    // A background refresh must NOT clobber the live edit.
    rerender(
      <DockerTab settings={{ ...docker, socket_path: "tcp://server:2375" }} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )
    expect(screen.getByDisplayValue("tcp://user:2375")).toBeInTheDocument()
    expect(screen.queryByDisplayValue("tcp://server:2375")).not.toBeInTheDocument()

    // After a successful save clears dirty, the server-normalized value is adopted.
    await act(async () => {
      fireEvent.click(screen.getByText("Save"))
    })
    expect(onSave).toHaveBeenCalledWith({ socket_path: "tcp://user:2375" })
    rerender(
      <DockerTab settings={{ ...docker, socket_path: "unix:///normalized.sock" }} onSave={onSave} onTest={() => {}} saving={false} testing={false} testResult={null} />
    )
    expect(screen.getByDisplayValue("unix:///normalized.sock")).toBeInTheDocument()
  })
})

/**
 * GeneralTab numeric-field validation (#9).
 *
 * `reconcile_interval_seconds` / `cert_renewal_window_days` map to backend
 * `Field(ge=1)`. A cleared input `Number('')`s to 0, which the API 422s. The tab
 * must surface an inline error and disable Save (so no doomed PUT fires) while a
 * field is empty or < 1, and save normally once both are valid (>= 1). The
 * pre-existing per-field dirty guard must stay intact (covered above).
 */
describe("SettingsPage GeneralTab numeric validation", () => {
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

    expect(screen.getByText("Must be a whole number of at least 1")).toBeInTheDocument()
    expect(screen.getByText("Save")).toBeDisabled()
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
})

/**
 * GeneralTab retention / health-check runtime settings (Wave 3b).
 *
 * `event_retention_days` and `health_check_interval_seconds` are new
 * `Field(ge=1)` ints on the General settings PUT, and the reconcile interval is
 * now the SLOW full-reconcile cadence. These cover rendering, save payload, and
 * the shared positive-int gate (0/blank rejected before a doomed PUT).
 */
describe("SettingsPage GeneralTab retention & health-check fields", () => {
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

/**
 * Required text-field validation (#13).
 *
 * `zone_id`, `control_url`, `default_ts_hostname_prefix`, and `socket_path` are
 * `Field(min_length=1)` server-side, so a blank value 422s with no guidance.
 * Mirroring GeneralTab's numeric gating, the Cloudflare / Tailscale / Docker
 * tabs must surface an inline error and disable Save (firing no doomed PUT)
 * while a required text field is blank.
 */
describe("SettingsPage required text-field validation", () => {
  async function renderSettings(fetchMock: ReturnType<typeof vi.fn>) {
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

  function firedPut(fetchMock: ReturnType<typeof vi.fn>) {
    return fetchMock.mock.calls.some(
      (c: unknown[]) => typeof c[1] === "object" && (c[1] as RequestInit).method === "PUT"
    )
  }

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

  it("disables Docker Save and shows an inline error when the socket path is cleared, firing no PUT", async () => {
    const fetchMock = mockFetch()
    await renderSettings(fetchMock)

    fireEvent.click(screen.getByText("Docker"))
    expect(screen.getByText("Save")).not.toBeDisabled()

    fireEvent.change(screen.getByPlaceholderText("unix:///var/run/docker.sock"), { target: { value: "" } })

    expect(screen.getByText("Required — cannot be blank")).toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).toBeDisabled()

    fireEvent.click(saveBtn)
    expect(firedPut(fetchMock)).toBe(false)
  })
})

/**
 * Shared error banner reset on tab change (#14).
 *
 * The `error` banner is shared across tabs, so an error raised on one tab must
 * be cleared when the user switches tabs — otherwise a stale, misleading message
 * lingers on an unrelated tab.
 */
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

/**
 * Per-service scoping of the connection-test / save busy flags.
 *
 * `testing` / `saving` used to be shared booleans handed to every tab, so an
 * in-flight (or hung) test on one tab disabled the Test/Save buttons on ALL
 * tabs, and a late test result could overwrite a newer one. The flags are now
 * scoped to the active service (mirroring the existing testResult scoping) and a
 * seq guard drops stale results.
 */
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

/**
 * GeneralTab required text-field validation (FSA1).
 *
 * `base_domain` and `acme_email` are `Field(min_length=1)` server-side (the
 * schema strips before the length check), exactly like the Cloudflare/Tailscale/
 * Docker text fields gated in #13 — but GeneralTab previously gated only its
 * numeric fields, so clearing either text field fired a doomed 422 PUT. The tab
 * must surface an inline error and disable Save while either is blank/whitespace.
 */
describe("SettingsPage GeneralTab required text-field validation", () => {
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

/**
 * GeneralTab timezone select robustness (SPJ3).
 *
 * The timezone field is a constrained <select> populated from
 * `Intl.supportedValuesOf('timeZone')`. That call was unguarded, so on a runtime
 * lacking `Intl.supportedValuesOf` the option `.map` throws and crashes the
 * General tab. The guard falls back to at least the current timezone + UTC so the
 * select still renders.
 */
describe("SettingsPage GeneralTab timezone select (SPJ3)", () => {
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
