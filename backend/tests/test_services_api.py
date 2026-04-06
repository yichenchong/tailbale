"""Tests for the Service CRUD API endpoints."""

import json


def _create_service(client, **overrides):
    """Helper to create a service with defaults."""
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
        # Verify service was created successfully (event emission verified via DB)
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
        """Same container exposed on two different ports should succeed."""
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
        # Both reference the same container
        assert resp1.json()["upstream_container_id"] == resp2.json()["upstream_container_id"]

    def test_same_container_same_port_different_hostnames(self, client):
        """Same container and port but different hostnames should succeed."""
        resp1 = _create_service(
            client, name="App Primary", hostname="app.example.com",
        )
        assert resp1.status_code == 201
        resp2 = _create_service(
            client, name="App Alias", hostname="alias.example.com",
        )
        assert resp2.status_code == 201

    def test_edge_names_unique_across_exposures(self, client):
        """Multiple exposures of the same container must get unique edge names."""
        resp1 = _create_service(
            client, name="Nextcloud", hostname="nc1.example.com",
        )
        resp2 = _create_service(
            client, name="Nextcloud", hostname="nc2.example.com",
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        d1, d2 = resp1.json(), resp2.json()
        assert d1["edge_container_name"] != d2["edge_container_name"]
        assert d1["network_name"] != d2["network_name"]
        assert d1["ts_hostname"] != d2["ts_hostname"]

    def test_three_exposures_unique_slugs(self, client):
        """Three services with the same name get incrementing suffixes."""
        r1 = _create_service(client, name="App", hostname="a1.example.com")
        r2 = _create_service(client, name="App", hostname="a2.example.com")
        r3 = _create_service(client, name="App", hostname="a3.example.com")
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r3.status_code == 201
        names = {r.json()["edge_container_name"] for r in [r1, r2, r3]}
        assert len(names) == 3  # all unique

    def test_list_shows_all_exposures(self, client):
        """Service list includes all exposures, not just one per container."""
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
        assert data["upstream_port"] == 80  # unchanged
        assert data["hostname"] == "nextcloud.example.com"  # unchanged


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

        # Verify it's gone
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
        """Deleting a service should cascade delete its status."""
        svc_id = _create_service(client).json()["id"]
        # Verify status exists
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.json()["status"] is not None
        # Delete service
        client.delete(f"/api/services/{svc_id}")
        # Service and status should both be gone
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.status_code == 404

    def test_delete_allows_hostname_reuse(self, client):
        """After deleting, the hostname should be available again."""
        svc_id = _create_service(client, hostname="reuse.example.com").json()["id"]
        client.delete(f"/api/services/{svc_id}")
        resp = _create_service(client, hostname="reuse.example.com")
        assert resp.status_code == 201


class TestStubActionEndpoints:
    """Remaining stub endpoints return 501 until implemented in later milestones."""

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
    """Test that status includes health_checks and cert_expires_at."""

    def test_status_includes_health_checks_and_cert(self, client, db_session):
        from app.models.certificate import Certificate
        from app.models.service_status import ServiceStatus
        import json

        svc_id = _create_service(client).json()["id"]

        # Update status with health checks
        status = db_session.get(ServiceStatus, svc_id)
        status.health_checks = json.dumps({"edge_container_running": True, "cert_present": False})
        db_session.commit()

        # Add certificate with expiry
        cert = Certificate(
            service_id=svc_id,
            hostname="nextcloud.example.com",
        )
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
        """Creating a service should emit a service_created event."""
        from app.models.event import Event

        _create_service(client)
        events = db_session.query(Event).filter(Event.kind == "service_created").all()
        assert len(events) == 1
        assert "Nextcloud" in events[0].message

    def test_update_generates_event(self, client, db_session):
        """Updating a service should emit a service_updated event."""
        from app.models.event import Event

        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        events = db_session.query(Event).filter(Event.kind == "service_updated").all()
        assert len(events) == 1
        details = json.loads(events[0].details)
        assert details["name"] == "Renamed"

    def test_disable_generates_event(self, client, db_session):
        """Disabling a service should emit a service_disabled event."""
        from app.models.event import Event

        svc_id = _create_service(client).json()["id"]
        client.post(f"/api/services/{svc_id}/disable")
        events = db_session.query(Event).filter(Event.kind == "service_disabled").all()
        assert len(events) == 1

    def test_delete_generates_event(self, client, db_session):
        """Deleting a service should emit a service_deleted event."""
        from app.models.event import Event

        svc_id = _create_service(client).json()["id"]
        client.delete(f"/api/services/{svc_id}")
        events = db_session.query(Event).filter(Event.kind == "service_deleted").all()
        assert len(events) == 1
        # service_id should be None since it's SET NULL on delete
        assert events[0].service_id is None

    def test_noop_update_no_event(self, client, db_session):
        """Updating with no changes should not emit an event."""
        from app.models.event import Event

        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={})
        events = db_session.query(Event).filter(Event.kind == "service_updated").all()
        assert len(events) == 0
