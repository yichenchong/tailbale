import { get, post, put } from "./core"

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

export const settingsApi = {
  all: () => get<AllSettings>("/settings"),
  update: (section: SettingsSection, body: Record<string, unknown>) =>
    put<AllSettings>(`/settings/${section}`, body),
  test: (service: SettingsTestService) => post<ConnectionTestResult>(`/settings/test/${service}`),
  developerReset: (kind: DeveloperResetKind) => post<void>(`/settings/developer/${kind}`),
  mainLogs: (tail = 250) => get<MainLogsResponse>(`/settings/developer/main-logs?tail=${tail}`),
}
