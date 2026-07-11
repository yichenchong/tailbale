"""Shared helpers for the service-API test suite (split from test_services_api.py)."""

from unittest.mock import MagicMock

from app.models.service import Service
from app.models.service_status import ServiceStatus

DEFAULT_SERVICE_API_BODY = {
    "name": "Nextcloud",
    "upstream_container_id": "abc123def456",
    "upstream_container_name": "nextcloud",
    "upstream_scheme": "http",
    "upstream_port": 80,
    "hostname": "nextcloud.example.com",
    "base_domain": "example.com",
}

DEFAULT_SERVICE_DB_VALUES = {
    "name": "TestApp",
    "upstream_container_id": "abc123",
    "upstream_container_name": "testapp",
    "upstream_scheme": "http",
    "upstream_port": 80,
    "hostname": "testapp.example.com",
    "base_domain": "example.com",
    "edge_container_name": "edge_testapp",
    "network_name": "edge_net_testapp",
    "ts_hostname": "edge-testapp",
}


def service_api_body(**overrides):
    """Return the canonical JSON body for POST /api/services tests."""
    body = DEFAULT_SERVICE_API_BODY.copy()
    body.update(overrides)
    return body


def create_service_api(client, **overrides):
    """Create a service through the public API using canonical defaults."""
    return client.post("/api/services", json=service_api_body(**overrides))


def service_db_values(**overrides):
    """Return canonical Service model kwargs for direct DB setup."""
    values = DEFAULT_SERVICE_DB_VALUES.copy()
    values.update(overrides)
    return values


def create_service_db(
    db,
    *,
    status_phase: str | None = "pending",
    commit: bool = True,
    **overrides,
):
    """Insert a service directly into the DB for tests.

    ``status_phase=None`` creates only the service row for tests that assert
    missing-status behavior.
    """
    svc = Service(**service_db_values(**overrides))
    db.add(svc)
    db.flush()
    if status_phase is not None:
        status = ServiceStatus(service_id=svc.id, phase=status_phase)
        db.add(status)
    if commit:
        db.commit()
    return svc


def make_container(exposed_ports=None, port_bindings=None, *, name="testcontainer"):
    """Create a mock Docker container with port metadata."""
    c = MagicMock()
    c.name = name
    c.attrs = {
        "Config": {"ExposedPorts": exposed_ports or {}},
        "HostConfig": {"PortBindings": port_bindings or {}},
    }
    return c


_create_service = create_service_api
_create_service_in_db = create_service_db
_make_container = make_container
