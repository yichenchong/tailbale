import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { DockerTab } from "@/pages/settings/DockerTab"
import { mockSettings, mockFetch, renderSettings, firedPut } from "./settingsTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsDockerTab", () => {
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
    // The test result is injected asynchronously after a deliberate action, so it
    // must announce to assistive tech via a polite live region (role="status").
    expect(screen.getByRole("status")).toHaveTextContent("Docker is reachable")
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
})

describe("SettingsDockerTab dirty-state guards", () => {
  const docker = mockSettings.docker

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

describe("SettingsDockerTab Docker environment fallback", () => {
  it("allows saving a blank socket path so Docker can use DOCKER_HOST/from_env", async () => {
    const fetchMock = mockFetch()
    await renderSettings(fetchMock)

    fireEvent.click(screen.getByText("Docker"))
    fireEvent.change(screen.getByPlaceholderText("Leave blank to use DOCKER_HOST / docker.from_env()"), { target: { value: "" } })

    expect(screen.queryByText("Required — cannot be blank")).not.toBeInTheDocument()
    const saveBtn = screen.getByText("Save")
    expect(saveBtn).not.toBeDisabled()

    fireEvent.click(saveBtn)
    await waitFor(() => {
      expect(firedPut(fetchMock)).toBe(true)
    })
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/settings/docker",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ socket_path: "" }),
      }),
    )
  })
})

describe("SettingsDockerTab a11y contract", () => {
  const docker = mockSettings.docker

  it("labels the Docker socket-path field via htmlFor", () => {
    render(<DockerTab settings={docker} onSave={vi.fn()} onTest={vi.fn()} saving={false} testing={false} testResult={null} />)
    expect(screen.getByLabelText("Docker Socket Path")).toHaveValue("unix:///var/run/docker.sock")
  })
})
