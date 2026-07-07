"""Shared helpers for the service-API test suite (split from test_services_api.py)."""

from unittest.mock import MagicMock

from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service(client, **overrides):
    """Helper to create a service with defaults via the API."""
    body = {
        "name": "Nextcloud",
        "upstream_container_id": "abc123def456",
        "upstream_container_name": "nextcloud",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "nextcloud.example.com",
        "base_domain": "example.com",
    }
    body.update(overrides)
    return client.post("/api/services", json=body)


def _create_service_in_db(db, **overrides):
    """Insert a service directly into the DB for testing."""
    defaults = {
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
    defaults.update(overrides)
    svc = Service(**defaults)
    db.add(svc)
    db.flush()
    status = ServiceStatus(service_id=svc.id, phase="pending")
    db.add(status)
    db.commit()
    return svc


def _make_container(exposed_ports=None, port_bindings=None):
    """Create a mock Docker container with port metadata."""
    c = MagicMock()
    c.name = "testcontainer"
    c.attrs = {
        "Config": {"ExposedPorts": exposed_ports or {}},
        "HostConfig": {"PortBindings": port_bindings or {}},
    }
    return c
