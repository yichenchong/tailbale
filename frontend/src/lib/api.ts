const API_BASE = "/api"

type ApiErrorDetail = unknown

/**
 * Thrown by non-redirecting requests (e.g. `api.getSafe`) when the server
 * responds 401. Callers that must NOT bounce to /login (background pollers)
 * catch this typed marker to handle the auth failure on their own terms.
 */
export class UnauthorizedError extends Error {
  constructor(message = "Unauthorized") {
    super(message)
    this.name = "UnauthorizedError"
  }
}

interface RequestConfig {
  // When false, a 401 throws `UnauthorizedError` instead of redirecting to
  // /login. Defaults to true (redirect), matching `api.get`.
  redirectOn401?: boolean
}

function formatErrorDetail(detail: ApiErrorDetail): string | null {
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item
        if (item && typeof item === "object") {
          for (const key of ["msg", "message", "error"]) {
            const value = (item as Record<string, unknown>)[key]
            if (typeof value === "string" && value) return value
          }
        }
        return null
      })
      .filter((msg): msg is string => Boolean(msg))
    return messages.length > 0 ? messages.join("; ") : null
  }
  if (detail && typeof detail === "object") {
    for (const key of ["message", "msg", "error"]) {
      const value = (detail as Record<string, unknown>)[key]
      if (typeof value === "string" && value) return value
    }
  }
  return null
}

