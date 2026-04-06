"""Pydantic schemas for service CRUD operations."""

import re

from pydantic import BaseModel, Field, field_validator


class ServiceCreate(BaseModel):
    """Request body for creating a new service exposure."""

    name: str = Field(..., min_length=1, max_length=128)
    upstream_container_id: str = Field(..., min_length=1)
    upstream_container_name: str = Field(..., min_length=1)
    upstream_scheme: str = Field(default="http", pattern=r"^(http|https)$")
    upstream_port: int = Field(..., ge=1, le=65535)
    healthcheck_path: str | None = None
    hostname: str = Field(..., min_length=1)
    base_domain: str = Field(..., min_length=1)
    enabled: bool = True
    preserve_host_header: bool = True
    custom_caddy_snippet: str | None = None
    app_profile: str | None = None

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$", v):
            raise ValueError("Invalid hostname format")
        return v


class ServiceUpdate(BaseModel):
    """Request body for updating a service exposure."""

    name: str | None = None
    upstream_scheme: str | None = Field(default=None, pattern=r"^(http|https)$")
    upstream_port: int | None = Field(default=None, ge=1, le=65535)
    healthcheck_path: str | None = None
    hostname: str | None = None
    enabled: bool | None = None
    preserve_host_header: bool | None = None
    custom_caddy_snippet: str | None = None
    app_profile: str | None = None

    @field_validator("hostname")
    @classmethod
    def validate_hostname(cls, v: str | None) -> str | None:
        if v is not None and not re.match(
            r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$", v
        ):
            raise ValueError("Invalid hostname format")
        return v


class ServiceStatusResponse(BaseModel):
    phase: str
    message: str | None = None
    tailscale_ip: str | None = None
    edge_container_id: str | None = None
    last_reconciled_at: str | None = None
    health_checks: dict[str, bool] | None = None
    cert_expires_at: str | None = None


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

    status: ServiceStatusResponse | None = None

    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


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


class DiscoveryResponse(BaseModel):
    containers: list[DiscoveredContainer]
    total: int
