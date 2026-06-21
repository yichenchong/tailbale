"""Tests for the reconciler engine."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler.reconciler import reconcile_service


def _create_service(db, **overrides):
    defaults = {
        "name": "TestApp", "upstream_container_id": "abc123",
        "upstream_container_name": "testapp", "upstream_scheme": "http",
        "upstream_port": 80, "hostname": "testapp.example.com",
        "base_domain": "example.com", "edge_container_name": "edge_testapp",
        "network_name": "edge_net_testapp", "ts_hostname": "edge-testapp",
    }
    defaults.update(overrides)
    svc = Service(**defaults)
    db.add(svc)
    db.flush()
    db.add(ServiceStatus(service_id=svc.id, phase="pending"))
    db.commit()
    return svc


# Patch at source modules since reconciler uses lazy imports
_P_SECRET = "app.secrets.read_secret"
_P_RENDER = "app.edge.config_renderer.render_caddyfile"
_P_WRITE = "app.edge.config_renderer.write_caddyfile"
_P_CERT = "app.certs.renewal_task.process_service_cert"
_P_NETWORK = "app.edge.network_manager.ensure_network"
_P_CREATE_EDGE = "app.edge.container_manager.create_edge_container"
_P_FIND_EDGE = "app.edge.container_manager._find_edge_container"
_P_START = "app.edge.container_manager.start_edge"
_P_TS_IP = "app.edge.container_manager.detect_tailscale_ip"
_P_RELOAD = "app.edge.container_manager.reload_caddy"
_P_HEALTH = "app.health.health_checker.run_health_checks"
_P_AGGREGATE = "app.health.health_checker.aggregate_status"
_P_DNS = "app.adapters.dns_reconciler.reconcile_dns"


class TestReconcileService:
    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_full_reconcile_new_service(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        svc = _create_service(db_session)

        mock_secret.return_value = "ts-key"
        mock_render.return_value = "caddyfile content"
        mock_find_edge.return_value = None  # no existing edge
        mock_create_edge.return_value = "container123"
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {"edge_container_running": True}
        mock_aggregate.return_value = "healthy"

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "healthy"
        assert result["tailscale_ip"] == "100.64.0.1"
        mock_network.assert_called_once()
        mock_create_edge.assert_called_once()
        mock_write.assert_called_once()

        # Check status was updated
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "healthy"
        assert status.tailscale_ip == "100.64.0.1"
        assert status.last_reconciled_at is not None

        # Check events were emitted
        events = db_session.query(Event).filter(Event.kind == "reconcile_completed").all()
        assert len(events) == 1

    def test_probe_retry_not_scheduled_when_critical_health_check_fails(
        self, db_session, tmp_data_dir
    ):
        svc = _create_service(db_session)
        edge = MagicMock()
        edge.id = "edge_id"
        edge.status = "running"
        checks = {
            "upstream_container_present": False,
            "upstream_network_connected": True,
            "edge_container_present": True,
            "edge_container_running": True,
            "tailscale_ready": True,
            "tailscale_ip_present": True,
            "cert_present": True,
            "caddy_config_present": True,
            "cert_not_expiring": True,
            "dns_record_present": True,
            "dns_matches_ip": True,
            "https_probe_ok": False,
        }

        with (
            patch(_P_SECRET, return_value="ts-key"),
            patch(_P_RENDER, return_value="caddyfile content"),
            patch(_P_WRITE),
            patch(_P_CERT),
            patch(_P_NETWORK),
            patch(_P_CREATE_EDGE),
            patch(_P_FIND_EDGE, return_value=edge),
            patch(_P_START),
            patch(_P_TS_IP, return_value="100.64.0.1"),
            patch(_P_RELOAD),
            patch(_P_AGGREGATE, return_value="error"),
            patch(_P_HEALTH, return_value=checks),
            patch("app.reconciler.probe_retry.schedule_probe_retry") as mock_schedule,
        ):
            result = reconcile_service(db_session, svc)

        assert result["phase"] == "error"
        mock_schedule.assert_not_called()

    @patch(_P_NETWORK)
    @patch(_P_CREATE_EDGE)
    @patch(_P_DNS)
    @patch(_P_SECRET)
    def test_disabled_service_is_not_converged(
        self, mock_secret, mock_dns, mock_create_edge, mock_network,
        db_session, tmp_data_dir,
    ):
        """A disabled service must never be brought back online by reconcile
        (manual trigger or sweep TOCTOU): no edge/network/DNS work, phase stays
        disabled."""
        mock_secret.return_value = "ts-key"
        svc = _create_service(db_session, enabled=False)

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "disabled"
        mock_network.assert_not_called()
        mock_create_edge.assert_not_called()
        mock_dns.assert_not_called()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "disabled"

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_updates_stale_upstream_container_id_after_restart(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        svc = _create_service(db_session, upstream_container_id="stale123", upstream_container_name="testapp")

        mock_secret.return_value = "ts-key"
        mock_render.return_value = "caddyfile content"
        mock_find_edge.return_value = None
        mock_create_edge.return_value = "container123"
        mock_network.return_value = ("net123", "fresh456")
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {"edge_container_running": True}
        mock_aggregate.return_value = "healthy"

        reconcile_service(db_session, svc)

        updated = db_session.get(Service, svc.id)
        assert updated is not None
        assert updated.upstream_container_id == "fresh456"

    @patch(_P_SECRET)
    def test_fails_without_ts_authkey(self, mock_secret, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        mock_secret.return_value = None  # no auth key

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert "auth key" in result["error"].lower()

        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "failed"

        events = db_session.query(Event).filter(Event.kind == "reconcile_failed").all()
        assert len(events) == 1

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_existing_edge_not_recreated(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        svc = _create_service(db_session)

        mock_secret.return_value = "ts-key"
        mock_render.return_value = "caddyfile content"

        # Edge already exists and running
        existing_edge = MagicMock()
        existing_edge.id = "existing_id"
        existing_edge.status = "running"
        mock_find_edge.return_value = existing_edge

        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "healthy"
        mock_create_edge.assert_not_called()  # Should not recreate
        mock_start.assert_not_called()  # Already running

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_starts_stopped_edge(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "config"

        stopped_edge = MagicMock()
        stopped_edge.id = "edge_id"
        stopped_edge.status = "exited"

        mock_find_edge.return_value = stopped_edge

        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        reconcile_service(db_session, svc)

        mock_start.assert_called_once()

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_TS_IP)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_skips_caddy_reload_when_config_unchanged(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge,
        mock_ts_ip, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"

        config_content = "existing config"
        mock_render.return_value = config_content

        # Write existing Caddyfile with same content
        generated_dir = Path(tmp_data_dir) / "generated" / svc.id
        generated_dir.mkdir(parents=True, exist_ok=True)
        (generated_dir / "Caddyfile").write_text(config_content)

        edge = MagicMock()
        edge.id = "edge_id"
        edge.status = "running"
        mock_find_edge.return_value = edge

        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        with patch(_P_RELOAD) as mock_reload:
            result = reconcile_service(db_session, svc)
            mock_reload.assert_not_called()
            assert result["caddy_reloaded"] is False

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_naive_expiring_cert_timestamp_triggers_renewal(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        from datetime import datetime, timedelta

        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "edge_id"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        cert_dir = Path(tmp_data_dir) / "certs" / svc.hostname
        cert_dir.mkdir(parents=True)
        (cert_dir / "fullchain.pem").write_text("cert")

        with patch(
            "app.certs.cert_manager.get_cert_expiry",
            return_value=datetime.now() + timedelta(days=10),
        ):
            result = reconcile_service(db_session, svc)

        assert result["phase"] == "healthy"
        mock_cert.assert_called_once()


    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_unparseable_existing_cert_triggers_renewal(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "edge_id"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        cert_dir = Path(tmp_data_dir) / "certs" / svc.hostname
        cert_dir.mkdir(parents=True)
        (cert_dir / "fullchain.pem").write_text("not a cert")

        with patch("app.certs.cert_manager.get_cert_expiry", return_value=None):
            result = reconcile_service(db_session, svc)

        assert result["phase"] == "healthy"
        mock_cert.assert_called_once()

    @patch(_P_NETWORK)
    @patch(_P_SECRET)
    def test_handles_network_failure(self, mock_secret, mock_network, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_network.side_effect = RuntimeError("Docker not available")

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert result["error"] is not None

    @patch(_P_DNS)
    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_dns_failure_does_not_block(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health, mock_dns,
        db_session, tmp_data_dir,
    ):
        from app.settings_store import set_setting

        svc = _create_service(db_session)

        mock_secret.side_effect = lambda name: {"tailscale_authkey": "ts-key", "cloudflare_token": "cf-tok"}.get(name)
        set_setting(db_session, "cf_zone_id", "zone1")

        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_dns.side_effect = RuntimeError("CF API down")
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        result = reconcile_service(db_session, svc)

        # Should still complete despite DNS failure
        assert result["phase"] == "healthy"


    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_caddy_reload_failure_marks_reconcile_failed(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "changed config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_reload.side_effect = RuntimeError("invalid caddy config")

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert "Caddy reload failed" in result["error"]
        mock_health.assert_not_called()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "failed"

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_failed_caddy_reload_is_retried_next_reconcile(
        self, mock_secret, mock_render, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # write_caddyfile is intentionally NOT mocked so the real Caddyfile is
        # written to disk. The bug: once the desired config is on disk, a naive
        # disk-vs-render diff reports "unchanged" and never retries a reload that
        # previously failed, leaving Caddy on stale config while reporting healthy.
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "v2 config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        # First reconcile: new config is written, but the reload fails.
        mock_reload.side_effect = RuntimeError("admin api connection refused")
        first = reconcile_service(db_session, svc)
        assert first["phase"] == "failed"
        assert "Caddy reload failed" in first["error"]

        caddyfile = Path(tmp_data_dir) / "generated" / svc.id / "Caddyfile"
        assert caddyfile.read_text(encoding="utf-8") == "v2 config"

        # Second reconcile: the on-disk config already equals desired, so the
        # disk diff is "unchanged" — yet the reload MUST still be retried because
        # the running Caddy never picked up the new config.
        mock_reload.reset_mock()
        mock_reload.side_effect = None
        mock_reload.return_value = "ok"
        second = reconcile_service(db_session, svc)

        assert second["phase"] == "healthy"
        mock_reload.assert_called_once()
        assert second["caddy_reloaded"] is True

    @patch(_P_SECRET)
    def test_marks_service_failed_after_locked_status_update(self, mock_secret, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        service_id = svc.id
        mock_secret.return_value = "ts-key"

        original_flush = db_session.flush
        raised = False

        def flush_with_lock(*args, **kwargs):
            nonlocal raised
            if not raised and (db_session.dirty or db_session.new):
                raised = True
                raise OperationalError(
                    "UPDATE service_status SET phase=? WHERE service_id=?",
                    ("validating", service_id),
                    Exception("database is locked"),
                )
            return original_flush(*args, **kwargs)

        db_session.flush = flush_with_lock
        try:
            result = reconcile_service(db_session, svc)
        finally:
            db_session.flush = original_flush

        assert result["phase"] == "failed"
        assert "database is locked" in result["error"]

        status = db_session.get(ServiceStatus, service_id)
        assert status is not None
        assert status.phase == "failed"
        assert "database is locked" in (status.message or "")

        events = db_session.query(Event).filter(Event.kind == "reconcile_failed").all()
        assert len(events) == 1

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK)
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_serializes_overlapping_reconciles(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        tmp_data_dir,
    ):
        db_path = tmp_data_dir / "reconcile-overlap.sqlite"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine)

        seed_db = TestSession()
        try:
            svc = _create_service(seed_db)
            service_id = svc.id
        finally:
            seed_db.close()

        mock_secret.return_value = "ts-key"
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "edge_id"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        active_calls = 0
        max_active_calls = 0
        call_lock = threading.Lock()

        def slow_network(*args, **kwargs):
            nonlocal active_calls, max_active_calls
            with call_lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
            try:
                time.sleep(0.1)
            finally:
                with call_lock:
                    active_calls -= 1

        mock_network.side_effect = slow_network

        errors: list[Exception] = []
        results: list[dict] = []

        def run_reconcile():
            db = TestSession()
            try:
                svc = db.get(Service, service_id)
                assert svc is not None
                results.append(reconcile_service(db, svc))
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close()

        first = threading.Thread(target=run_reconcile)
        second = threading.Thread(target=run_reconcile)
        first.start()
        second.start()
        first.join()
        second.join()

        engine.dispose()

        assert errors == []
        assert len(results) == 2
        assert max_active_calls == 1
        assert all(result["phase"] == "healthy" for result in results)


class TestProbeRetryScheduling:
    def test_deduplicates_active_retry_thread(self):
        from app.reconciler import probe_retry

        probe_retry._ACTIVE_RETRIES.clear()
        try:
            with patch.object(probe_retry.threading, "Thread") as mock_thread:
                probe_retry.schedule_probe_retry("svc_123")
                probe_retry.schedule_probe_retry("svc_123")

            mock_thread.assert_called_once()
            mock_thread.return_value.start.assert_called_once()
        finally:
            probe_retry._ACTIVE_RETRIES.clear()

    def test_probe_retry_does_not_overwrite_status_changed_during_probe(self, db_session):
        import app.database as database_module
        from app.reconciler import probe_retry

        svc = _create_service(db_session)
        TestSession = sessionmaker(bind=db_session.get_bind())

        def change_status_during_probe(*args, **kwargs):
            other_db = TestSession()
            try:
                status = other_db.get(ServiceStatus, svc.id)
                status.message = "reconcile in progress"
                other_db.commit()
            finally:
                other_db.close()
            return {"https_probe_ok": True}

        with (
            patch.object(database_module, "SessionLocal", TestSession),
            patch.object(probe_retry, "MAX_RETRIES", 1),
            patch.object(probe_retry, "_compute_delay", return_value=0),
            patch.object(probe_retry.time, "sleep"),
            patch("app.settings_store.get_runtime_paths", return_value={
                "generated_dir": "/tmp/generated",
                "certs_dir": "/tmp/certs",
            }),
            patch("app.health.health_checker.run_health_checks", side_effect=change_status_during_probe),
            patch("app.health.health_checker.aggregate_status", return_value="healthy"),
        ):
            probe_retry._probe_retry_loop(svc.id, None)

        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "pending"
        assert status.message == "reconcile in progress"

    def test_probe_retry_stops_before_sleep_for_disabled_service(self, db_session):
        from datetime import UTC, datetime, timedelta

        import app.database as database_module
        from app.reconciler import probe_retry

        svc = _create_service(db_session, enabled=False)
        status = db_session.get(ServiceStatus, svc.id)
        status.probe_retry_at = datetime.now(UTC) + timedelta(minutes=5)
        status.probe_retry_attempt = 3
        db_session.commit()
        TestSession = sessionmaker(bind=db_session.get_bind())

        with (
            patch.object(database_module, "SessionLocal", TestSession),
            patch.object(probe_retry.time, "sleep") as mock_sleep,
        ):
            probe_retry._probe_retry_loop(svc.id, None)

        mock_sleep.assert_not_called()
        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.probe_retry_at is None
        assert status.probe_retry_attempt is None

    def test_probe_retry_stops_before_sleep_when_already_healthy(self, db_session):
        from datetime import UTC, datetime, timedelta

        import app.database as database_module
        from app.reconciler import probe_retry

        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "healthy"
        status.probe_retry_at = datetime.now(UTC) + timedelta(minutes=5)
        status.probe_retry_attempt = 3
        db_session.commit()
        TestSession = sessionmaker(bind=db_session.get_bind())

        with (
            patch.object(database_module, "SessionLocal", TestSession),
            patch.object(probe_retry.time, "sleep") as mock_sleep,
        ):
            probe_retry._probe_retry_loop(svc.id, None)

        mock_sleep.assert_not_called()
        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.probe_retry_at is None
        assert status.probe_retry_attempt is None

    def test_allows_retry_thread_when_socket_path_changes(self):
        from app.reconciler import probe_retry

        probe_retry._ACTIVE_RETRIES.clear()
        try:
            with patch.object(probe_retry.threading, "Thread") as mock_thread:
                probe_retry.schedule_probe_retry("svc_123", "unix:///old.sock")
                probe_retry.schedule_probe_retry("svc_123", "unix:///new.sock")

            assert mock_thread.call_count == 2
            assert mock_thread.return_value.start.call_count == 2
        finally:
            probe_retry._ACTIVE_RETRIES.clear()
