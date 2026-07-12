"""Pydantic schemas for service CRUD operations."""

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.edge.caddy_snippet import validate_caddy_snippet

_HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$"
)


def _validate_hostname(v: str) -> str:
    """Validate hostname charset/structure plus RFC 1035 length limits.

    The regex enforces lowercase DNS labels; the explicit length checks reject
    hostnames that DNS/ACME would otherwise reject with an opaque error (the
    hostname is also used verbatim as the on-disk cert directory name).
    """
    # fullmatch (not match): match() would let a trailing newline through because
    # `$` also anchors before a final "\n" — and the hostname is used verbatim as
    # the cert dir name and in the Caddyfile, so embedded control chars are unsafe.
    if not _HOSTNAME_RE.fullmatch(v):
        raise ValueError("Invalid hostname format")
    if len(v) > 253:
        raise ValueError("Hostname must not exceed 253 characters")
    if any(len(label) > 63 for label in v.split(".")):
        raise ValueError("Each hostname label must not exceed 63 characters")
    return v


_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def _validate_container_name(v: str) -> str:
    """Validate an upstream container name against Docker's name charset.

    The name is rendered RAW into the Caddyfile ``reverse_proxy <name>:<port>``
    line, so an unconstrained value is a config-injection vector via the direct
    API. Docker container names match ``[a-zA-Z0-9][a-zA-Z0-9_.-]*`` — this
    accepts every legitimate name (e.g. ``nextcloud``, ``c123``, ``my-app_1.2``)
    while rejecting any value carrying whitespace, newlines, ``;``, braces, or
    quotes that could escape the directive.
    """
    # fullmatch (not match): match() would let a trailing newline through
    # because `$` also anchors before a final "\n".
    if not _CONTAINER_NAME_RE.fullmatch(v):
        raise ValueError(
            "Invalid container name: must match Docker's charset "
            "'[a-zA-Z0-9][a-zA-Z0-9_.-]*'"
        )
    return v

def _validate_network_name(v: str) -> str:
    """Validate a Docker network name used for additional edge attachments."""
    if not _CONTAINER_NAME_RE.fullmatch(v):
        raise ValueError(
            "Invalid Docker network name: must match Docker's charset "
            "'[a-zA-Z0-9][a-zA-Z0-9_.-]*'"
        )
    return v


class AdditionalNetwork(BaseModel):
    """Operator-owned Docker network the edge joins with DNS aliases."""

    name: str = Field(..., min_length=1)
    aliases: list[str] = Field(..., min_length=1)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_network_name(v)

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [alias.strip().lower() if isinstance(alias, str) else alias for alias in v]
        return v

    @field_validator("aliases")
    @classmethod
    def validate_aliases(cls, aliases: list[str]) -> list[str]:
        seen: set[str] = set()
        for alias in aliases:
            _validate_hostname(alias)
            if alias in seen:
                raise ValueError("Duplicate alias in additional network")
            seen.add(alias)
        return aliases


def _validate_additional_networks(v: Any) -> list[dict] | None:
    """Normalize additional edge network attachments to JSON-serializable dicts."""
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError("Additional networks must be a list")
    seen: set[str] = set()
    networks: list[dict] = []
    for raw in v:
        network = AdditionalNetwork.model_validate(raw)
        if network.name in seen:
            raise ValueError("Duplicate additional network name")
        seen.add(network.name)
        networks.append(network.model_dump())
    return networks



def _validate_caddy_snippet(v: str) -> str:
    """Validate a rendered custom Caddy snippet for site-block containment.

    Delegates to the edge subsystem's ``validate_caddy_snippet`` facade, which
    lexes the snippet in its rendered form to guarantee it cannot break out of
    its per-service ``host { }`` block. Only the stable facade is imported —
    never the edge module's internal lexer/renderer wiring.
    """
    return validate_caddy_snippet(v)


