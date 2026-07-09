import re

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
    health_check_interval_seconds: int | None = Field(default=None, ge=1)
    # Upper bound (~27 years) keeps `datetime.now() + timedelta(days=window)` well
    # clear of the OverflowError ceiling that would 500 the manual /renew-cert path;
    # this stops the bad value at write. The dashboard (routers/dashboard.py) and
    # manual-renew (services/cert_ops.py) consumers additionally guard the addition
    # with try/except OverflowError for legacy/direct-DB values; the reconciler and
    # renewal-scan consumers instead rely on their per-service broad except handlers,
    # so an overflow there degrades one service, never crashes the loop.
    # event_retention_days keeps its ge=1-only contract (its consumer in
    # events/retention_task.py has defined saturating overflow semantics).
    cert_renewal_window_days: int | None = Field(default=None, ge=1, le=10000)
    event_retention_days: int | None = Field(default=None, ge=1)
    timezone: str | None = Field(default=None, min_length=1)
    developer_mode: bool | None = None

    @field_validator("base_domain")
    @classmethod
    def normalize_base_domain(cls, value: str | None) -> str | None:
        if value is None:
            return value
        domain = value.lower()
        if not re.fullmatch(
            r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*",
            domain,
        ):
            raise ValueError("Invalid base domain format")
        if len(domain) > 253:
            raise ValueError("Base domain must not exceed 253 characters")
        if any(len(label) > 63 for label in domain.split(".")):
            raise ValueError("Each base domain label must not exceed 63 characters")
        return domain

    @field_validator("acme_email")
    @classmethod
    def validate_acme_email(cls, value: str | None) -> str | None:
        if value is None:
            return value
        # Very general shape: exactly one '@', non-empty whitespace-free local
        # part, and a domain with at least one dot. Deliberately lenient — it
        # only catches obvious mistakes, never rejects a real address.
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value


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
    health_check_interval_seconds: int
    cert_renewal_window_days: int
    event_retention_days: int
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
