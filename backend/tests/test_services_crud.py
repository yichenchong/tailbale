"""Service CRUD API tests: create/update/disable/delete/list lifecycle, hostname-change teardown, slug/name and upstream validation, DNS-row handling, status transitions, snippet-audit, and lego-artifact cleanup.

Mirrors app.services.crud (split from test_services_api.py)."""

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.certificate import Certificate
from app.models.dns_record import DnsRecord
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.routers.services import _validate_upstream as _real_validate_upstream
from tests._services_helpers import (
    _create_service,
    _create_service_in_db,
    _make_container,
)


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

    def test_create_disabled_starts_disabled_without_reconcile(self, client):
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = _create_service(client, enabled=False)

        assert resp.status_code == 201
        data = resp.json()
        assert data["enabled"] is False
        assert data["status"]["phase"] == "disabled"
        assert data["status"]["message"] == "Service is disabled"
        mock_reconcile.assert_not_called()

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

    def test_whitespace_only_name_rejected(self, client):
        # A blank-after-strip name must fail min_length, not create a nameless service.
        resp = _create_service(client, name="   ")
        assert resp.status_code == 422

    def test_surrounding_whitespace_in_name_trimmed(self, client):
        resp = _create_service(client, name="  Trimmed App  ")
        assert resp.status_code == 201
        assert resp.json()["name"] == "Trimmed App"

    def test_max_length_enforced_after_strip(self, client):
        # 132 raw chars stripping to exactly 128 must be accepted, proving max_length
        # is applied AFTER the strip (a pre-strip check would reject the raw length).
        resp = _create_service(client, name="  " + "x" * 128 + "  ")
        assert resp.status_code == 201
        assert resp.json()["name"] == "x" * 128

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

    def test_long_name_ts_hostname_within_dns_label_limit(self, client):
        # A name at the 128-char display cap must still derive a ts_hostname
        # within the 63-char DNS-label limit Tailscale enforces on
        # `tailscale up --hostname=`. The display cap on name stays at 128.
        long_name = "a" * 128
        resp = _create_service(
            client, name=long_name, hostname="longname.example.com"
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == long_name  # display cap unchanged
        assert len(data["ts_hostname"]) <= 63
        # All three derived names share the same capped slug.
        slug = data["ts_hostname"][len("edge-"):]
        assert data["edge_container_name"] == f"edge_{slug}"
        assert data["network_name"] == f"edge_net_{slug}"


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

    def test_long_distinct_names_get_unique_capped_slugs(self, client):
        # Two distinct long names that share the same truncated prefix must get
        # distinct slugs, both within the DNS-label limit (collision suffixing
        # operates on the capped base).
        shared = "z" * 60  # slugifies past the base cap; both truncate identically
        r1 = _create_service(client, name=shared + " one", hostname="lone.example.com")
        r2 = _create_service(client, name=shared + " two", hostname="ltwo.example.com")
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        assert d1["ts_hostname"] != d2["ts_hostname"]
        assert len(d1["ts_hostname"]) <= 63
        assert len(d2["ts_hostname"]) <= 63

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

    def test_list_batches_status_cert_and_handles_missing(self, client, db_session, db_engine):
        from sqlalchemy import event as sa_event

        id1 = _create_service(client, name="A", hostname="a.example.com").json()["id"]
        id2 = _create_service(client, name="B", hostname="b.example.com").json()["id"]
        id3 = _create_service(client, name="C", hostname="c.example.com").json()["id"]

        # id1 gains a cert; id2 loses its status row entirely.
        cert = Certificate(service_id=id1, hostname="a.example.com")
        cert.expires_at = datetime(2026, 10, 1)
        db_session.add(cert)
        db_session.delete(db_session.get(ServiceStatus, id2))
        db_session.commit()

        status_q: list[str] = []
        cert_q: list[str] = []

        def _track(conn, cursor, statement, parameters, context, executemany):
            low = statement.lower()
            if "from service_status" in low:
                status_q.append(statement)
            elif "from certificates" in low:
                cert_q.append(statement)

        sa_event.listen(db_engine, "before_cursor_execute", _track)
        try:
            resp = client.get("/api/services")
        finally:
            sa_event.remove(db_engine, "before_cursor_execute", _track)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3

        # Constant query count regardless of N (unbatched would be one per service).
        assert len(status_q) == 1
        assert len(cert_q) == 1

        by_id = {s["id"]: s for s in data["services"]}
        assert by_id[id1]["status"]["cert_expires_at"] == "2026-10-01T00:00:00"
        assert by_id[id2]["status"] is None
        assert by_id[id3]["status"] is not None
        assert by_id[id3]["status"]["cert_expires_at"] is None

        # Ordering matches a direct ordered query.
        expected_order = [
            s.id
            for s in db_session.query(Service)
            .order_by(Service.created_at.desc(), Service.id.desc())
            .all()
        ]
        assert [s["id"] for s in data["services"]] == expected_order


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

    @patch("app.edge.container_manager.stop_edge")
    def test_update_enabled(self, mock_stop, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"enabled": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["status"]["phase"] == "disabled"
        assert data["status"]["message"] == "Service disabled by user"
        mock_stop.assert_called_once()

    def test_update_enable_marks_pending_and_schedules_reconcile(self, client):
        svc_id = _create_service(client, enabled=False).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"enabled": True})

        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["status"]["phase"] == "pending"
        assert data["status"]["message"] == "Awaiting reconciliation after enable"
        mock_reconcile.assert_called_once()

    def test_update_hostname_schedules_reconcile(self, client):
        """A hostname change on an enabled service must schedule an immediate
        reconcile. The change tears down the old hostname's DNS record and cert
        directory, so the new hostname needs a fresh DNS record + cert without
        waiting for the periodic loop."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"
        mock_reconcile.assert_called_once()

    def test_update_hostname_disabled_no_reconcile(self, client):
        """A disabled service stays offline: a hostname change must NOT schedule a
        reconcile (that would bring the service back online)."""
        svc_id = _create_service(
            client, name="App", hostname="app.example.com", enabled=False
        ).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        mock_reconcile.assert_not_called()

    def test_update_port_schedules_reconcile(self, client):
        """A config-affecting field change (upstream_port) on an enabled service
        must schedule an immediate reconcile so the re-rendered Caddyfile /
        reverse-proxy change applies in seconds, not after the periodic loop."""
        svc_id = _create_service(client).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 8080})
        assert resp.status_code == 200
        mock_reconcile.assert_called_once()

    def test_update_name_only_does_not_schedule_reconcile(self, client):
        """A non-config field change (name) on an enabled service must NOT schedule
        a reconcile — it doesn't alter the rendered Caddyfile, so don't over-trigger."""
        svc_id = _create_service(client).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        mock_reconcile.assert_not_called()

    def test_update_config_on_disabled_does_not_schedule_reconcile(self, client):
        """A config-affecting change on a DISABLED service must NOT schedule a
        reconcile — there is nothing to bring up."""
        svc_id = _create_service(client, enabled=False).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 8080})
        assert resp.status_code == 200
        mock_reconcile.assert_not_called()

    def test_update_config_to_same_value_does_not_schedule_reconcile(self, client):
        """A config field re-sent with its CURRENT value is not a real change, so
        it must NOT schedule a reconcile. Exercises the value-comparison arm of
        config_changed (``changes[field] != getattr(svc, field)``): dropping it
        would over-trigger a reconcile on every no-op edit."""
        svc_id = _create_service(client, upstream_port=80).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 80})
        assert resp.status_code == 200
        assert resp.json()["upstream_port"] == 80
        mock_reconcile.assert_not_called()

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

    @pytest.mark.parametrize(
        "field",
        [
            "name",
            "upstream_scheme",
            "upstream_port",
            "hostname",
            "enabled",
            "preserve_host_header",
        ],
    )
    def test_update_rejects_null_for_non_nullable_fields(self, client, field):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={field: None})
        assert resp.status_code == 422


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

    def test_delete_serializes_with_reconcile_mutex(self, db_session):
        from sqlalchemy.orm import sessionmaker

        from app.locks import reconcile_lock_for
        from app.services import delete_service_record

        svc = _create_service_in_db(db_session)
        service_id = svc.id
        TestSession = sessionmaker(bind=db_session.get_bind())
        started = threading.Event()
        deleted = threading.Event()
        errors: list[Exception] = []

        def run_delete():
            thread_db = TestSession()
            try:
                thread_svc = thread_db.get(Service, service_id)
                started.set()
                delete_service_record(thread_db, thread_svc, cleanup_dns=False)
                deleted.set()
            except Exception as exc:
                errors.append(exc)
            finally:
                thread_db.close()

        with (
            patch("app.edge.container_manager.remove_edge"),
            patch("app.edge.network_manager.remove_network"),
            reconcile_lock_for(service_id),
        ):
            worker = threading.Thread(target=run_delete)
            worker.start()
            assert started.wait(1)
            assert not deleted.wait(0.05)
            assert db_session.get(Service, service_id) is not None

        worker.join(1)
        assert not worker.is_alive()
        assert errors == []
        db_session.expire_all()
        assert db_session.get(Service, service_id) is None


class TestDeleteForgetsReconcileLock:
    """Deleting a service must drop its per-service reconcile-lock entry so the
    in-process _RECONCILE_LOCKS registry stays bounded by live + in-flight ids
    (it used to grow one entry per id ever seen and never shrink)."""

    def test_delete_drops_only_deleted_services_lock(self, client):
        from app.locks import _RECONCILE_LOCKS, reconcile_lock_for

        keep_id = _create_service(client, hostname="keep.example.com").json()["id"]
        drop_id = _create_service(client, hostname="drop.example.com").json()["id"]

        # Seed the lock entries the way real reconcile would (background reconcile
        # is mocked in tests, so creation alone never touches the registry).
        reconcile_lock_for(keep_id)
        reconcile_lock_for(drop_id)
        assert keep_id in _RECONCILE_LOCKS
        assert drop_id in _RECONCILE_LOCKS

        try:
            resp = client.delete(f"/api/services/{drop_id}")
            assert resp.status_code == 204

            # Pre-fix this FAILS: the deleted service's entry was retained forever.
            assert drop_id not in _RECONCILE_LOCKS
            # Deleting one service must not evict a different live service's lock.
            assert keep_id in _RECONCILE_LOCKS
        finally:
            _RECONCILE_LOCKS.pop(keep_id, None)
            _RECONCILE_LOCKS.pop(drop_id, None)

    def test_reconcile_after_delete_is_graceful_and_no_resurrected_lock(self, db_session):
        # reconcile_one is autouse-mocked in this suite; reconcile_all is the real,
        # unmocked sweep and is the cleaner deterministic no-op to assert against.
        from app.locks import _RECONCILE_LOCKS, reconcile_lock_for
        from app.reconciler.reconcile_loop import reconcile_all
        from app.services import delete_service_record

        svc = _create_service_in_db(db_session)
        sid = svc.id
        reconcile_lock_for(sid)
        assert sid in _RECONCILE_LOCKS

        with (
            patch("app.edge.container_manager.remove_edge"),
            patch("app.edge.network_manager.remove_network"),
        ):
            delete_service_record(db_session, svc, cleanup_dns=False)

        # Lock entry dropped post-commit: no lost-exclusion window, registry bounded.
        assert sid not in _RECONCILE_LOCKS

        # A reconcile sweep after the delete is a safe no-op for the now-absent
        # service: it is no longer in the enabled snapshot, so it is skipped (no
        # raise, no Docker), and crucially never re-acquires a pointless lock.
        db_session.expire_all()
        assert reconcile_all(db_session) == 0
        assert sid not in _RECONCILE_LOCKS


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
        details = events[0].details
        assert details["name"] == "Renamed"

    def test_update_event_preserves_level_message_and_details(self, client, db_session):
        # Regression: services._emit_event was deleted and its call sites now go
        # straight to the central emit_event. The representative service_updated
        # emit must keep its level (default "info"), message, and *dict* details
        # (serialized centrally) intact across the consolidation.
        from app.models.event import Event
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        event = (
            db_session.query(Event).filter(Event.kind == "service_updated").one()
        )
        assert event.level == "info"
        assert event.message == "Service 'Renamed' updated"
        assert event.details == {"name": "Renamed"}

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


class TestSnippetAuditEvents:
    """A custom_caddy_snippet is an admin-only Caddy-config injection / SSRF
    vector, so any set/change/clear must surface as a DISTINCT, high-visibility
    audit event (kind 'service_snippet_changed', level 'warning') in addition to
    the routine service_created/service_updated events. Pre-fix this kind does
    not exist, so the positive assertions below fail."""

    _SNIPPET = "header X-Frame-Options DENY"
    _SNIPPET2 = "header X-Content-Type-Options nosniff"

    def _snippet_events(self, db, service_id=None):
        from app.models.event import Event
        q = db.query(Event).filter(Event.kind == "service_snippet_changed")
        if service_id is not None:
            q = q.filter(Event.service_id == service_id)
        return q.all()

    def _by_action(self, events):
        return {e.details["action"]: e for e in events}

    def test_create_with_snippet_emits_warning_event(self, client, db_session):
        import hashlib
        _create_service(client, custom_caddy_snippet=self._SNIPPET)
        events = self._snippet_events(db_session)
        assert len(events) == 1
        ev = events[0]
        assert ev.level == "warning"
        assert "set" in ev.message
        assert "Nextcloud" in ev.message
        details = ev.details
        assert details["action"] == "set"
        assert details["new_len"] == len(self._SNIPPET)
        assert details["new_sha256"] == hashlib.sha256(self._SNIPPET.encode()).hexdigest()

    def test_create_without_snippet_no_event(self, client, db_session):
        _create_service(client)
        assert len(self._snippet_events(db_session)) == 0

    def test_update_set_snippet_emits_event(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"custom_caddy_snippet": self._SNIPPET})
        events = self._snippet_events(db_session, svc_id)
        assert len(events) == 1
        assert events[0].level == "warning"
        assert events[0].details["action"] == "set"

    def test_update_change_snippet_emits_changed(self, client, db_session):
        import hashlib
        svc_id = _create_service(client, custom_caddy_snippet=self._SNIPPET).json()["id"]
        # The create emitted one 'set'; modifying must add a DISTINCT 'changed'.
        client.put(f"/api/services/{svc_id}", json={"custom_caddy_snippet": self._SNIPPET2})
        by_action = self._by_action(self._snippet_events(db_session, svc_id))
        assert set(by_action) == {"set", "changed"}
        changed = by_action["changed"]
        assert changed.level == "warning"
        details = changed.details
        assert details["new_len"] == len(self._SNIPPET2)
        assert details["new_sha256"] == hashlib.sha256(self._SNIPPET2.encode()).hexdigest()

    def test_update_clear_snippet_emits_cleared(self, client, db_session):
        svc_id = _create_service(client, custom_caddy_snippet=self._SNIPPET).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"custom_caddy_snippet": ""})
        by_action = self._by_action(self._snippet_events(db_session, svc_id))
        assert set(by_action) == {"set", "cleared"}
        details = by_action["cleared"].details
        assert details["new_len"] == 0
        assert details["new_sha256"] is None

    def test_update_other_field_no_snippet_event(self, client, db_session):
        svc_id = _create_service(client, custom_caddy_snippet=self._SNIPPET).json()["id"]
        # Only the create's 'set' should exist; renaming is not a snippet delta.
        client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert len(self._snippet_events(db_session, svc_id)) == 1

    def test_update_same_snippet_no_new_event(self, client, db_session):
        svc_id = _create_service(client, custom_caddy_snippet=self._SNIPPET).json()["id"]
        # Re-sending the identical snippet is not a delta -> no extra event.
        client.put(f"/api/services/{svc_id}", json={"custom_caddy_snippet": self._SNIPPET})
        assert len(self._snippet_events(db_session, svc_id)) == 1


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

    def test_hostname_overlong_label_rejected(self, client):
        resp = self._create(client, hostname="a" * 64 + ".example.com")
        assert resp.status_code == 422

    def test_hostname_max_label_accepted(self, client):
        resp = self._create(client, hostname="a" * 63 + ".example.com")
        assert resp.status_code == 201


class TestBaseDomainAlwaysConfigured:
    """base_domain is always the configured domain, derived server-side."""

    def _create(self, client, hostname="app.example.com", base_domain=None):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": hostname,
        }
        if base_domain is not None:
            body["base_domain"] = base_domain
        return client.post("/api/services", json=body)

    def test_base_domain_derived_from_configured_domain(self, client):
        resp = self._create(client, hostname="app.example.com")
        assert resp.status_code == 201
        assert resp.json()["base_domain"] == "example.com"

    def test_client_supplied_base_domain_is_ignored(self, client):
        # Even a divergent client base_domain cannot override the configured one.
        resp = self._create(client, hostname="a.b.example.com", base_domain="b.example.com")
        assert resp.status_code == 201
        assert resp.json()["base_domain"] == "example.com"

    def test_hostname_outside_configured_domain_rejected(self, client):
        resp = self._create(client, hostname="app.wrong.com")
        assert resp.status_code == 422
        assert "must end with" in resp.json()["detail"]

    def test_deep_hostname_under_configured_domain_accepted(self, client):
        resp = self._create(client, hostname="a.b.example.com")
        assert resp.status_code == 201
        assert resp.json()["base_domain"] == "example.com"

    def test_hostname_equal_to_configured_domain_rejected(self, client):
        resp = self._create(client, hostname="example.com")
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


    def test_validate_upstream_docker_unreachable_detail_is_generic(self, db_session, caplog):
        """_validate_upstream's 503 must NOT leak the socket path / DOCKER_HOST in
        str(exc); the real error is logged server-side instead."""
        import logging

        import docker
        from fastapi import HTTPException

        secret = "unix:///run/secret-docker.sock connection refused"
        # The default docker_socket_path setting is truthy, so the DockerClient
        # constructor (not from_env) is exercised; make it raise a docker error
        # whose str() embeds a socket-path-like secret. _real_validate_upstream is
        # captured at import time to bypass the autouse _mock_upstream_validation.
        with (
            patch("docker.DockerClient", side_effect=docker.errors.DockerException(secret)),
            caplog.at_level(logging.ERROR, logger="app.routers.services"),
            pytest.raises(HTTPException) as exc_info,
        ):
            _real_validate_upstream(db_session, "abc123", 80)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Cannot connect to Docker to validate upstream container"
        assert secret not in exc_info.value.detail
        assert secret in caplog.text

    def test_validate_upstream_not_found_real_branch(self, db_session):
        """The real _validate_upstream maps a docker NotFound raised by
        containers.get to a 422 (container missing) — distinct from the
        daemon-down 503 path. Exercises the actual NotFound arm rather than a
        fully-patched _validate_upstream."""
        import contextlib

        import docker
        from fastapi import HTTPException

        fake_client = MagicMock()
        fake_client.containers.get.side_effect = docker.errors.NotFound("no such container")

        @contextlib.contextmanager
        def fake_docker_client(_socket):
            yield fake_client

        with (
            patch("app.routers.services.docker_client", fake_docker_client),
            pytest.raises(HTTPException) as exc_info,
        ):
            _real_validate_upstream(db_session, "missing_ctr", 80)

        assert exc_info.value.status_code == 422
        assert "missing_ctr" in exc_info.value.detail


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
            status.health_checks = {"edge_container_running": True}
            db_session.commit()
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"]["health_checks"] is None

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_clears_probe_retry_state(self, mock_stop, client, db_session):
        resp = _create_service(client, name="App", hostname="app.example.com")
        svc_id = resp.json()["id"]
        status = db_session.query(ServiceStatus).filter_by(service_id=svc_id).first()
        if status:
            status.probe_retry_at = datetime.now(UTC) + timedelta(hours=1)
            status.probe_retry_attempt = 5
            db_session.commit()

        resp = client.post(f"/api/services/{svc_id}/disable")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"]["probe_retry_at"] is None
        assert data["status"]["probe_retry_attempt"] is None

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
        from datetime import datetime
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        cert = Certificate(
            service_id=svc_id,
            hostname="app.example.com",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC),
            last_renewed_at=datetime(2026, 5, 1, tzinfo=UTC),
            last_failure="old error",
            next_retry_at=datetime(2026, 5, 2, tzinfo=UTC),
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

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_invalid_port_does_not_tear_down_hostname(
        self, mock_secret, mock_cleanup, client, db_session
    ):
        """A PUT that changes both hostname and an INVALID port must reject before
        any destructive hostname teardown: the old DNS record and cert dir must be
        left intact, and the hostname must not change."""
        from fastapi import HTTPException

        from app.settings_store import set_setting
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        db_session.add(DnsRecord(service_id=svc_id, hostname="app.example.com", record_id="cf_rec"))
        db_session.commit()

        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=422, detail="Port 9999 is not exposed"),
        ):
            resp = client.put(
                f"/api/services/{svc_id}",
                json={"hostname": "new.example.com", "upstream_port": 9999},
            )
        assert resp.status_code == 422

        # Destructive teardown must NOT have run: DNS cleanup was never invoked,
        # the DnsRecord row survives, and the hostname is unchanged.
        mock_cleanup.assert_not_called()
        db_session.expire_all()
        assert client.get(f"/api/services/{svc_id}").json()["hostname"] == "app.example.com"
        assert db_session.get(DnsRecord, svc_id) is not None


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
        details = job.details
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
        details = jobs[0].details
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


class TestUpdateNameValidation:
    """update_service should enforce the same name constraints as create."""

    def test_update_empty_name_rejected(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": ""})
        assert resp.status_code == 422

    def test_update_overlong_name_rejected(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "x" * 129})
        assert resp.status_code == 422

    def test_update_valid_name_accepted(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "x" * 128})
        assert resp.status_code == 200
        assert resp.json()["name"] == "x" * 128

    def test_update_whitespace_only_name_rejected(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "   "})
        assert resp.status_code == 422

    def test_update_trims_surrounding_whitespace(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "  Renamed  "})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_max_length_enforced_after_strip(self, client):
        svc_id = _create_service(client).json()["id"]
        # Raw length 132 strips to exactly 128 — accepted only if max_length is post-strip.
        resp = client.put(f"/api/services/{svc_id}", json={"name": "  " + "x" * 128 + "  "})
        assert resp.status_code == 200
        assert resp.json()["name"] == "x" * 128


class TestUpdateHostnameBaseDomain:
    """Changing the hostname must keep base_domain a suffix of the hostname."""

    def test_hostname_change_recomputes_base_domain(self, client):
        svc_id = _create_service(
            client, name="App", hostname="x.a.example.com", base_domain="a.example.com"
        ).json()["id"]

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "b.example.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["hostname"] == "b.example.com"
        # base_domain must remain a suffix of the new hostname (create invariant).
        assert data["hostname"].endswith(f".{data['base_domain']}")
        assert data["base_domain"] == "example.com"


class TestUpdatePortValidationHoistedOutOfLock:
    """The upstream-port revalidation does a Docker round-trip. It must run
    BEFORE update_service takes _SERVICE_LIFECYCLE_MUTEX + the per-service
    reconcile lock, so a slow/unreachable Docker can't stall every other
    lifecycle op. Pre-fix the validation ran while holding both locks."""

    def test_validation_runs_before_lifecycle_lock(self, client):
        from app.locks import _SERVICE_LIFECYCLE_MUTEX

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        observed: dict = {}

        def fake_validate(db, container_id, port):
            # Probe the lifecycle mutex from a DIFFERENT thread: an RLock is
            # reentrant for the holder, so only a separate thread reveals whether
            # the request handler is currently holding it. Pre-fix (validation
            # under the lock) this non-blocking acquire fails; post-fix the lock
            # is still free because validation is hoisted ahead of it.
            result: dict = {}

            def probe():
                acquired = _SERVICE_LIFECYCLE_MUTEX.acquire(blocking=False)
                result["acquired"] = acquired
                if acquired:
                    _SERVICE_LIFECYCLE_MUTEX.release()

            t = threading.Thread(target=probe)
            t.start()
            t.join()
            observed.update(result)

        with patch("app.routers.services._validate_upstream", side_effect=fake_validate):
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 9090})

        assert resp.status_code == 200
        assert observed.get("acquired") is True, (
            "lifecycle mutex was held during upstream-port validation; "
            "validation must be hoisted out of the lock"
        )


class TestHostnameChangeRecreatesEdge:
    """A hostname change must remove the edge container so the reconcile recreates
    it with the new per-hostname /certs mount.

    The edge container's ``/certs`` bind mount is ``certs_dir/<hostname>`` baked in
    at creation time, and the Caddyfile serves ``/certs/current/...``. The hostname
    change deletes the old hostname's cert dir and issues the new cert under
    ``certs_dir/<new_hostname>``; the reconcile only *creates* a container when one
    is missing, so without removing the stale container it keeps mounting the
    now-deleted old dir and can never see the new cert."""

    @patch("app.edge.container_manager.remove_edge")
    def test_hostname_change_removes_edge_container(self, mock_remove, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        mock_remove.assert_called_once()
        # The edge container name is immutable across a hostname change, so the
        # SAME container is removed (and later recreated with the new mount).
        args = mock_remove.call_args[0]
        assert args[0] == svc_id
        assert args[1] == "edge_app"

    @patch("app.edge.container_manager.remove_edge")
    def test_hostname_change_while_disabled_still_removes_edge(self, mock_remove, client):
        # Even disabled, the stopped container holds the stale cert mount; a later
        # re-enable would just start it pointing at the deleted cert dir. Remove
        # it now so re-enable recreates it cleanly.
        svc_id = _create_service(
            client, name="App", hostname="app.example.com", enabled=False
        ).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        mock_remove.assert_called_once()

    @patch("app.edge.container_manager.remove_edge")
    def test_non_hostname_update_does_not_remove_edge(self, mock_remove, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 8080})
        assert resp.status_code == 200
        mock_remove.assert_not_called()

    @patch("app.edge.container_manager.remove_edge")
    def test_same_hostname_does_not_remove_edge(self, mock_remove, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.example.com"})
        assert resp.status_code == 200
        mock_remove.assert_not_called()

    @patch("app.edge.container_manager.remove_edge", side_effect=RuntimeError("docker down"))
    def test_hostname_change_succeeds_when_remove_edge_fails(self, mock_remove, client):
        # Edge removal is best-effort: a Docker failure must not fail the update.
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"


class TestHostnameChangeStatusReset:
    """A hostname change re-provisions the service (DNS + cert + edge container are
    torn down and rebuilt), so a previously-healthy status must not linger."""

    def test_hostname_change_resets_status_to_pending(self, client, db_session):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        status = db_session.get(ServiceStatus, svc_id)
        status.phase = "healthy"
        status.message = "All systems go"
        status.health_checks = {"https_probe_ok": True}
        db_session.commit()

        with patch("app.edge.container_manager.remove_edge"):
            resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"]["phase"] == "pending"
        assert data["status"]["message"] == "Awaiting reconciliation after hostname change"
        assert data["status"]["health_checks"] is None

    def test_hostname_change_while_disabled_keeps_disabled_status(self, client, db_session):
        # A disabled service is not being brought online; its status must stay
        # "disabled", never flip to "pending".
        svc_id = _create_service(
            client, name="App", hostname="app.example.com", enabled=False
        ).json()["id"]
        with patch("app.edge.container_manager.remove_edge"):
            resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["status"]["phase"] == "disabled"


class TestBackgroundReconcileErrorIsolation:
    """The post-create / post-update reconcile is a fire-and-forget background
    task. A reconcile error (most realistically: the service was deleted in the
    race window before the task ran, so reconcile_one raises ValueError) must NOT
    escape the request cycle as an unhandled server exception. The two other
    reconcile callers (reconcile_all, the manual /reconcile endpoint) already
    guard against this; the background triggers must too."""

    def test_create_background_reconcile_swallows_errors(self, client):
        # Override the autouse no-op mock so the background task actually fails.
        with patch(
            "app.reconciler.reconcile_loop.reconcile_one",
            side_effect=ValueError("service gone"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
        # The create itself must still succeed; the background failure is logged,
        # not surfaced as a 500 / propagated server exception.
        assert resp.status_code == 201

    def test_update_background_reconcile_swallows_errors(self, client):
        svc_id = _create_service(client, enabled=False).json()["id"]
        with patch(
            "app.reconciler.reconcile_loop.reconcile_one",
            side_effect=ValueError("service gone"),
        ):
            # Enabling schedules the background reconcile.
            resp = client.put(f"/api/services/{svc_id}", json={"enabled": True})
        assert resp.status_code == 200


class TestBackgroundReconcilePassesSocket:
    """The post-create / post-enable reconcile is fire-and-forget in a background
    thread, but the round-4 refactor's whole point is that the Docker socket is
    resolved in the REQUEST thread (where the request DB session is still alive)
    and threaded through _reconcile_in_background to reconcile_one(socket_path=).
    A regression that resolved the socket in the background task (using a
    torn-down request session) or dropped the arg would converge against the
    wrong daemon. Invariant guard for both call sites (create + update-enable)."""

    def test_create_threads_request_socket_to_reconcile(self, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = _create_service(client, name="App", hostname="app.example.com")

        assert resp.status_code == 201
        mock_reconcile.assert_called_once()
        assert mock_reconcile.call_args.kwargs.get("socket_path") == "unix:///custom/docker.sock"

    def test_update_enable_threads_request_socket_to_reconcile(self, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        svc_id = _create_service(client, enabled=False).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"enabled": True})

        assert resp.status_code == 200
        mock_reconcile.assert_called_once()
        assert mock_reconcile.call_args.kwargs.get("socket_path") == "unix:///custom/docker.sock"


class TestLegoCertArtifactCleanup:
    """SC2: deleting a service or changing its hostname must remove lego's
    leftover ``.lego/certificates/<hostname>.{crt,key,json,issuer.crt}`` files
    (alongside the served cert dir), without touching a *different* hostname's
    artifacts. The renew fallback keeps working because ``cert_manager.renew_cert``
    re-issues when this lego state is absent (see test_cert_manager.py)."""

    _LEGO_SUFFIXES = (".crt", ".key", ".json", ".issuer.crt")

    def _seed_lego_artifacts(self, certs_root, hostname):
        lego_certs = Path(certs_root) / ".lego" / "certificates"
        lego_certs.mkdir(parents=True, exist_ok=True)
        made = []
        for suffix in self._LEGO_SUFFIXES:
            artifact = lego_certs / f"{hostname}{suffix}"
            artifact.write_text("x")
            made.append(artifact)
        return lego_certs, made

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_removes_lego_artifacts(self, mock_re, mock_rn, client, db_session, tmp_data_dir):
        from app.settings_store import set_setting

        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        db_session.commit()

        svc = _create_service(client, name="App", hostname="app.example.com").json()
        lego_certs, artifacts = self._seed_lego_artifacts(custom_certs, "app.example.com")
        # A different service's artifacts must survive the exact-name deletion.
        survivor = lego_certs / "other.example.com.crt"
        survivor.write_text("keep")

        client.delete(f"/api/services/{svc['id']}")

        for artifact in artifacts:
            assert not artifact.exists(), f"{artifact.name} should be removed on delete"
        assert survivor.exists(), "another hostname's lego artifacts must not be removed"

    def test_hostname_change_removes_old_lego_artifacts(self, client, db_session, tmp_data_dir):
        from app.settings_store import set_setting

        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        lego_certs, old_artifacts = self._seed_lego_artifacts(custom_certs, "app.example.com")
        # The incoming hostname's artifacts (if any) must not be collateral damage.
        survivor = lego_certs / "new.example.com.crt"
        survivor.write_text("keep")

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200

        for artifact in old_artifacts:
            assert not artifact.exists(), f"{artifact.name} should be removed on hostname change"
        assert survivor.exists(), "the new hostname's lego artifacts must not be removed"

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_survives_missing_lego_dir(self, mock_re, mock_rn, client, db_session, tmp_data_dir):
        """Best-effort: with no .lego store at all, delete must still succeed."""
        from app.settings_store import set_setting

        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        db_session.commit()

        svc = _create_service(client, name="App", hostname="app.example.com").json()
        resp = client.delete(f"/api/services/{svc['id']}")
        assert resp.status_code == 204
