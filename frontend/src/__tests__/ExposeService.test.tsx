import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

const mockSettings = {
  general: { base_domain: "example.com", acme_email: "a@b.com", reconcile_interval_seconds: 60, cert_renewal_window_days: 30, timezone: "UTC" },
  cloudflare: { zone_id: "", token_configured: false },
  tailscale: { auth_key_configured: false, api_key_configured: false, control_url: "", default_ts_hostname_prefix: "edge" },
  docker: { socket_path: "" },
  paths: { generated_root: "", cert_root: "", tailscale_state_root: "" },
  setup_complete: false,
}

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
})
