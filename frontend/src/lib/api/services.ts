import { del, get, post, put } from "./core"

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

// Per-service route builder — the single source for the `/services/{id}{sub}`
// shape every service endpoint below composes (previously hand-duplicated as
// `servicePath` in Services.tsx and ServiceDetail.tsx).
const servicePath = (id: string, sub = "") => `/services/${encodeURIComponent(id)}${sub}`

export const servicesApi = {
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
}
