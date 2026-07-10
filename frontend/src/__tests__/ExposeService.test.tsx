import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import ExposeService from "@/pages/ExposeService"
import { mockCreatedService, mockSettings, stubSettingsFetch } from "./exposeServiceTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
  stubSettingsFetch()
})

describe("ExposeService page - prefill", () => {
  it("renders the form", async () => {
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Expose Service")).toBeInTheDocument()
    })
    expect(screen.getByText("Service Name")).toBeInTheDocument()
    expect(screen.getByText("Hostname Prefix")).toBeInTheDocument()
    expect(screen.getByText("Upstream Port")).toBeInTheDocument()
    expect(screen.getByText("Create Service")).toBeInTheDocument()
  })

  it("pre-fills container name", async () => {
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nextcloud&image=nextcloud:28&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getAllByText(/nextcloud/).length).toBeGreaterThanOrEqual(1)
    })
  })

  it("shows hostname preview", async () => {
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=myapp&image=myapp:1&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getAllByText(/myapp\.example\.com/).length).toBeGreaterThanOrEqual(1)
    })
  })

  it("shows review section", async () => {
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=myapp&image=myapp:1&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Review")).toBeInTheDocument()
    })
    expect(screen.getByText("DNS record:")).toBeInTheDocument()
    expect(screen.getByText("Upstream:")).toBeInTheDocument()
  })

  it("renders port selector from discovered ports", async () => {
    const ports = JSON.stringify([
      { container_port: "80", host_port: "8080", protocol: "tcp" },
      { container_port: "443", host_port: null, protocol: "tcp" },
    ])
    render(
      <MemoryRouter initialEntries={[`/expose?container_id=c1&container_name=web&image=web:1&ports=${encodeURIComponent(ports)}`]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Expose Service")).toBeInTheDocument()
    })
  })

  it("resets form and detected profile when the discovered container changes", async () => {
    const firstPorts = JSON.stringify([
      { container_port: "80", host_port: "8080", protocol: "tcp" },
      { container_port: "8080", host_port: null, protocol: "tcp" },
    ])
    const secondPorts = JSON.stringify([
      { container_port: "9090", host_port: null, protocol: "tcp" },
    ])
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      }
      if (String(url).includes("/profiles/detect")) {
        const image = new URL(String(url), "http://localhost").searchParams.get("image")
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(
            image === "nextcloud:28"
              ? {
                  detected_profile: "nextcloud",
                  profile: {
                    name: "Nextcloud",
                    recommended_port: 8080,
                    healthcheck_path: "/status.php",
                    preserve_host_header: false,
                    post_setup_reminder: null,
                    image_patterns: ["nextcloud"],
                  },
                }
              : { detected_profile: null, profile: null }
          ),
        })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ...mockCreatedService, id: "svc_other" }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={[`/expose?container_id=c1&container_name=nextcloud&image=nextcloud:28&ports=${encodeURIComponent(firstPorts)}`]}>
        <Routes>
          <Route
            path="/expose"
            element={
              <>
                <ExposeService />
                <Link to={`/expose?container_id=c2&container_name=redis&image=redis:7&ports=${encodeURIComponent(secondPorts)}`}>
                  Switch container
                </Link>
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText(/Detected/)).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole("link", { name: "Switch container" }))

    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "Service Name" })).toHaveValue("redis")
    })
    expect(screen.queryByText(/Detected/)).not.toBeInTheDocument()
    expect(screen.getByRole("combobox", { name: "Upstream Port" })).toHaveValue("9090")
    expect(screen.getByRole("textbox", { name: "Healthcheck Path (optional)" })).toHaveValue("")
    expect(screen.getByRole("checkbox", { name: "Preserve Host Header" })).toBeChecked()

    fireEvent.click(screen.getByText("Create Service"))

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        (call: unknown[]) =>
          String(call[0]).includes("/api/services") &&
          typeof call[1] === "object" &&
          (call[1] as RequestInit).method === "POST"
      )
      expect(JSON.parse(String((createCall?.[1] as RequestInit).body))).toMatchObject({
        name: "redis",
        upstream_container_id: "c2",
        upstream_port: 9090,
        healthcheck_path: null,
        preserve_host_header: true,
        app_profile: null,
      })
    })
  })

  it("clears detected profile state when only the discovered container name changes", async () => {
    const ports = JSON.stringify([{ container_port: "80", host_port: null, protocol: "tcp" }])
    const secondDetect = Promise.withResolvers<{
      detected_profile: string | null
      profile: null
    }>()
    let detectCalls = 0
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        detectCalls += 1
        return Promise.resolve({
          ok: true,
          json: () =>
            detectCalls === 1
              ? Promise.resolve({
                  detected_profile: "nextcloud",
                  profile: {
                    name: "Nextcloud",
                    recommended_port: 80,
                    healthcheck_path: "/status.php",
                    preserve_host_header: false,
                    post_setup_reminder: null,
                    image_patterns: ["nextcloud"],
                  },
                })
              : secondDetect.promise,
        })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ...mockCreatedService, id: "svc_renamed" }),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={[`/expose?container_id=c1&container_name=nextcloud&image=nextcloud:28&ports=${encodeURIComponent(ports)}`]}>
        <Routes>
          <Route
            path="/expose"
            element={
              <>
                <ExposeService />
                <Link to={`/expose?container_id=c1&container_name=renamed&image=nextcloud:28&ports=${encodeURIComponent(ports)}`}>
                  Rename container
                </Link>
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText(/Detected/)).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole("link", { name: "Rename container" }))

    await waitFor(() => {
      expect(screen.getByRole("textbox", { name: "Service Name" })).toHaveValue("renamed")
      expect(screen.queryByText(/Detected/)).not.toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Create Service"))

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        (call: unknown[]) =>
          String(call[0]).includes("/api/services") &&
          typeof call[1] === "object" &&
          (call[1] as RequestInit).method === "POST"
      )
      expect(JSON.parse(String((createCall?.[1] as RequestInit).body))).toMatchObject({
        name: "renamed",
        app_profile: null,
        healthcheck_path: null,
        preserve_host_header: true,
      })
    })
  })

  it("derives the edge container/network preview from the service name, not the hostname prefix", async () => {
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=myapp&image=myapp:1&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Review")).toBeInTheDocument()
    })

    // Edit ONLY the service name. The backend slugifies the service name to build
    // edge_<slug> / edge_net_<slug>, so the preview must follow the name — not the
    // (unchanged) hostname prefix, which still drives only the DNS record.
    fireEvent.change(screen.getByRole("textbox", { name: "Service Name" }), {
      target: { value: "My Web App" },
    })

    expect(screen.getByText("edge_my-web-app")).toBeInTheDocument()
    expect(screen.getByText("edge_net_my-web-app")).toBeInTheDocument()
    // The hostname prefix is still "myapp"; it must no longer drive the edge names.
    expect(screen.queryByText("edge_myapp")).not.toBeInTheDocument()
    // DNS record still reflects the unchanged hostname prefix.
    expect(screen.getByText("myapp.example.com")).toBeInTheDocument()
  })

  it("caps the preview slug at the backend's 50-char base limit for long names", async () => {
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=myapp&image=myapp:1&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Review")).toBeInTheDocument()
    })

    const longName = "a".repeat(60)
    fireEvent.change(screen.getByRole("textbox", { name: "Service Name" }), {
      target: { value: longName },
    })

    const capped = "a".repeat(50)
    expect(screen.getByText(`edge_${capped}`)).toBeInTheDocument()
    expect(screen.getByText(`edge_net_${capped}`)).toBeInTheDocument()
    // The uncapped 60-char slug must NOT appear (it would overstate the real name).
    expect(screen.queryByText(`edge_${"a".repeat(60)}`)).not.toBeInTheDocument()
  })

  it("prefills a valid hostname prefix when the container name ends in a non-alphanumeric char", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=web.&image=web:1&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    // The trailing dot must be stripped so the prefilled prefix is a valid DNS
    // label and Create is not silently disabled once settings load.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })
    const prefix = screen.getByText("Hostname Prefix").closest("label")!.querySelector("input")!
    expect(prefix).toHaveValue("web")
  })
})
