const API_BASE = "/api"

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  })
  if (res.status === 401 && !path.startsWith("/auth/")) {
    window.location.href = "/login"
    throw new Error("Session expired")
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `Request failed: ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
}

// --- Settings types ---

export interface GeneralSettings {
  base_domain: string
  acme_email: string
  reconcile_interval_seconds: number
  cert_renewal_window_days: number
  timezone: string
}

export interface CloudflareSettings {
  zone_id: string
  token_configured: boolean
}

export interface TailscaleSettings {
  auth_key_configured: boolean
  api_key_configured: boolean
  control_url: string
  default_ts_hostname_prefix: string
}

export interface DockerSettings {
  socket_path: string
}

export interface PathSettings {
  generated_root: string
  cert_root: string
  tailscale_state_root: string
}

export interface AllSettings {
  general: GeneralSettings
  cloudflare: CloudflareSettings
  tailscale: TailscaleSettings
  docker: DockerSettings
  paths: PathSettings
  setup_complete: boolean
}

export interface ConnectionTestResult {
  success: boolean
  message: string
}

// --- Service types ---

export interface ServiceStatus {
  phase: string
  message: string | null
  tailscale_ip: string | null
  edge_container_id: string | null
  last_reconciled_at: string | null
  health_checks: Record<string, boolean> | null
  cert_expires_at: string | null
  probe_retry_at: string | null
  probe_retry_attempt: number | null
  last_probe_at: string | null
}

export interface ServiceItem {
  id: string
  name: string
  enabled: boolean
  upstream_container_id: string
  upstream_container_name: string
  upstream_scheme: string
  upstream_port: number
  healthcheck_path: string | null
  hostname: string
  base_domain: string
  edge_container_name: string
  network_name: string
  ts_hostname: string
  preserve_host_header: boolean
  custom_caddy_snippet: string | null
  app_profile: string | null
  status: ServiceStatus | null
  created_at: string
  updated_at: string
}

export interface ServiceListResponse {
  services: ServiceItem[]
  total: number
}

export interface ServiceCreateRequest {
  name: string
  upstream_container_id: string
  upstream_container_name: string
  upstream_scheme: string
  upstream_port: number
  healthcheck_path?: string | null
  hostname: string
  base_domain: string
  enabled?: boolean
  preserve_host_header?: boolean
  custom_caddy_snippet?: string | null
  app_profile?: string | null
}

export interface ServiceUpdateRequest {
  name?: string
  upstream_scheme?: string
  upstream_port?: number
  healthcheck_path?: string | null
  hostname?: string
  enabled?: boolean
  preserve_host_header?: boolean
  custom_caddy_snippet?: string | null
  app_profile?: string | null
}

// --- Auth types ---

export interface AuthStatus {
  setup_complete: boolean
  authenticated: boolean
}

export interface AuthUser {
  id: string
  username: string
  display_name: string | null
  role: string
}

export interface LoginResponse {
  user: AuthUser
}

// --- Discovery types ---

export interface ContainerPort {
  container_port: string
  host_port: string | null
  protocol: string
}

export interface DiscoveredContainer {
  id: string
  name: string
  image: string
  status: string
  state: string
  ports: ContainerPort[]
  networks: string[]
  labels: Record<string, string>
}

export interface DiscoveryResponse {
  containers: DiscoveredContainer[]
  total: number
}
