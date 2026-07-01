/**
 * Shared model factories for the frontend test suite (AR11).
 *
 * Each `make*` helper returns a plain object whose DEFAULTS are byte-equivalent
 * to the fixtures the page tests previously hand-wrote inline, so migrating a
 * test to a factory does not change any assertion. Callers reproduce per-test
 * variations by passing a `Partial<...>` of overrides (spread last, so an
 * explicit `undefined`/`null` wins). Fixture shapes intentionally mirror what
 * the components actually consume from the API JSON — not necessarily the full
 * `api.ts` interfaces — which is exactly what the old inline literals did.
 */

export interface ServiceStatusFixture {
  phase: string
  message: string | null
  tailscale_ip: string | null
  edge_container_id: string | null
  last_reconciled_at: string | null
  health_checks: Record<string, boolean> | null
  cert_expires_at: string | null
}

/** Default status mirrors ServiceDetail's inline `mockService.status`. */
export function makeServiceStatus(
  overrides: Partial<ServiceStatusFixture> = {},
): ServiceStatusFixture {
  return {
    phase: "pending",
    message: "Awaiting first reconciliation",
    tailscale_ip: null,
    edge_container_id: null,
    last_reconciled_at: null,
    health_checks: {
      upstream_container_present: true,
      edge_container_running: false,
      cert_present: true,
    },
    cert_expires_at: "2026-08-01T00:00:00",
    ...overrides,
  }
}

export interface ServiceFixture {
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
  status: ServiceStatusFixture | null
  created_at: string
  updated_at: string
}

/** Default service mirrors ServiceDetail's inline `mockService`. */
export function makeService(overrides: Partial<ServiceFixture> = {}): ServiceFixture {
  return {
    id: "svc_abc123",
    name: "Nextcloud",
    enabled: true,
    upstream_container_id: "c123",
    upstream_container_name: "nextcloud",
    upstream_scheme: "http",
    upstream_port: 80,
    healthcheck_path: "/status.php",
    hostname: "nextcloud.example.com",
    base_domain: "example.com",
    edge_container_name: "edge_nextcloud",
    network_name: "edge_net_nextcloud",
    ts_hostname: "edge-nextcloud",
    preserve_host_header: true,
    custom_caddy_snippet: null,
    app_profile: "nextcloud",
    status: makeServiceStatus(),
    created_at: "2026-04-05T00:00:00",
    updated_at: "2026-04-05T00:00:00",
    ...overrides,
  }
}

/** A `GET /services` list envelope wrapping the given items. */
export function makeServiceList(
  services: ServiceFixture[] = [makeService()],
): { services: ServiceFixture[]; total: number } {
  return { services, total: services.length }
}

export interface GeneralSettingsFixture {
  base_domain: string
  acme_email: string
  reconcile_interval_seconds: number
  cert_renewal_window_days: number
  timezone: string
}

export interface SettingsFixture {
  general: GeneralSettingsFixture
  cloudflare: { zone_id: string; token_configured: boolean }
  tailscale: {
    auth_key_configured: boolean
    api_key_configured: boolean
    control_url: string
    default_ts_hostname_prefix: string
  }
  docker: { socket_path: string }
  paths: { generated_root: string; cert_root: string; tailscale_state_root: string }
  setup_complete: boolean
}

/**
 * Default settings mirror the identical `mockSettings` literal shared verbatim
 * by ExposeService.test.tsx and OrphanDns.test.tsx (the "unconfigured" shape
 * those pages fetch for base-domain/hostname preview). SettingsPage.test.tsx
 * uses a different, fully-configured shape and keeps its own literal.
 */
export function makeSettings(overrides: Partial<SettingsFixture> = {}): SettingsFixture {
  return {
    general: {
      base_domain: "example.com",
      acme_email: "a@b.com",
      reconcile_interval_seconds: 60,
      cert_renewal_window_days: 30,
      timezone: "UTC",
    },
    cloudflare: { zone_id: "", token_configured: false },
    tailscale: {
      auth_key_configured: false,
      api_key_configured: false,
      control_url: "",
      default_ts_hostname_prefix: "edge",
    },
    docker: { socket_path: "" },
    paths: { generated_root: "", cert_root: "", tailscale_state_root: "" },
    setup_complete: false,
    ...overrides,
  }
}

export interface EventFixture {
  id: string
  service_id: string | null
  kind: string
  level: string
  message: string
  details: Record<string, unknown> | null
  created_at: string | null
}

/** Default event mirrors Events.test.tsx's first inline event. */
export function makeEvent(overrides: Partial<EventFixture> = {}): EventFixture {
  return {
    id: "evt_1",
    service_id: "svc_1",
    kind: "cert_issued",
    level: "info",
    message: "Certificate issued for nextcloud.example.com",
    details: { hostname: "nextcloud.example.com", issuer: "letsencrypt" },
    created_at: "2026-04-05T12:00:00Z",
    ...overrides,
  }
}

export interface ContainerPortFixture {
  container_port: string
  host_port: string | null
  protocol: string
}

export function makeContainerPort(
  overrides: Partial<ContainerPortFixture> = {},
): ContainerPortFixture {
  return { container_port: "80", host_port: "9080", protocol: "tcp", ...overrides }
}

export interface ContainerFixture {
  id: string
  name: string
  image: string
  status: string
  state: string
  ports: ContainerPortFixture[]
  networks: string[]
  labels: Record<string, string>
}

/** Default container mirrors Discover.test.tsx's inline nextcloud container. */
export function makeContainer(overrides: Partial<ContainerFixture> = {}): ContainerFixture {
  return {
    id: "c1",
    name: "nextcloud",
    image: "nextcloud:28",
    status: "running",
    state: "running",
    ports: [makeContainerPort()],
    networks: ["bridge"],
    labels: {},
    ...overrides,
  }
}

export interface JobDetailsFixture {
  record_id: string
  hostname: string
  zone_id: string
  value: string | null
  service_name: string
}

/** Default job details mirror OrphanDns.test.tsx's first job. */
export function makeJobDetails(overrides: Partial<JobDetailsFixture> = {}): JobDetailsFixture {
  return {
    record_id: "cf_r1",
    hostname: "nextcloud.example.com",
    zone_id: "zone1",
    value: "100.64.0.1",
    service_name: "Nextcloud",
    ...overrides,
  }
}

export interface JobFixture {
  id: string
  service_id: string | null
  kind: string
  status: string
  progress: number
  message: string | null
  details: JobDetailsFixture | null
  created_at: string | null
  updated_at: string | null
}

/** Default orphan-DNS job mirrors OrphanDns.test.tsx's first (pending) job. */
export function makeJob(overrides: Partial<JobFixture> = {}): JobFixture {
  return {
    id: "job_abc123",
    service_id: null,
    kind: "dns_orphan_cleanup",
    status: "pending",
    progress: 0,
    message: "Orphaned DNS record for deleted service 'Nextcloud'",
    details: makeJobDetails(),
    created_at: "2026-04-08T14:30:00Z",
    updated_at: "2026-04-08T14:30:00Z",
    ...overrides,
  }
}
