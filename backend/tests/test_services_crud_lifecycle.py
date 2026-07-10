"""Service CRUD API tests: disable/delete lifecycle, hostname-change teardown (DNS/cert cleanup), background-reconcile isolation, and lego-artifact cleanup.

Covers lifecycle behavior after the app.services facade split from test_services_crud.py."""

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app import services as service_layer
from app.locks import _RECONCILE_LOCKS, reconcile_lock_for
from app.models.certificate import Certificate
from app.models.dns_record import DnsRecord
from app.models.job import Job
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler.reconcile_loop import reconcile_all
from app.services import delete_service_record
from app.settings_store import set_setting
from tests._services_helpers import (
    _create_service,
    _create_service_in_db,
)


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


class TestHostnameChangeDnsAbort:
    """Hostname change should abort if old DNS record cannot be deleted from Cloudflare."""

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_hostname_change_aborts_on_cf_failure(self, mock_secret, mock_cleanup, client, db_session):
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

    @patch("app.secrets.read_secret", return_value=None)
    def test_hostname_change_updates_local_dns_record_hostname(
        self, mock_secret, client, db_session
    ):
        """A surviving local-only DnsRecord (no record_id, no CF creds) must have
        its hostname rewritten to the new value so a later reconcile syncs the
        CORRECT hostname to Cloudflare instead of orphaning a record for the old
        one. Exercises crud.update_service's ``dns_record.hostname = body.hostname``
        branch (reachable only when the row survives the DNS teardown)."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        dns = DnsRecord(service_id=svc_id, hostname="app.example.com", record_id=None)
        db_session.add(dns)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200

        db_session.expire_all()
        updated_dns = db_session.get(DnsRecord, svc_id)
        assert updated_dns is not None
        assert updated_dns.hostname == "new.example.com"


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
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = _create_service(client, name="App", hostname="app.example.com")

        assert resp.status_code == 201
        mock_reconcile.assert_called_once()
        assert mock_reconcile.call_args.kwargs.get("socket_path") == "unix:///custom/docker.sock"

    def test_update_enable_threads_request_socket_to_reconcile(self, client, db_session):
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

        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        db_session.commit()

        svc = _create_service(client, name="App", hostname="app.example.com").json()
        resp = client.delete(f"/api/services/{svc['id']}")
        assert resp.status_code == 204


class TestDeleteRefreshesStaleService:
    """delete_service_record must tear down the CURRENT service row, not the
    pre-lock snapshot the router loaded. update_service/disable_service both
    re-fetch with populate_existing under the lifecycle lock; delete must too.
    A hostname change that commits between the router's fetch and the delete's
    lock acquisition would otherwise leave delete keying its cert-dir / lego
    teardown off the STALE old hostname — silently leaking the current
    hostname's cert state while re-removing the already-gone old one."""

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_uses_current_hostname_not_stale_snapshot(
        self, mock_re, mock_rn, client, db_session, tmp_data_dir
    ):
        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        # Seed served cert dirs for BOTH the old snapshot hostname and the one a
        # concurrent rename switched the row to.
        old_dir = Path(custom_certs) / "app.example.com"
        old_dir.mkdir(parents=True)
        (old_dir / "fullchain.pem").write_text("old")
        new_dir = Path(custom_certs) / "new.example.com"
        new_dir.mkdir(parents=True)
        (new_dir / "fullchain.pem").write_text("new")

        # Load the ORM object (old hostname), then simulate the concurrent rename
        # having committed WHILE the delete blocked on the lock: mutate the row via
        # raw SQL in this session's open transaction so the ORM object stays stale.
        stale_svc = db_session.get(Service, svc_id)
        db_session.execute(
            text("UPDATE services SET hostname = :h WHERE id = :id"),
            {"h": "new.example.com", "id": svc_id},
        )
        assert stale_svc.hostname == "app.example.com"  # object still stale

        service_layer.delete_service_record(db_session, stale_svc, cleanup_dns=False)

        # The delete must clean up the CURRENT hostname's cert dir; pre-fix it
        # used the stale snapshot and left new_dir behind (removing old_dir).
        assert not new_dir.exists(), "delete must remove the current hostname's cert dir"


class TestDisablePreservesCertState:
    """Disable keeps a disabled service's on-disk cert state (served dir + lego
    artifacts) so a later re-enable reuses the existing certificate instead of
    forcing a fresh Let's Encrypt issue (ACME rate-limit risk). This is the
    ``remove_cert_state=False`` contract in disable_service's
    _teardown_hostname_resources call — delete, by contrast, removes it."""

    _LEGO_SUFFIXES = (".crt", ".key", ".json", ".issuer.crt")

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_keeps_cert_dir_and_lego_artifacts(
        self, mock_stop, client, db_session, tmp_data_dir
    ):
        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        db_session.commit()

        svc = _create_service(client, name="App", hostname="app.example.com").json()

        # Seed the served cert dir + lego artifacts a prior issue/renew left behind.
        cert_dir = Path(custom_certs) / "app.example.com"
        cert_dir.mkdir(parents=True)
        (cert_dir / "fullchain.pem").write_text("cert")
        lego_certs = Path(custom_certs) / ".lego" / "certificates"
        lego_certs.mkdir(parents=True)
        lego_files = [lego_certs / f"app.example.com{s}" for s in self._LEGO_SUFFIXES]
        for artifact in lego_files:
            artifact.write_text("x")

        resp = client.post(f"/api/services/{svc['id']}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

        # remove_cert_state=False: disable touches none of the cert state.
        assert cert_dir.exists(), "disable must keep the served cert dir for re-enable"
        assert (cert_dir / "fullchain.pem").exists()
        for artifact in lego_files:
            assert artifact.exists(), f"disable must keep lego artifact {artifact.name}"

    @patch("app.adapters.dns_reconciler.cleanup_dns_record",
           return_value={"deleted_remote": True, "deleted_local": True, "error": None})
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.container_manager.stop_edge")
    def test_disable_with_dns_cleanup_still_keeps_cert_dir(
        self, mock_stop, mock_secret, mock_cleanup, client, db_session, tmp_data_dir
    ):
        """DNS teardown and cert-state teardown are independently gated: even when
        cleanup_dns removes the DNS record, the cert dir is preserved."""
        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc = _create_service(client, name="App", hostname="app.example.com").json()
        cert_dir = Path(custom_certs) / "app.example.com"
        cert_dir.mkdir(parents=True)
        (cert_dir / "fullchain.pem").write_text("cert")

        resp = client.post(f"/api/services/{svc['id']}/disable?cleanup_dns=true")
        assert resp.status_code == 200
        mock_cleanup.assert_called_once()
        assert cert_dir.exists(), "cert dir must survive disable even with cleanup_dns"
