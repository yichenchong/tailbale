from pydantic import BaseModel


class GeneralSettingsUpdate(BaseModel):
    base_domain: str | None = None
    acme_email: str | None = None
    reconcile_interval_seconds: int | None = None
    cert_renewal_window_days: int | None = None
    timezone: str | None = None


class CloudflareSettingsUpdate(BaseModel):
    zone_id: str | None = None
    token: str | None = None  # Write-only — never returned


class TailscaleSettingsUpdate(BaseModel):
    auth_key: str | None = None  # Write-only — never returned
    api_key: str | None = None   # Write-only — for device management API
    control_url: str | None = None
    default_ts_hostname_prefix: str | None = None


class DockerSettingsUpdate(BaseModel):
    socket_path: str | None = None


class PathSettingsUpdate(BaseModel):
    generated_root: str | None = None
    cert_root: str | None = None
    tailscale_state_root: str | None = None


class GeneralSettingsResponse(BaseModel):
    base_domain: str
    acme_email: str
    reconcile_interval_seconds: int
    cert_renewal_window_days: int
    timezone: str


class CloudflareSettingsResponse(BaseModel):
    zone_id: str
    token_configured: bool


class TailscaleSettingsResponse(BaseModel):
    auth_key_configured: bool
    api_key_configured: bool
    control_url: str
    default_ts_hostname_prefix: str


class DockerSettingsResponse(BaseModel):
    socket_path: str


class PathSettingsResponse(BaseModel):
    generated_root: str
    cert_root: str
    tailscale_state_root: str


class AllSettingsResponse(BaseModel):
    general: GeneralSettingsResponse
    cloudflare: CloudflareSettingsResponse
    tailscale: TailscaleSettingsResponse
    docker: DockerSettingsResponse
    paths: PathSettingsResponse
    setup_complete: bool


class ConnectionTestResult(BaseModel):
    success: bool
    message: str