async function request<T>(path: string, options?: RequestInit, config?: RequestConfig): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  })
  if (res.status === 401 && !path.startsWith("/auth/")) {
    if (config?.redirectOn401 === false) {
      throw new UnauthorizedError()
    }
    window.location.href = "/login"
    throw new Error("Session expired")
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(formatErrorDetail(body?.detail) || `Request failed: ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  // An empty (or whitespace-only) but non-204 body would make res.json() throw
  // an opaque SyntaxError, so read the raw text and JSON.parse only when it
  // holds non-whitespace content, returning undefined otherwise. Real Response
  // objects always expose text(); fall back to json() only for partial Response
  // stubs that omit it.
  if (typeof res.text === "function") {
    const text = await res.text()
    return (text.trim() ? JSON.parse(text) : undefined) as T
  }
  return res.json()
}

function get<T>(path: string) {
  return request<T>(path)
}
// Non-redirecting GET: on 401 it throws `UnauthorizedError` rather than
// sending the browser to /login — for background pollers that must never
// trigger a navigation.
function getSafe<T>(path: string) {
  return request<T>(path, undefined, { redirectOn401: false })
}
function put<T>(path: string, body: unknown) {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) })
}
function post<T>(path: string, body?: unknown) {
  return request<T>(path, {
    method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}
function del<T>(path: string) {
  return request<T>(path, { method: "DELETE" })
}

// Per-service route builder — the single source for the `/services/{id}{sub}`
// shape every service endpoint below composes (previously hand-duplicated as
// `servicePath` in Services.tsx and ServiceDetail.tsx).
const servicePath = (id: string, sub = "") => `/services/${encodeURIComponent(id)}${sub}`

/**
 * The typed API surface. The generic verbs (`get`/`getSafe`/`put`/`post`/
 * `delete`) stay available; the namespaced endpoint groups below are built on
 * top of them and OWN each path + request/response type, so call sites invoke a
 * typed function instead of hand-building URL strings. HTTP method + URL emitted
 * by each function is identical to the strings the call sites used before.
 */
export const api = {
  get,
  getSafe,
  put,
  post,
  delete: del,

  dashboard: {
    summary: () => get<DashboardSummary>("/dashboard/summary"),
  },

  services: {
    list: () => get<ServiceListResponse>("/services"),
    get: (id: string) => get<ServiceItem>(servicePath(id)),
    create: (body: ServiceCreateRequest) => post<ServiceItem>("/services", body),
    update: (id: string, body: ServiceUpdateRequest) => put<ServiceItem>(servicePath(id), body),
    remove: (id: string, opts?: { cleanupDns?: boolean }) =>
      del<void>(servicePath(id, opts?.cleanupDns ? "?cleanup_dns=true" : "")),
    reload: (id: string) => post<void>(servicePath(id, "/reload")),
    restartEdge: (id: string) => post<void>(servicePath(id, "/restart-edge")),
    recreateEdge: (id: string) => post<void>(servicePath(id, "/recreate-edge")),
    reconcile: (id: string) => post<void>(servicePath(id, "/reconcile")),
    disable: (id: string) => post<ServiceItem>(servicePath(id, "/disable")),
    renewCert: (id: string, opts?: { force?: boolean }) =>
      post<RenewCertResponse>(servicePath(id, opts?.force ? "/renew-cert?force=true" : "/renew-cert")),
    edgeVersion: (id: string) => get<EdgeVersionResponse>(servicePath(id, "/edge-version")),
    updateEdge: (id: string) => post<void>(servicePath(id, "/update-edge")),
  },

  events: {
    list: (params: EventsQuery) => {
      const qs = new URLSearchParams()
      if (params.search) qs.set("search", params.search)
      if (params.level) qs.set("level", params.level)
      if (params.kind) qs.set("kind", params.kind)
      qs.set("limit", String(params.limit))
      qs.set("offset", String(params.offset))
      const s = qs.toString()
      return get<EventsResponse>(`/events${s ? `?${s}` : ""}`)
    },
    kinds: () => get<EventKindsResponse>("/events/kinds"),
  },

  discovery: {
    containers: (params: DiscoveryQuery) => {
      const qs = new URLSearchParams({
        running_only: String(params.runningOnly),
        hide_managed: "true",
        ...(params.search ? { search: params.search } : {}),
      })
      return get<DiscoveryResponse>(`/discovery/containers?${qs}`)
    },
  },

  profiles: {
    detect: (image: string) =>
      get<ProfileDetectResponse>(`/profiles/detect?image=${encodeURIComponent(image)}`),
  },

  jobs: {
    list: (params: JobsQuery) => {
      const qs = new URLSearchParams()
      if (params.kind) qs.set("kind", params.kind)
      qs.set("limit", String(params.limit))
      qs.set("offset", String(params.offset))
      return get<JobsResponse>(`/jobs?${qs.toString()}`)
    },
    retry: (id: string) => post<JobActionResult>(`/jobs/${encodeURIComponent(id)}/retry`),
    dismiss: (id: string) => del<void>(`/jobs/${encodeURIComponent(id)}`),
  },

  settings: {
    all: () => get<AllSettings>("/settings"),
    update: (section: SettingsSection, body: Record<string, unknown>) =>
      put<AllSettings>(`/settings/${section}`, body),
    test: (service: SettingsTestService) => post<ConnectionTestResult>(`/settings/test/${service}`),
    developerReset: (kind: DeveloperResetKind) => post<void>(`/settings/developer/${kind}`),
    mainLogs: (tail = 250) => get<MainLogsResponse>(`/settings/developer/main-logs?tail=${tail}`),
  },

  auth: {
    status: () => get<AuthStatus>("/auth/status"),
    setupProgress: () => get<SetupProgress>("/auth/setup-progress"),
    login: (body: CredentialsRequest) => post<LoginResponse>("/auth/login", body),
    setupUser: (body: CredentialsRequest) => post<LoginResponse>("/auth/setup-user", body),
    changePassword: (body: ChangePasswordRequest) => post<void>("/auth/change-password", body),
  },

  meta: {
    version: () => get<VersionResponse>("/version"),
  },
}

// --- Dashboard types ---

export interface DashboardSummary {
  services: {
    total: number
    healthy: number
    warning: number
    error: number
  }
  expiring_certs: {
    service_id: string
    service_name: string
    hostname: string
    expires_at: string | null
  }[]
  recent_errors: {
    id: string
    service_id: string | null
    kind: string
    message: string
    created_at: string | null
  }[]
  recent_events: {
    id: string
    service_id: string | null
    kind: string
    level: string
    message: string
    created_at: string | null
  }[]
}

// --- Settings types ---

export interface GeneralSettings {
  base_domain: string
  acme_email: string
  reconcile_interval_seconds: number
  health_check_interval_seconds: number
  cert_renewal_window_days: number
  event_retention_days: number
  timezone: string
  developer_mode: boolean
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

export interface MainLogsResponse {
  container: string
  logs: string
}

// Settings sections accepted by PUT /settings/{section}.
export type SettingsSection =
  | "general"
  | "cloudflare"
  | "tailscale"
  | "docker"
  | "paths"
  | "setup-complete"

// Services whose connection can be probed via POST /settings/test/{service}.
export type SettingsTestService = "cloudflare" | "tailscale" | "docker"

// Developer reset actions exposed via POST /settings/developer/{kind}.
export type DeveloperResetKind = "reset-setup-complete" | "reset-all"

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

export interface ServiceCreateRequest {
  name: string
  upstream_container_id: string
  upstream_container_name: string
  upstream_scheme: string
  upstream_port: number
  healthcheck_path: string | null
  hostname: string
  enabled: boolean
  preserve_host_header: boolean
  custom_caddy_snippet: string | null
  app_profile: string | null
}

export interface EdgeVersionResponse {
  orchestrator_version: string
  edge_version: string | null
  up_to_date: boolean
}

export interface RenewCertResponse {
  success: boolean
  performed: boolean
  needs_force: boolean
  message: string
  expires_at?: string | null
  last_failure?: string | null
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

export interface SetupProgress {
  user_exists: boolean
  base_domain_set: boolean
  cloudflare_configured: boolean
  cloudflare_token_set?: boolean
  acme_email_set: boolean
  tailscale_configured: boolean
  docker_configured: boolean
}

export interface CredentialsRequest {
  username: string
  password: string
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
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

export interface DiscoveryQuery {
  runningOnly: boolean
  search?: string
}

// --- Events types ---

export interface EventItem {
  id: string
  service_id: string | null
  kind: string
  level: string
  message: string
  details: Record<string, unknown> | null
  created_at: string | null
}

export interface EventsResponse {
  events: EventItem[]
  total: number
}

/** GET /events/kinds — the canonical registry the backend emits, sorted. */
export interface EventKindsResponse {
  kinds: string[]
}

export interface EventsQuery {
  search?: string
  level?: string
  kind?: string
  limit: number
  offset: number
}

// --- Jobs types ---

export interface JobDetails {
  record_id: string
  hostname: string
  zone_id: string
  value: string | null
  service_name: string
}

export interface OrphanJob {
  id: string
  service_id: string | null
  kind: string
  status: string
  progress: number
  message: string | null
  details: JobDetails | null
  created_at: string | null
  updated_at: string | null
}

export interface JobsResponse {
  jobs: OrphanJob[]
  total: number
}

export interface JobsQuery {
  kind?: string
  limit: number
  offset: number
}

export interface JobActionResult {
  success: boolean
  message: string
}

// --- Meta types ---

export interface VersionResponse {
  version: string
}

// --- Profile types ---

export interface AppProfile {
  name: string
  recommended_port: number
  healthcheck_path: string | null
  preserve_host_header: boolean
  post_setup_reminder: string | null
  image_patterns: string[]
}

export interface ProfileDetectResponse {
  detected_profile: string | null
  profile: AppProfile | null
}
