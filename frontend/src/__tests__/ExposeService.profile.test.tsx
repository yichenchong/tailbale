import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import ExposeService from "@/pages/ExposeService"
import { mockSettings, stubSettingsFetch } from "./exposeServiceTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
  stubSettingsFetch()
})

describe("ExposeService page - profile", () => {
  it("renders the detected profile's post_setup_reminder when present", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            detected_profile: "nextcloud",
            profile: {
              name: "Nextcloud",
              recommended_port: 80,
              healthcheck_path: "/status.php",
              preserve_host_header: false,
              post_setup_reminder: "Add your domain to trusted_domains in config.php.",
              image_patterns: ["nextcloud"],
            },
          }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nextcloud&image=nextcloud:28&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText(/Detected/)).toBeInTheDocument()
    })
    expect(screen.getByText(/After creating:/)).toBeInTheDocument()
    expect(screen.getByText("Add your domain to trusted_domains in config.php.")).toBeInTheDocument()
  })

  it("keeps the first exposed port when the detected profile recommends a non-exposed port", async () => {
    // recExposed guard: applying a recommended port the container does NOT expose
    // would desync the <select> (no matching <option>) from the value actually
    // submitted. The profile's other defaults must still apply.
    const ports = JSON.stringify([
      { container_port: "80", host_port: "8080", protocol: "tcp" },
      { container_port: "8443", host_port: null, protocol: "tcp" },
    ])
    const fetchMock = vi.fn((url: string) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            detected_profile: "ghost",
            profile: {
              name: "Ghost",
              recommended_port: 2368, // not among the exposed ports above
              healthcheck_path: "/ghost/api/health",
              preserve_host_header: true,
              post_setup_reminder: null,
              image_patterns: ["ghost"],
            },
          }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={[`/expose?container_id=c1&container_name=ghost&image=ghost:5&ports=${encodeURIComponent(ports)}`]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText(/Detected/)).toBeInTheDocument()
    })
    // Port stays at the first exposed option (80), not the non-exposed 2368.
    expect(screen.getByRole("combobox", { name: "Upstream Port" })).toHaveValue("80")
    // ...but the profile's healthcheck default still applied.
    expect(screen.getByRole("textbox", { name: "Healthcheck Path (optional)" })).toHaveValue("/ghost/api/health")
  })
})
