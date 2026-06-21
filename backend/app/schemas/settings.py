from pydantic import BaseModel, Field, field_validator


class _SettingsUpdateModel(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class GeneralSettingsUpdate(_SettingsUpdateModel):
    base_domain: str | None = Field(default=None, min_length=1)
    acme_email: str | None = Field(default=None, min_length=1)
    reconcile_interval_seconds: int | None = Field(default=None, ge=1)
    cert_renewal_window_days: int | None = Field(default=None, ge=1)
    timezone: str | None = Field(default=None, min_length=1)
    developer_mode: bool | None = None


class CloudflareSettingsUpdate(_SettingsUpdateModel):
    zone_id: str | None = Field(default=None, min_length=1)
    token: str | None = Field(default=None, min_length=1)  # Write-only — never returned


class TailscaleSettingsUpdate(_SettingsUpdateModel):
    auth_key: str | None = Field(default=None, min_length=1)  # Write-only — edge login
    api_key: str | None = Field(default=None, min_length=1)   # Write-only — device management API
    control_url: str | None = Field(default=None, min_length=1)
    default_ts_hostname_prefix: str | None = Field(default=None, min_length=1)


class DockerSettingsUpdate(_SettingsUpdateModel):
    socket_path: str | None = Field(default=None, min_length=1)


class PathSettingsUpdate(_SettingsUpdateModel):
    generated_root: str | None = None
    cert_root: str | None = None
    tailscale_state_root: str | None = None


class GeneralSettingsResponse(BaseModel):
    base_domain: str
    acme_email: str
    reconcile_interval_seconds: int
    cert_renewal_window_days: int
    timezone: str
    developer_mode: bool

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