class ServiceCreate(BaseModel):
    """Request body for creating a new service exposure."""

    name: str = Field(..., min_length=1, max_length=128)
    upstream_container_id: str = Field(..., min_length=1)
    upstream_container_name: str = Field(..., min_length=1)
    upstream_scheme: str = Field(default="http", pattern=r"^(http|https)$")
    upstream_port: int = Field(..., ge=1, le=65535)
    healthcheck_path: str | None = None
    hostname: str = Field(..., min_length=1)
    enabled: bool = True
    preserve_host_header: bool = True
    custom_caddy_snippet: str | None = None
    app_profile: str | None = None
    additional_networks: list[dict] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        # Strip before length checks so a whitespace-only name fails min_length.
        return v.strip() if isinstance(v, str) else v

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str) -> str:
        return _validate_hostname(v)

    @field_validator("upstream_container_name")
    @classmethod
    def validate_upstream_container_name(cls, v: str) -> str:
        return _validate_container_name(v)

    @field_validator("custom_caddy_snippet")
    @classmethod
    def validate_custom_caddy_snippet(cls, v: str | None) -> str | None:
        return _validate_caddy_snippet(v) if v is not None else v

    @field_validator("additional_networks", mode="before")
    @classmethod
    def validate_additional_networks(cls, v: Any) -> list[dict] | None:
        return _validate_additional_networks(v)


class ServiceUpdate(BaseModel):
    """Request body for updating a service exposure."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    upstream_scheme: str | None = Field(default=None, pattern=r"^(http|https)$")
    upstream_port: int | None = Field(default=None, ge=1, le=65535)
    healthcheck_path: str | None = None
    hostname: str | None = None
    enabled: bool | None = None
    preserve_host_header: bool | None = None
    custom_caddy_snippet: str | None = None
    app_profile: str | None = None
    additional_networks: list[dict] | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        # Strip before length checks so a whitespace-only name fails min_length.
        return v.strip() if isinstance(v, str) else v

    @field_validator(
        "name",
        "upstream_scheme",
        "upstream_port",
        "hostname",
        "enabled",
        "preserve_host_header",
    )
    @classmethod
    def reject_null_for_non_nullable_fields(cls, v):
        if v is None:
            raise ValueError("Field cannot be null")
        return v

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str | None) -> str | None:
        return _validate_hostname(v) if v is not None else v

    @field_validator("custom_caddy_snippet")
    @classmethod
    def validate_custom_caddy_snippet(cls, v: str | None) -> str | None:
        return _validate_caddy_snippet(v) if v is not None else v

    @field_validator("additional_networks", mode="before")
    @classmethod
    def validate_additional_networks(cls, v: Any) -> list[dict] | None:
        return _validate_additional_networks(v)


class ServiceStatusResponse(BaseModel):
    phase: str
    message: str | None = None
    tailscale_ip: str | None = None
    edge_container_id: str | None = None
    last_reconciled_at: str | None = None
    health_checks: dict[str, bool] | None = None
    cert_expires_at: str | None = None
    probe_retry_at: str | None = None
    probe_retry_attempt: int | None = None
    last_probe_at: str | None = None


class ServiceResponse(BaseModel):
    """Response body for a service exposure."""

    id: str
    name: str
    enabled: bool

    upstream_container_id: str
    upstream_container_name: str
    upstream_scheme: str
    upstream_port: int
    healthcheck_path: str | None = None

    hostname: str
    base_domain: str

    edge_container_name: str
    network_name: str
    ts_hostname: str

    preserve_host_header: bool
    custom_caddy_snippet: str | None = None
    app_profile: str | None = None
    additional_networks: list[AdditionalNetwork] | None = None

    status: ServiceStatusResponse | None = None

    created_at: str
    updated_at: str


class ServiceListResponse(BaseModel):
    services: list[ServiceResponse]
    total: int


class ContainerPortInfo(BaseModel):
    container_port: str
    host_port: str | None = None
    protocol: str = "tcp"


class DiscoveredContainer(BaseModel):
    id: str
    name: str
    image: str
    status: str
    state: str
    ports: list[ContainerPortInfo]
    networks: list[str]
    labels: dict[str, str]


class AppProfileResponse(BaseModel):
    name: str
    recommended_port: int = Field(..., ge=1, le=65535)
    healthcheck_path: str | None
    preserve_host_header: bool
    post_setup_reminder: str | None
    image_patterns: list[str]


class ProfilesResponse(BaseModel):
    profiles: dict[str, AppProfileResponse]


class ProfileDetectionResponse(BaseModel):
    detected_profile: str | None
    profile: AppProfileResponse | None


class DiscoveryResponse(BaseModel):
    containers: list[DiscoveredContainer]
    total: int
