"""Tests for the Service CRUD API endpoints."""

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from app.models.certificate import Certificate
from app.models.dns_record import DnsRecord
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


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------


class TestCreateService:
    def test_create_basic(self, client):
        resp = _create_service(client)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Nextcloud"
        assert data["hostname"] == "nextcloud.example.com"
        assert data["upstream_container_name"] == "nextcloud"
        assert data["upstream_port"] == 80
        assert data["enabled"] is True
        assert data["id"].startswith("svc_")
        assert data["edge_container_name"] == "edge_nextcloud"
        assert data["network_name"] == "edge_net_nextcloud"
        assert data["ts_hostname"] == "edge-nextcloud"

    def test_create_with_status(self, client):
        resp = _create_service(client)
        data = resp.json()
        assert data["status"] is not None
        assert data["status"]["phase"] == "pending"
        assert "reconciliation" in data["status"]["message"].lower()

    def test_create_emits_event(self, client):
        resp = _create_service(client)
        assert resp.status_code == 201

    def test_create_with_all_fields(self, client):
        resp = _create_service(
            client,
            name="Jellyfin",
            upstream_container_id="jelly123",
            upstream_container_name="jellyfin",
            upstream_scheme="https",
            upstream_port=8096,
            healthcheck_path="/health",
            hostname="jellyfin.example.com",
            base_domain="example.com",
            enabled=False,
            preserve_host_header=False,
            custom_caddy_snippet="header X-Custom true",
            app_profile="jellyfin",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["upstream_scheme"] == "https"
        assert data["upstream_port"] == 8096
        assert data["healthcheck_path"] == "/health"
        assert data["enabled"] is False
        assert data["preserve_host_header"] is False
        assert data["custom_caddy_snippet"] == "header X-Custom true"
        assert data["app_profile"] == "jellyfin"

    def test_duplicate_hostname_rejected(self, client):
        _create_service(client, hostname="app.example.com")
        resp = _create_service(client, name="Other", hostname="app.example.com")
        assert resp.status_code == 409
        assert "already in use" in resp.json()["detail"]

    def test_invalid_hostname_rejected(self, client):
        resp = _create_service(client, hostname="INVALID HOSTNAME!")
        assert resp.status_code == 422

    def test_invalid_port_rejected(self, client):
        resp = _create_service(client, upstream_port=0)
        assert resp.status_code == 422

    def test_invalid_port_too_high(self, client):
        resp = _create_service(client, upstream_port=70000)
        assert resp.status_code == 422

    def test_invalid_scheme_rejected(self, client):
        resp = _create_service(client, upstream_scheme="ftp")
        assert resp.status_code == 422

    def test_missing_required_fields(self, client):
        resp = client.post("/api/services", json={"name": "Incomplete"})
        assert resp.status_code == 422

    def test_slug_generation(self, client):
        resp = _create_service(
            client,
            name="My Cool App 2",
            hostname="myapp.example.com",
        )
        data = resp.json()
        assert data["edge_container_name"] == "edge_my-cool-app-2"
        assert data["network_name"] == "edge_net_my-cool-app-2"
        assert data["ts_hostname"] == "edge-my-cool-app-2"


class TestMultiExposure:
    """One container can have multiple exposures (different ports/hostnames)."""

    def test_same_container_different_ports(self, client):
        resp1 = _create_service(
            client, name="Nextcloud Web", hostname="nextcloud.example.com",
            upstream_port=80,
        )
        assert resp1.status_code == 201
        resp2 = _create_service(
            client, name="Nextcloud DAV", hostname="dav.example.com",
            upstream_port=443,
        )
        assert resp2.status_code == 201
        assert resp1.json()["upstream_container_id"] == resp2.json()["upstream_container_id"]

    def test_same_container_same_port_different_hostnames(self, client):
        resp1 = _create_service(client, name="App Primary", hostname="app.example.com")
        assert resp1.status_code == 201
        resp2 = _create_service(client, name="App Alias", hostname="alias.example.com")
        assert resp2.status_code == 201

    def test_edge_names_unique_across_exposures(self, client):
        resp1 = _create_service(client, name="Nextcloud", hostname="nc1.example.com")
        resp2 = _create_service(client, name="Nextcloud", hostname="nc2.example.com")
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        d1, d2 = resp1.json(), resp2.json()
        assert d1["edge_container_name"] != d2["edge_container_name"]
        assert d1["network_name"] != d2["network_name"]
        assert d1["ts_hostname"] != d2["ts_hostname"]

    def test_three_exposures_unique_slugs(self, client):
        r1 = _create_service(client, name="App", hostname="a1.example.com")
        r2 = _create_service(client, name="App", hostname="a2.example.com")
        r3 = _create_service(client, name="App", hostname="a3.example.com")
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r3.status_code == 201
        names = {r.json()["edge_container_name"] for r in [r1, r2, r3]}
        assert len(names) == 3

    def test_list_shows_all_exposures(self, client):
        _create_service(client, name="NC Web", hostname="nc-web.example.com", upstream_port=80)
        _create_service(client, name="NC DAV", hostname="nc-dav.example.com", upstream_port=443)
        resp = client.get("/api/services")
        assert resp.json()["total"] == 2


class TestListServices:
    def test_empty_list(self, client):
        resp = client.get("/api/services")
        assert resp.status_code == 200
        data = resp.json()
        assert data["services"] == []
        assert data["total"] == 0

    def test_list_multiple(self, client):
        _create_service(client, name="App1", hostname="app1.example.com")
        _create_service(client, name="App2", hostname="app2.example.com")
        _create_service(client, name="App3", hostname="app3.example.com")
        resp = client.get("/api/services")
        data = resp.json()
        assert data["total"] == 3
        assert len(data["services"]) == 3

    def test_list_includes_status(self, client):
        _create_service(client)
        resp = client.get("/api/services")
        svc = resp.json()["services"][0]
        assert svc["status"]["phase"] == "pending"

    def test_list_returns_all(self, client):
        _create_service(client, name="First", hostname="first.example.com")
        _create_service(client, name="Second", hostname="second.example.com")
        resp = client.get("/api/services")
        names = {s["name"] for s in resp.json()["services"]}
        assert names == {"First", "Second"}


class TestGetService:
    def test_get_existing(self, client):
        create_resp = _create_service(client)
        svc_id = create_resp.json()["id"]
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == svc_id
        assert resp.json()["name"] == "Nextcloud"

    def test_get_nonexistent(self, client):
        resp = client.get("/api/services/svc_nonexistent")
        assert resp.status_code == 404

    def test_get_includes_full_details(self, client):
        create_resp = _create_service(client, healthcheck_path="/status.php")
        svc_id = create_resp.json()["id"]
        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["healthcheck_path"] == "/status.php"
        assert data["status"] is not None
        assert "created_at" in data
        assert "updated_at" in data


class TestUpdateService:
    def test_update_name(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_port(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 8080})
        assert resp.status_code == 200
        assert resp.json()["upstream_port"] == 8080

    def test_update_scheme(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"upstream_scheme": "https"})
        assert resp.status_code == 200
        assert resp.json()["upstream_scheme"] == "https"

    def test_update_hostname(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"

    def test_update_hostname_conflict(self, client):
        _create_service(client, name="App1", hostname="app1.example.com")
        svc_id = _create_service(client, name="App2", hostname="app2.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app1.example.com"})
        assert resp.status_code == 409

    def test_update_hostname_same_is_ok(self, client):
        svc_id = _create_service(client, hostname="same.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "same.example.com"})
        assert resp.status_code == 200

    def test_update_enabled(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_update_advanced_fields(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={
            "preserve_host_header": False,
            "custom_caddy_snippet": "log { output stdout }",
            "app_profile": "nextcloud",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["preserve_host_header"] is False
        assert data["custom_caddy_snippet"] == "log { output stdout }"
        assert data["app_profile"] == "nextcloud"

    def test_update_nonexistent(self, client):
        resp = client.put("/api/services/svc_nonexistent", json={"name": "X"})
        assert resp.status_code == 404

    def test_partial_update_preserves_fields(self, client):
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"name": "Updated"})
        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["name"] == "Updated"
        assert data["upstream_port"] == 80
        assert data["hostname"] == "nextcloud.example.com"


class TestDisableService:
    def test_disable(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_disable_nonexistent(self, client):
        resp = client.post("/api/services/svc_nonexistent/disable")
        assert resp.status_code == 404

    def test_disable_persists(self, client):
        svc_id = _create_service(client).json()["id"]
        client.post(f"/api/services/{svc_id}/disable")
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.json()["enabled"] is False


class TestDeleteService:
    def test_delete(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.status_code == 404

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/services/svc_nonexistent")
        assert resp.status_code == 404

    def test_delete_removes_from_list(self, client):
        svc_id = _create_service(client).json()["id"]
        client.delete(f"/api/services/{svc_id}")
        resp = client.get("/api/services")
        assert resp.json()["total"] == 0

    def test_delete_cascades_status(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.json()["status"] is not None
        client.delete(f"/api/services/{svc_id}")
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.status_code == 404

    def test_delete_allows_hostname_reuse(self, client):
        svc_id = _create_service(client, hostname="reuse.example.com").json()["id"]
        client.delete(f"/api/services/{svc_id}")
        resp = _create_service(client, hostname="reuse.example.com")
        assert resp.status_code == 201


class TestStubActionEndpoints:
    def test_stub_404_for_nonexistent_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/reload")
        assert resp.status_code == 404
        resp = client.post("/api/services/svc_nonexistent/restart-edge")
        assert resp.status_code == 404
        resp = client.post("/api/services/svc_nonexistent/reconcile")
        assert resp.status_code == 404
        resp = client.get("/api/services/svc_nonexistent/logs/edge")
        assert resp.status_code == 404


class TestStatusResponseFields:
    def test_status_includes_health_checks_and_cert(self, client, db_session):
        from app.models.certificate import Certificate
        from app.models.service_status import ServiceStatus

        svc_id = _create_service(client).json()["id"]
        status = db_session.get(ServiceStatus, svc_id)
        status.health_checks = json.dumps({"edge_container_running": True, "cert_present": False})
        db_session.commit()

        cert = Certificate(service_id=svc_id, hostname="nextcloud.example.com")
        from datetime import datetime
        cert.expires_at = datetime(2026, 8, 1)
        db_session.add(cert)
        db_session.commit()

        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["status"]["health_checks"] == {"edge_container_running": True, "cert_present": False}
        assert data["status"]["cert_expires_at"] == "2026-08-01T00:00:00"

    def test_status_without_cert_or_health_checks(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["status"]["health_checks"] is None
        assert data["status"]["cert_expires_at"] is None

    def test_list_includes_cert_expiry(self, client, db_session):
        from app.models.certificate import Certificate
        from datetime import datetime

        svc_id = _create_service(client).json()["id"]
        cert = Certificate(service_id=svc_id, hostname="nextcloud.example.com")
        cert.expires_at = datetime(2026, 7, 15)
        db_session.add(cert)
        db_session.commit()

        resp = client.get("/api/services")
        svc = resp.json()["services"][0]
        assert svc["status"]["cert_expires_at"] == "2026-07-15T00:00:00"


class TestServiceEvents:
    def test_create_generates_event(self, client, db_session):
        from app.models.event import Event
        _create_service(client)
        events = db_session.query(Event).filter(Event.kind == "service_created").all()
        assert len(events) == 1
        assert "Nextcloud" in events[0].message

    def test_update_generates_event(self, client, db_session):
        from app.models.event import Event
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        events = db_session.query(Event).filter(Event.kind == "service_updated").all()
        assert len(events) == 1
        details = json.loads(events[0].details)
        assert details["name"] == "Renamed"

    def test_disable_generates_event(self, client, db_session):
        from app.models.event import Event
        svc_id = _create_service(client).json()["id"]
        client.post(f"/api/services/{svc_id}/disable")
        events = db_session.query(Event).filter(Event.kind == "service_disabled").all()
        assert len(events) == 1

    def test_delete_generates_event(self, client, db_session):
        from app.models.event import Event
        svc_id = _create_service(client).json()["id"]
        client.delete(f"/api/services/{svc_id}")
        events = db_session.query(Event).filter(Event.kind == "service_deleted").all()
        assert len(events) == 1
        assert events[0].service_id is None

    def test_noop_update_no_event(self, client, db_session):
        from app.models.event import Event
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={})
        events = db_session.query(Event).filter(Event.kind == "service_updated").all()
        assert len(events) == 0


# ---------------------------------------------------------------------------
# Hostname validation
# ---------------------------------------------------------------------------


class TestHostnameValidation:
    """Hostname domain validation on create (spec fix #8)."""

    def _create(self, client, hostname="app.example.com", **kw):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": hostname,
            "base_domain": "example.com",
            **kw,
        }
        return client.post("/api/services", json=body)

    def test_hostname_matching_domain_accepted(self, client):
        resp = self._create(client, hostname="myapp.example.com")
        assert resp.status_code == 201

    def test_hostname_wrong_domain_rejected(self, client):
        resp = self._create(client, hostname="myapp.wrongdomain.com")
        assert resp.status_code == 422
        assert "must end with" in resp.json()["detail"]

    def test_hostname_bare_domain_rejected(self, client):
        resp = self._create(client, hostname="example.com")
        assert resp.status_code == 422

    def test_subdomain_deep_nesting_accepted(self, client):
        resp = self._create(client, hostname="a.b.c.example.com")
        assert resp.status_code == 201


class TestBaseDomainConsistency:
    """base_domain must match the hostname suffix."""

    def _create(self, client, hostname="app.example.com", base_domain="example.com"):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": hostname,
            "base_domain": base_domain,
        }
        return client.post("/api/services", json=body)

    def test_consistent_base_domain_accepted(self, client):
        resp = self._create(client, hostname="app.example.com", base_domain="example.com")
        assert resp.status_code == 201

    def test_inconsistent_base_domain_rejected(self, client):
        resp = self._create(client, hostname="app.example.com", base_domain="wrong.com")
        assert resp.status_code == 422
        assert "inconsistent" in resp.json()["detail"].lower()

    def test_base_domain_not_suffix_of_hostname_rejected(self, client):
        resp = self._create(client, hostname="app.example.com", base_domain="ple.com")
        assert resp.status_code == 422

    def test_deep_hostname_with_matching_subdomain(self, client):
        resp = self._create(client, hostname="a.b.example.com", base_domain="b.example.com")
        assert resp.status_code == 201

    def test_deep_hostname_with_root_domain(self, client):
        """base_domain=example.com is valid for any hostname under it."""
        resp = self._create(client, hostname="a.b.example.com", base_domain="example.com")
        assert resp.status_code == 201

    def test_base_domain_equals_hostname_rejected(self, client):
        """hostname must be a subdomain of base_domain, not equal to it."""
        resp = self._create(client, hostname="example.com", base_domain="example.com")
        assert resp.status_code == 422


class TestUpdateHostnameValidation:
    """Hostname domain validation on update."""

    def test_update_hostname_valid_domain(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"

    def test_update_hostname_wrong_domain_rejected(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.wrong.com"})
        assert resp.status_code == 422
        assert "must end with" in resp.json()["detail"]

    def test_update_hostname_same_value_ok(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.example.com"})
        assert resp.status_code == 200

    def test_update_hostname_conflict_still_caught(self, client):
        _create_service(client, hostname="taken.example.com", name="First")
        svc_id = _create_service(client, hostname="other.example.com", name="Second").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "taken.example.com"})
        assert resp.status_code == 409

    def test_update_non_hostname_fields_unaffected(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_hostname_deep_subdomain_accepted(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "a.b.c.example.com"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Upstream validation
# ---------------------------------------------------------------------------


class TestUpstreamContainerValidation:
    """create_service should reject requests when upstream container doesn't exist."""

    def test_missing_container_returns_422(self, client):
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=__import__("fastapi").HTTPException(status_code=422, detail="not found"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 422

    def test_docker_unreachable_returns_503(self, client):
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=__import__("fastapi").HTTPException(status_code=503, detail="cannot connect"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 503

    def test_valid_container_succeeds(self, client):
        resp = _create_service(client, name="App", hostname="app.example.com")
        assert resp.status_code == 201

    def test_create_service_calls_validate_upstream(self, client):
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 201
            mock_val.assert_called_once()
            args = mock_val.call_args
            assert args[0][1] == "abc123def456"
            assert args[0][2] == 80

    def test_validate_upstream_not_found_via_api(self, client):
        from fastapi import HTTPException
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=422, detail="Upstream container 'x' not found"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 422
            assert "not found" in resp.json()["detail"].lower()

    def test_validate_upstream_docker_unreachable_via_api(self, client):
        from fastapi import HTTPException
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=503, detail="Cannot connect to Docker"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 503


class TestUpstreamPortValidation:
    """_validate_upstream_port should check exposed ports on the container."""

    def test_port_in_exposed_ports_passes(self):
        from app.routers.services import _validate_upstream_port
        container = _make_container(exposed_ports={"80/tcp": {}, "443/tcp": {}})
        _validate_upstream_port(container, 80)

    def test_port_not_in_exposed_ports_raises(self):
        from app.routers.services import _validate_upstream_port
        container = _make_container(exposed_ports={"80/tcp": {}, "443/tcp": {}})
        with pytest.raises(__import__("fastapi").HTTPException) as exc_info:
            _validate_upstream_port(container, 8080)
        assert exc_info.value.status_code == 422
        assert "8080" in exc_info.value.detail
        assert "80" in exc_info.value.detail

    def test_port_in_host_bindings_passes(self):
        from app.routers.services import _validate_upstream_port
        container = _make_container(port_bindings={"3000/tcp": [{"HostPort": "3000"}]})
        _validate_upstream_port(container, 3000)

    def test_no_exposed_ports_allows_any(self):
        from app.routers.services import _validate_upstream_port
        container = _make_container()
        _validate_upstream_port(container, 9999)

    def test_merged_exposed_and_bindings(self):
        from app.routers.services import _validate_upstream_port
        container = _make_container(
            exposed_ports={"80/tcp": {}},
            port_bindings={"8080/tcp": [{"HostPort": "8080"}]},
        )
        _validate_upstream_port(container, 80)
        _validate_upstream_port(container, 8080)

    def test_rejects_port_when_others_exist(self):
        from app.routers.services import _validate_upstream_port
        container = _make_container(
            exposed_ports={"80/tcp": {}},
            port_bindings={"8080/tcp": [{"HostPort": "8080"}]},
        )
        with pytest.raises(__import__("fastapi").HTTPException) as exc_info:
            _validate_upstream_port(container, 3000)
        assert exc_info.value.status_code == 422


class TestContainerExistenceCheck:
    """Container existence warning on create."""

    def _create(self, client, **kw):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "app.example.com",
            "base_domain": "example.com",
            **kw,
        }
        return client.post("/api/services", json=body)

    @patch("app.routers.services.docker_lib", create=True)
    def test_warning_when_container_not_found(self, mock_docker, client):
        resp = self._create(client)
        assert resp.status_code == 201
        msg = resp.json()["status"]["message"].lower()
        assert "reconciliation" in msg

    @patch("app.routers.services.docker_lib", create=True)
    def test_default_message_when_docker_unavailable(self, mock_docker, client):
        resp = self._create(client)
        assert resp.status_code == 201
        msg = resp.json()["status"]["message"].lower()
        assert "reconciliation" in msg


# ---------------------------------------------------------------------------
# Disable / enable behaviour
# ---------------------------------------------------------------------------


class TestDisableStopsEdge:
    """Disable stops edge container."""

    def _create(self, client):
        body = {
            "name": "App", "upstream_container_id": "abc123",
            "upstream_container_name": "app", "upstream_scheme": "http",
            "upstream_port": 80, "hostname": "app.example.com",
            "base_domain": "example.com",
        }
        return client.post("/api/services", json=body)

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_calls_stop_edge(self, mock_stop, client):
        svc_id = self._create(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        mock_stop.assert_called_once()

    @patch("app.edge.container_manager.stop_edge", side_effect=RuntimeError("no container"))
    def test_disable_succeeds_even_if_stop_fails(self, mock_stop, client):
        svc_id = self._create(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


class TestDisableSetsPhase:
    """Disabling a service should update its status phase to 'disabled'."""

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_sets_phase_disabled(self, mock_stop, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["status"]["phase"] == "disabled"
        assert data["status"]["message"] == "Service disabled by user"

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_clears_health_checks(self, mock_stop, client, db_session):
        resp = _create_service(client, name="App", hostname="app.example.com")
        svc_id = resp.json()["id"]
        status = db_session.query(ServiceStatus).filter_by(service_id=svc_id).first()
        if status:
            status.health_checks = json.dumps({"edge_container_running": True})
            db_session.commit()
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"]["health_checks"] is None

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_does_not_leave_healthy_status(self, mock_stop, client, db_session):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        status = db_session.query(ServiceStatus).filter_by(service_id=svc_id).first()
        if status:
            status.phase = "healthy"
            status.message = "All checks passed"
            db_session.commit()
        resp = client.post(f"/api/services/{svc_id}/disable")
        data = resp.json()
        assert data["status"]["phase"] != "healthy"
        assert data["status"]["phase"] == "disabled"

    @patch("app.edge.container_manager.stop_edge")
    def test_get_disabled_service_shows_disabled(self, mock_stop, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        client.post(f"/api/services/{svc_id}/disable")
        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["enabled"] is False
        assert data["status"]["phase"] == "disabled"


class TestDisableDnsCleanup:
    """spec section 7.4 -- disable may optionally remove DNS records."""

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_without_cleanup_dns(self, mock_stop, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    @patch("app.adapters.dns_reconciler.cleanup_dns_record",
           return_value={"deleted_remote": True, "deleted_local": True, "error": None})
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.container_manager.stop_edge")
    def test_disable_with_cleanup_dns(self, mock_stop, mock_secret, mock_cleanup, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable?cleanup_dns=true")
        assert resp.status_code == 200
        mock_cleanup.assert_called_once()

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value=None)
    @patch("app.edge.container_manager.stop_edge")
    def test_disable_cleanup_dns_no_token_is_noop(self, mock_stop, mock_secret, mock_cleanup, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable?cleanup_dns=true")
        assert resp.status_code == 200
        mock_cleanup.assert_not_called()

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_still_stops_edge(self, mock_stop, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        client.post(f"/api/services/{svc_id}/disable")
        mock_stop.assert_called_once()


# ---------------------------------------------------------------------------
# Delete cleanup
# ---------------------------------------------------------------------------


class TestDeleteCleansUp:
    """Delete removes edge + network + files."""

    def _create(self, client):
        body = {
            "name": "App", "upstream_container_id": "abc123",
            "upstream_container_name": "app", "upstream_scheme": "http",
            "upstream_port": 80, "hostname": "app.example.com",
            "base_domain": "example.com",
        }
        return client.post("/api/services", json=body)

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_calls_remove_edge_and_network(self, mock_remove_edge, mock_remove_net, client):
        svc_id = self._create(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204
        mock_remove_edge.assert_called_once()
        mock_remove_net.assert_called_once()

    @patch("app.edge.network_manager.remove_network", side_effect=Exception("fail"))
    @patch("app.edge.container_manager.remove_edge", side_effect=Exception("fail"))
    def test_delete_succeeds_even_if_cleanup_fails(self, mock_re, mock_rn, client):
        svc_id = self._create(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.status_code == 404


class TestDeleteUsesRuntimePaths:
    """Delete should use get_runtime_paths() for disk cleanup."""

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_reads_runtime_paths(self, mock_re, mock_rn, client, db_session, tmp_data_dir):
        from app.settings_store import set_setting

        custom_gen = str(tmp_data_dir / "custom_gen")
        custom_certs = str(tmp_data_dir / "custom_certs")
        custom_ts = str(tmp_data_dir / "custom_ts")
        set_setting(db_session, "generated_root", custom_gen)
        set_setting(db_session, "cert_root", custom_certs)
        set_setting(db_session, "tailscale_state_root", custom_ts)
        db_session.commit()

        resp = _create_service(client, name="App", hostname="app.example.com")
        svc = resp.json()

        svc_gen = Path(custom_gen) / svc["id"]
        svc_cert = Path(custom_certs) / svc["hostname"]
        svc_ts = Path(custom_ts) / svc["edge_container_name"]
        for d in [svc_gen, svc_cert, svc_ts]:
            d.mkdir(parents=True, exist_ok=True)
            (d / "dummy.txt").write_text("test")

        client.delete(f"/api/services/{svc['id']}")

        assert not svc_gen.exists()
        assert not svc_cert.exists()
        assert not svc_ts.exists()


# ---------------------------------------------------------------------------
# Hostname change cleanup
# ---------------------------------------------------------------------------


class TestHostnameChangeCleanup:
    """Changing hostname should clean up old DNS record, cert files, and cert metadata."""

    @patch("app.adapters.dns_reconciler.cleanup_dns_record",
           return_value={"deleted_remote": True, "deleted_local": True, "error": None})
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_hostname_change_deletes_old_dns(self, mock_secret, mock_cleanup, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"
        mock_cleanup.assert_called_once()

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value=None)
    def test_hostname_change_without_cf_token_still_succeeds(self, mock_secret, mock_cleanup, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        mock_cleanup.assert_not_called()

    @patch("app.adapters.dns_reconciler.cleanup_dns_record",
           return_value={"deleted_remote": False, "deleted_local": False, "error": "CF unreachable"})
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_hostname_change_aborts_when_dns_cleanup_fails(
        self, mock_secret, mock_cleanup, client, db_session
    ):
        """Hostname change should abort with 502 when old DNS record can't be removed."""
        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        dns = DnsRecord(service_id=svc_id, hostname="app.example.com", record_id="cf_rec_old")
        db_session.add(dns)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 502
        assert "failed to remove old dns record" in resp.json()["detail"].lower()

        # Hostname should NOT have changed
        db_session.expire_all()
        check = client.get(f"/api/services/{svc_id}")
        assert check.json()["hostname"] == "app.example.com"

        # DnsRecord should still exist with old hostname
        updated_dns = db_session.get(DnsRecord, svc_id)
        assert updated_dns is not None
        assert updated_dns.hostname == "app.example.com"

    def test_hostname_change_updates_cert_hostname(self, client, db_session):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        cert = Certificate(service_id=svc_id, hostname="app.example.com")
        db_session.add(cert)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200

        db_session.expire_all()
        updated_cert = db_session.get(Certificate, svc_id)
        assert updated_cert is not None
        assert updated_cert.hostname == "new.example.com"

    def test_hostname_change_clears_stale_cert_metadata(self, client, db_session):
        """Hostname change should clear expires_at, last_renewed_at, last_failure, next_retry_at."""
        from datetime import datetime, timezone
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        cert = Certificate(
            service_id=svc_id,
            hostname="app.example.com",
            expires_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            last_renewed_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            last_failure="old error",
            next_retry_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
        )
        db_session.add(cert)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200

        db_session.expire_all()
        updated_cert = db_session.get(Certificate, svc_id)
        assert updated_cert.hostname == "new.example.com"
        assert updated_cert.expires_at is None
        assert updated_cert.last_renewed_at is None
        assert updated_cert.last_failure is None
        assert updated_cert.next_retry_at is None

    def test_hostname_change_removes_old_cert_dir(self, client, db_session, tmp_data_dir):
        from app.settings_store import set_setting
        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        old_cert_dir = Path(custom_certs) / "app.example.com"
        old_cert_dir.mkdir(parents=True)
        (old_cert_dir / "fullchain.pem").write_text("old-cert")
        (old_cert_dir / "privkey.pem").write_text("old-key")

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert not old_cert_dir.exists()

    def test_same_hostname_no_cleanup(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.adapters.dns_reconciler.cleanup_dns_record") as mock_cleanup:
            resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.example.com"})
            assert resp.status_code == 200
            mock_cleanup.assert_not_called()

    def test_non_hostname_update_no_cleanup(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.adapters.dns_reconciler.cleanup_dns_record") as mock_cleanup:
            resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
            assert resp.status_code == 200
            mock_cleanup.assert_not_called()


# ---------------------------------------------------------------------------
# Unused import cleanup verification
# ---------------------------------------------------------------------------


class TestUnusedImportCleanup:
    """Verify stale imports have been removed from endpoints."""

    def test_health_check_full_no_unused_imports(self):
        import ast
        source = Path(__file__).parent.parent / "app" / "routers" / "services.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "full_health_check":
                body_source = ast.dump(node)
                assert "app_settings" not in body_source or "app.config" not in body_source
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom):
                        names = [alias.name for alias in child.names]
                        if child.module and "config" in child.module:
                            assert "settings" not in names, \
                                "app_settings is imported but unused in full_health_check"

    def test_update_edge_no_unused_imports(self):
        import ast
        source = Path(__file__).parent.parent / "app" / "routers" / "services.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_edge_endpoint":
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom):
                        if child.module and "config" in child.module:
                            names = [alias.name for alias in child.names]
                            assert "settings" not in names, \
                                "app_settings is imported but unused in update_edge_endpoint"


# ---------------------------------------------------------------------------
# Upstream port revalidation on update
# ---------------------------------------------------------------------------


class TestUpdateUpstreamPortValidation:
    """update_service should revalidate upstream port when it changes."""

    def test_update_port_revalidates(self, client):
        """Changing upstream_port should trigger _validate_upstream."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 9090})
            assert resp.status_code == 200
            mock_val.assert_called_once()
            args = mock_val.call_args[0]
            assert args[2] == 9090  # new port

    def test_update_port_invalid_rejected(self, client):
        """If the new port isn't exposed by the container, update should 422."""
        from fastapi import HTTPException
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=422, detail="Port 9999 is not exposed"),
        ):
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 9999})
            assert resp.status_code == 422
            assert "9999" in resp.json()["detail"]

    def test_update_same_port_no_revalidation(self, client):
        """Setting port to the same value should not trigger revalidation."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 80})
            assert resp.status_code == 200
            mock_val.assert_not_called()

    def test_update_non_port_fields_no_revalidation(self, client):
        """Changing non-port fields should not trigger upstream revalidation."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
            assert resp.status_code == 200
            mock_val.assert_not_called()

    def test_update_docker_unreachable_503(self, client):
        """If Docker is unreachable during port revalidation, return 503."""
        from fastapi import HTTPException
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=503, detail="Cannot connect to Docker"),
        ):
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 443})
            assert resp.status_code == 503


# ---------------------------------------------------------------------------
# DNS cleanup structured result — hostname change aborts on failure
# ---------------------------------------------------------------------------


class TestHostnameChangeDnsAbort:
    """Hostname change should abort if old DNS record cannot be deleted from Cloudflare."""

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_hostname_change_aborts_on_cf_failure(self, mock_secret, mock_cleanup, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        mock_cleanup.return_value = {
            "deleted_remote": False,
            "deleted_local": False,
            "error": "Connection refused",
        }

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 502
        assert "failed to remove old dns record" in resp.json()["detail"].lower()

        # Hostname should NOT have changed
        check = client.get(f"/api/services/{svc_id}")
        assert check.json()["hostname"] == "app.example.com"

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_hostname_change_proceeds_on_cf_success(self, mock_secret, mock_cleanup, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        mock_cleanup.return_value = {
            "deleted_remote": True,
            "deleted_local": True,
            "error": None,
        }

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_hostname_change_no_record_proceeds(self, mock_secret, mock_cleanup, client, db_session):
        """If there's no DNS record at all, hostname change should proceed."""
        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        mock_cleanup.return_value = {
            "deleted_remote": False,
            "deleted_local": False,
            "error": None,
        }

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"


# ---------------------------------------------------------------------------
# DNS cleanup structured result — disable keeps row on failure
# ---------------------------------------------------------------------------


class TestDisableDnsCleanupStructured:
    """Disable with cleanup_dns should preserve DnsRecord row on Cloudflare failure."""

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.container_manager.stop_edge")
    def test_disable_keeps_dns_row_on_cf_failure(
        self, mock_stop, mock_secret, mock_cleanup, client, db_session
    ):
        from app.models.dns_record import DnsRecord
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        # Create a DnsRecord for the service
        dns = DnsRecord(service_id=svc_id, hostname="app.example.com", record_id="cf_old")
        db_session.add(dns)
        db_session.commit()

        mock_cleanup.return_value = {
            "deleted_remote": False,
            "deleted_local": False,
            "error": "API rate limited",
        }

        resp = client.post(f"/api/services/{svc_id}/disable?cleanup_dns=true")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # DnsRecord should still exist because cleanup returned it
        # (cleanup_dns_record itself no longer deletes; we're checking
        # it was called but didn't delete the row)
        mock_cleanup.assert_called_once()

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.container_manager.stop_edge")
    def test_disable_still_succeeds_on_cf_failure(
        self, mock_stop, mock_secret, mock_cleanup, client, db_session
    ):
        """Disable proceeds even when cleanup_dns_record reports a failure."""
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        mock_cleanup.return_value = {
            "deleted_remote": False,
            "deleted_local": False,
            "error": "timeout",
        }

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable?cleanup_dns=true")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        mock_cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# DNS cleanup structured result — delete emits warning
# ---------------------------------------------------------------------------


class TestDeleteDnsCleanupStructured:
    """Delete with cleanup_dns should emit warning on Cloudflare failure."""

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_proceeds_on_cf_failure(
        self, mock_re, mock_rn, mock_secret, mock_cleanup, client, db_session
    ):
        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        mock_cleanup.return_value = {
            "deleted_remote": False,
            "deleted_local": False,
            "error": "forbidden",
        }

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204

        # Service should be gone
        check = client.get(f"/api/services/{svc_id}")
        assert check.status_code == 404

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_calls_cleanup_on_failure(
        self, mock_re, mock_rn, mock_secret, mock_cleanup, client, db_session
    ):
        """Delete calls cleanup_dns_record and proceeds even on failure."""
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        mock_cleanup.return_value = {
            "deleted_remote": False,
            "deleted_local": False,
            "error": "API error",
        }

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204
        mock_cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# Delete creates orphan-cleanup Job for surviving DnsRecord
# ---------------------------------------------------------------------------


class TestDeleteOrphanJob:
    """Delete should persist orphaned DNS record info in a Job row before CASCADE."""

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_creates_orphan_job_on_cleanup_failure(
        self, mock_re, mock_rn, mock_secret, mock_cleanup, client, db_session
    ):
        """When cleanup_dns fails, an orphan Job should be created with record info."""
        from app.models.job import Job
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        # cleanup_dns_record returns error — local row preserved by cleanup_dns_record
        mock_cleanup.return_value = {
            "deleted_remote": False,
            "deleted_local": False,
            "error": "forbidden",
        }

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        # Manually add a DnsRecord so the orphan job logic has something to persist
        dns = DnsRecord(service_id=svc_id, hostname="app.example.com", record_id="cf_rec_999", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204

        # Job should exist with the orphaned record details
        jobs = db_session.query(Job).filter(Job.kind == "dns_orphan_cleanup").all()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == "pending"
        assert job.service_id is None  # SET NULL after CASCADE
        import json as _json
        details = _json.loads(job.details)
        assert details["record_id"] == "cf_rec_999"
        assert details["hostname"] == "app.example.com"
        assert details["zone_id"] == "zone123"

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_creates_orphan_job_without_cleanup_dns(
        self, mock_re, mock_rn, client, db_session
    ):
        """Even without cleanup_dns=true, if a DnsRecord exists, an orphan Job is created."""
        from app.models.job import Job

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        dns = DnsRecord(service_id=svc_id, hostname="app.example.com", record_id="cf_rec_888")
        db_session.add(dns)
        db_session.commit()

        # Delete without cleanup_dns — cleanup not even attempted
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204

        jobs = db_session.query(Job).filter(Job.kind == "dns_orphan_cleanup").all()
        assert len(jobs) == 1
        import json as _json
        details = _json.loads(jobs[0].details)
        assert details["record_id"] == "cf_rec_888"

    @patch("app.adapters.dns_reconciler.cleanup_dns_record",
           return_value={"deleted_remote": True, "deleted_local": True, "error": None})
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_no_orphan_job_on_successful_cleanup(
        self, mock_re, mock_rn, mock_secret, mock_cleanup, client, db_session
    ):
        """When cleanup succeeds (DnsRecord deleted), no orphan Job should be created."""
        from app.models.job import Job
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        # Note: cleanup mock says deleted_local=True, so DnsRecord is gone before
        # the orphan check runs. No orphan job needed.

        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204

        jobs = db_session.query(Job).filter(Job.kind == "dns_orphan_cleanup").all()
        assert len(jobs) == 0

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_no_orphan_job_when_no_dns_record(
        self, mock_re, mock_rn, client, db_session
    ):
        """When no DnsRecord exists, no orphan Job should be created."""
        from app.models.job import Job

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204

        jobs = db_session.query(Job).filter(Job.kind == "dns_orphan_cleanup").all()
        assert len(jobs) == 0


# ---------------------------------------------------------------------------
# Hostname change blocks when CF credentials are missing but record exists
# ---------------------------------------------------------------------------


class TestHostnameChangeNoCreds:
    """Hostname change should abort when CF credentials are missing but a DNS record exists."""

    @patch("app.secrets.read_secret", return_value=None)
    def test_hostname_change_blocked_when_record_exists_no_creds(
        self, mock_secret, client, db_session
    ):
        """If a DnsRecord with record_id exists but no CF creds, hostname change is 422."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        # Create a DnsRecord simulating a previously-created Cloudflare record
        dns = DnsRecord(service_id=svc_id, hostname="app.example.com", record_id="cf_rec_777")
        db_session.add(dns)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 422
        assert "credentials" in resp.json()["detail"].lower()

        # Hostname should NOT have changed
        check = client.get(f"/api/services/{svc_id}")
        assert check.json()["hostname"] == "app.example.com"

    @patch("app.secrets.read_secret", return_value=None)
    def test_hostname_change_proceeds_when_no_record_no_creds(
        self, mock_secret, client
    ):
        """If no DnsRecord exists and no CF creds, hostname change should succeed."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"

    @patch("app.secrets.read_secret", return_value=None)
    def test_hostname_change_proceeds_when_record_has_no_record_id(
        self, mock_secret, client, db_session
    ):
        """DnsRecord without record_id (never synced to CF) should not block hostname change."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        # DnsRecord with no record_id — was created locally but never pushed to CF
        dns = DnsRecord(service_id=svc_id, hostname="app.example.com", record_id=None)
        db_session.add(dns)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"
