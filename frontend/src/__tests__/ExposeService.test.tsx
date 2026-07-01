import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Link, MemoryRouter, Route, Routes, useParams } from "react-router-dom"
import { makeSettings } from "./factories"

const mockSettings = makeSettings()

const mockCreatedService = {
  id: "svc_new123",
  name: "nginx",
  enabled: true,
  upstream_container_id: "c1",
  upstream_container_name: "nginx",
  upstream_scheme: "http",
  upstream_port: 80,
  hostname: "nginx.example.com",
  base_domain: "example.com",
  edge_container_name: "edge_nginx",
  network_name: "edge_net_nginx",
  ts_hostname: "edge-nginx",
  preserve_host_header: true,
  custom_caddy_snippet: null,
  app_profile: null,
  healthcheck_path: null,
  status: { phase: "pending", message: "Awaiting first reconciliation", tailscale_ip: null, edge_container_id: null, last_reconciled_at: null, health_checks: null, cert_expires_at: null },
  created_at: "2026-04-05T00:00:00",
  updated_at: "2026-04-05T00:00:00",
}

function ServiceIdProbe() {
  const { id } = useParams<{ id: string }>()
  return <div data-testid="matched-id">{id}</div>
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(mockSettings),
  }))
})

describe("ExposeService page", () => {
  it("renders the form", async () => {
    const { default: ExposeService } = await import("@/pages/ExposeService")
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
    const { default: ExposeService } = await import("@/pages/ExposeService")
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
    const { default: ExposeService } = await import("@/pages/ExposeService")
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
    const { default: ExposeService } = await import("@/pages/ExposeService")
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
    const { default: ExposeService } = await import("@/pages/ExposeService")
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

    const { default: ExposeService } = await import("@/pages/ExposeService")
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

  it("requires a discovered container before creating a service", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ detected_profile: null, profile: null }),
        })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockCreatedService),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("Create Service")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByRole("textbox", { name: "Service Name" }), { target: { value: "manual" } })
    fireEvent.change(screen.getByText("Hostname Prefix").closest("label")!.querySelector("input")!, { target: { value: "manual" } })

    expect(screen.getByRole("button", { name: "Create Service" })).toBeDisabled()
    fireEvent.submit(screen.getByRole("button", { name: "Create Service" }).closest("form")!)

    expect(screen.getByText("Choose a discovered container before creating a service")).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        (call: unknown[]) =>
          String(call[0]).includes("/api/services") &&
          typeof call[1] === "object" &&
          (call[1] as RequestInit).method === "POST"
      )
    ).toBe(false)
  })

  it("calls API and navigates after submit", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      // Settings GET
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      }
      // Profile detect
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ detected_profile: null, profile: null }),
        })
      }
      // Service POST (create)
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockCreatedService),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("Create Service")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Create Service"))

    // Verify the POST was made
    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (c: unknown[]) => typeof c[1] === "object" && (c[1] as RequestInit).method === "POST"
      )
      expect(postCalls.length).toBeGreaterThanOrEqual(1)
    })
  })

  it("submits the service form when Enter is pressed in a field", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ detected_profile: null, profile: null }),
        })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockCreatedService),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("Create Service")).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole("textbox", { name: "Service Name" }))
    await userEvent.keyboard("{Enter}")

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (c: unknown[]) =>
            String(c[0]).includes("/api/services") &&
            typeof c[1] === "object" &&
            (c[1] as RequestInit).method === "POST"
        )
      ).toBe(true)
    })
  })

  it("keeps Create Service disabled until settings load", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    expect(screen.getByRole("button", { name: "Create Service" })).toBeDisabled()
  })

  it("blocks invalid hostname prefixes before creating the service", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ detected_profile: null, profile: null }),
        })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockCreatedService),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })

    const hostnameInput = screen.getByText("Hostname Prefix").closest("label")!.querySelector("input")!
    fireEvent.change(hostnameInput, { target: { value: "-bad" } })

    expect(screen.getByRole("button", { name: "Create Service" })).toBeDisabled()
    expect(
      screen.getByText(/Must start and end with a lowercase letter or number/)
    ).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        (c: unknown[]) =>
          String(c[0]).includes("/api/services") &&
          typeof c[1] === "object" &&
          (c[1] as RequestInit).method === "POST"
      )
    ).toBe(false)
  })

  it("derives the edge container/network preview from the service name, not the hostname prefix", async () => {
    const { default: ExposeService } = await import("@/pages/ExposeService")
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
    const { default: ExposeService } = await import("@/pages/ExposeService")
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

  it("blocks a service name longer than the backend's 128-char limit", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })

    // 129 chars: one over the backend ServiceCreate.name max_length of 128.
    fireEvent.change(screen.getByRole("textbox", { name: "Service Name" }), {
      target: { value: "a".repeat(129) },
    })

    expect(screen.getByRole("button", { name: "Create Service" })).toBeDisabled()
    // Submitting the form directly (bypassing the disabled button) surfaces the
    // validation message and still must not POST.
    fireEvent.submit(screen.getByRole("button", { name: "Create Service" }).closest("form")!)
    expect(screen.getByText("Service name must be 128 characters or fewer")).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        (c: unknown[]) =>
          String(c[0]).includes("/api/services") &&
          typeof c[1] === "object" &&
          (c[1] as RequestInit).method === "POST"
      )
    ).toBe(false)
  })

  it("does not double-submit the service when the form is re-submitted while saving", async () => {
    let postCount = 0
    let resolvePost: (() => void) | null = null
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        postCount += 1
        // Keep the first POST in flight (saving stays true) so the second
        // submit exercises the in-flight guard rather than a fresh create.
        return new Promise((resolve) => {
          resolvePost = () => resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })

    // Submit via the form (the keyboard/Enter path), not the button.
    const form = screen.getByRole("button", { name: "Create Service" }).closest("form")!
    fireEvent.submit(form)

    // The first submit flips the form into the saving state with the POST pending.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Creating/ })).toBeDisabled()
    })

    // A second submit while that POST is still in flight must be ignored.
    fireEvent.submit(form)
    await Promise.resolve()
    expect(postCount).toBe(1)

    // Settle the in-flight POST so no promise dangles past the test.
    await act(async () => {
      resolvePost?.()
    })
  })

  it("ignores a synchronous double-fire of the submit event (ref-based in-flight guard)", async () => {
    // A state-based `if (saving) return` guard only blocks a second submit once
    // React has committed the `saving=true` re-render. Two submit events
    // dispatched within a single batch (before that commit) both close over
    // `saving=false` and slip through -> two POSTs. A ref set synchronously at
    // the top of the handler closes that window. This dispatches both submits
    // inside ONE act() so no re-render lands between them.
    let postCount = 0
    let resolvePost: (() => void) | null = null
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        postCount += 1
        return new Promise((resolve) => {
          resolvePost = () => resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })

    const form = screen.getByRole("button", { name: "Create Service" }).closest("form")!
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))
    })

    expect(postCount).toBe(1)

    // Settle the in-flight POST so no promise dangles past the test.
    await act(async () => {
      resolvePost?.()
    })
  })

  it("allows a retry after a failed submit (in-flight guard resets on error)", async () => {
    let postCount = 0
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        postCount += 1
        // First attempt fails; second succeeds. The ref-based guard must clear
        // on the failure so the second submit is not permanently blocked.
        return postCount === 1
          ? Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ detail: "boom" }) })
          : Promise.resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })

    const form = screen.getByRole("button", { name: "Create Service" }).closest("form")!
    fireEvent.submit(form)
    // The failed POST re-enables the button (saving reset) and clears the ref.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })
    expect(postCount).toBe(1)

    // Second submit must go through (a second POST fires).
    fireEvent.submit(form)
    await waitFor(() => {
      expect(postCount).toBe(2)
    })
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

    const { default: ExposeService } = await import("@/pages/ExposeService")
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

    const { default: ExposeService } = await import("@/pages/ExposeService")
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

  it("encodes the created service id when navigating to its detail page", async () => {
    // Service ids are server-generated and currently URL-safe, but the navigate
    // target must still be encoded (as every other service route is) so a slash
    // or space in an id can't break route matching on the detail page.
    const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...mockCreatedService, id: "svc/odd id" }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    const { default: ExposeService } = await import("@/pages/ExposeService")
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <Routes>
          <Route path="/expose" element={<ExposeService />} />
          <Route path="/services/:id" element={<ServiceIdProbe />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })
    fireEvent.click(screen.getByText("Create Service"))

    // With the id encoded, the slash-containing id round-trips through the route
    // and lands on the detail page; an unencoded navigate would mis-match.
    await waitFor(() => {
      expect(screen.getByTestId("matched-id")).toHaveTextContent("svc/odd id")
    })
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

    const { default: ExposeService } = await import("@/pages/ExposeService")
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
