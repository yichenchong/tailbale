import { del, get, getSafe, post, put } from "./api/core"
import { authApi } from "./api/auth"
import { dashboardApi } from "./api/dashboard"
import { discoveryApi } from "./api/discovery"
import { eventsApi } from "./api/events"
import { jobsApi } from "./api/jobs"
import { metaApi } from "./api/meta"
import { profilesApi } from "./api/profiles"
import { servicesApi } from "./api/services"
import { settingsApi } from "./api/settings"

export { UnauthorizedError } from "./api/core"

export type {
  AuthStatus,
  AuthUser,
  ChangePasswordRequest,
  CredentialsRequest,
  LoginResponse,
  SetupProgress,
} from "./api/auth"
export type { DashboardSummary } from "./api/dashboard"
export type { ContainerPort, DiscoveredContainer, DiscoveryQuery, DiscoveryResponse } from "./api/discovery"
export type { EventItem, EventKindsResponse, EventsQuery, EventsResponse } from "./api/events"
export type { JobActionResult, JobDetails, JobsQuery, JobsResponse, OrphanJob } from "./api/jobs"
export type { VersionResponse } from "./api/meta"
export type { AppProfile, ProfileDetectResponse } from "./api/profiles"
export type {
  EdgeVersionResponse,
  EdgeNetworkAttachment,
  RenewCertResponse,
  ServiceCreateRequest,
  ServiceItem,
  ServiceListResponse,
  ServiceStatus,
  ServiceUpdateRequest,
} from "./api/services"
export type {
  AllSettings,
  CloudflareSettings,
  ConnectionTestResult,
  DeveloperResetKind,
  DockerSettings,
  GeneralSettings,
  MainLogsResponse,
  PathSettings,
  SettingsSection,
  SettingsTestService,
  TailscaleSettings,
} from "./api/settings"

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

  dashboard: dashboardApi,
  services: servicesApi,
  events: eventsApi,
  discovery: discoveryApi,
  profiles: profilesApi,
  jobs: jobsApi,
  settings: settingsApi,
  auth: authApi,
  meta: metaApi,
}
