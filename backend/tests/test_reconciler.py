"""Tests for the reconciler engine."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
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


# Patch at source modules: reconciler imports them via the module-reference
# pattern (e.g. ``secrets.read_secret``), so the attribute resolves on the source
# module at call time and patching the source still takes effect.
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

    def test_probe_retry_schedule_failure_does_not_fail_reconcile(
        self, db_session, tmp_data_dir
    ):
        # Scheduling the background probe retry is best-effort: the periodic
        # reconcile sweep re-runs health checks regardless. If spawning the
        # helper thread raises (e.g. the process is out of threads under load),
        # an otherwise-successful reconcile that merely ended in "warning" must
        # NOT be flipped to "failed", and no reconcile_failed event is emitted.
        # Pre-fix the raise propagated into the broad except handler, which
        # rolled back and overwrote the committed "warning" with "failed".
        svc = _create_service(db_session)
        edge = MagicMock()
        edge.id = "edge_id"
        edge.status = "running"
        # Every CRITICAL check passes; only the HTTPS probe fails -> "warning",
        # so the reconciler reaches the probe-retry scheduling branch.
        checks = {
            "upstream_container_present": True,
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
            patch(_P_AGGREGATE, return_value="warning"),
            patch(_P_HEALTH, return_value=checks),
            patch(
                "app.reconciler.probe_retry.schedule_probe_retry",
                side_effect=RuntimeError("can't start new thread"),
            ) as mock_schedule,
        ):
            result = reconcile_service(db_session, svc)

        # The schedule WAS attempted, but its failure must not corrupt the
        # reconcile outcome.
        mock_schedule.assert_called_once()
        assert result["phase"] == "warning"
        assert result["error"] is None

        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "warning"
        # No misleading failure event; the success event still stands.
        failed = db_session.query(Event).filter(Event.kind == "reconcile_failed").all()
        assert failed == []
        completed = db_session.query(Event).filter(Event.kind == "reconcile_completed").all()
        assert len(completed) == 1

    def test_probe_retry_thread_start_failure_does_not_fail_reconcile(
        self, db_session, tmp_data_dir
    ):
        # The probe_retry import is now hoisted to the reconciler module top
        # (validated at startup), so an unresolvable module fails LOUDLY there
        # rather than being silently swallowed per-reconcile. The best-effort
        # guarantee now covers ONLY the thread START: drive the REAL
        # schedule_probe_retry and make Thread.start() raise (as under thread
        # exhaustion). schedule_probe_retry re-raises, _maybe_schedule_probe_retry
        # swallows it, and the already-committed "warning" reconcile outcome MUST
        # stand — no flip to "failed", no misleading reconcile_failed event.
        from app.reconciler import probe_retry

        svc = _create_service(db_session)
        edge = MagicMock()
        edge.id = "edge_id"
        edge.status = "running"
        # Every CRITICAL check passes; only the HTTPS probe fails -> "warning",
        # so the reconciler reaches the probe-retry scheduling branch.
        checks = {
            "upstream_container_present": True,
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

        # A Thread whose start() raises: the REAL schedule_probe_retry then
        # exercises its re-raise path and no background thread actually runs.
        failing_thread = MagicMock()
        failing_thread.start.side_effect = RuntimeError("can't start new thread")

        probe_retry._ACTIVE_RETRIES.clear()
        try:
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
                patch(_P_AGGREGATE, return_value="warning"),
                patch(_P_HEALTH, return_value=checks),
                patch.object(probe_retry.threading, "Thread", return_value=failing_thread),
            ):
                result = reconcile_service(db_session, svc)
        finally:
            probe_retry._ACTIVE_RETRIES.clear()

        # The thread-start failure must not corrupt the committed reconcile outcome.
        failing_thread.start.assert_called_once()
        assert result["phase"] == "warning"
        assert result["error"] is None

        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "warning"
        failed = db_session.query(Event).filter(Event.kind == "reconcile_failed").all()
        assert failed == []
        completed = db_session.query(Event).filter(Event.kind == "reconcile_completed").all()
        assert len(completed) == 1

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

        cert_dir = Path(tmp_data_dir) / "certs" / svc.hostname / "current"
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

        cert_dir = Path(tmp_data_dir) / "certs" / svc.hostname / "current"
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
    def test_no_tailscale_ip_skips_dns_but_completes(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health, mock_dns,
        db_session, tmp_data_dir,
    ):
        # When the edge never reports a Tailscale IP, the DNS step MUST be skipped
        # (no record can point at a missing IP) yet the reconcile still proceeds to
        # health and completes. CF token + zone are configured so the ONLY reason
        # DNS is skipped is the absent IP, isolating the `ts_ip` guard branch.
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        mock_secret.side_effect = lambda name: {
            "tailscale_authkey": "ts-key", "cloudflare_token": "cf-tok",
        }.get(name)
        set_setting(db_session, "cf_zone_id", "zone1")
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = None  # edge never surfaced a Tailscale IP
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        result = reconcile_service(db_session, svc)

        assert result["tailscale_ip"] is None
        mock_dns.assert_not_called()
        assert result["phase"] == "healthy"
        status = db_session.get(ServiceStatus, svc.id)
        assert status.tailscale_ip is None

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
    def test_tailscale_ip_acquired_event_emitted_once(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # The tailscale_ip_acquired event fires on a real IP CHANGE only. A second
        # reconcile that detects the SAME IP must NOT re-emit it (the persisted IP
        # already equals the detected one), or every sweep would spam the event log.
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        reconcile_service(db_session, svc)
        reconcile_service(db_session, svc)

        events = (
            db_session.query(Event)
            .filter(Event.kind == "tailscale_ip_acquired")
            .all()
        )
        assert len(events) == 1
        assert events[0].details == {"ip": "100.64.0.1"}

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
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_non_runtimeerror_reload_failure_is_classified_as_reload_failed(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # reload_caddy re-raises a non-retryable docker.errors.APIError (an
        # OSError subclass, NOT a RuntimeError) straight from exec_run. A narrow
        # `except RuntimeError` let that escape to the generic handler and be
        # mislabeled "Unexpected error"; every reload failure MUST be classified
        # as a Caddy reload failure (so the reload-pending marker survives for
        # the next retry) regardless of the concrete exception type.
        import docker.errors

        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "changed config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_reload.side_effect = docker.errors.APIError("exec create failed")

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert "Caddy reload failed" in result["error"]
        assert "Unexpected error" not in result["error"]
        mock_health.assert_not_called()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "failed"
        assert "Caddy reload failed" in (status.message or "")
        # The reload-pending marker survives the failed reload so the next
        # reconcile retries even when the on-disk config already matches desired.
        reload_pending = Path(tmp_data_dir) / "generated" / svc.id / ".reload_pending"
        assert reload_pending.exists()

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
    def test_reload_runtimeerror_is_classified_as_caddy_rejected(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # Reload differentiation: a RuntimeError means Caddy rejected the config.
        # It MUST surface as a plain "Caddy reload failed: <e>" (not the
        # Docker/edge or unexpected variants), and — like every reload failure —
        # leave .reload_pending set so the next reconcile retries.
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "changed config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_reload.side_effect = RuntimeError("adapter caddyfile: unknown directive")

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert result["error"] == "Caddy reload failed: adapter caddyfile: unknown directive"
        mock_health.assert_not_called()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "failed"
        reload_pending = Path(tmp_data_dir) / "generated" / svc.id / ".reload_pending"
        assert reload_pending.exists()

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
    def test_reload_dockerexception_is_classified_as_docker_unavailable(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # Reload differentiation: a docker.errors.DockerException (the edge/Docker
        # daemon being unreachable) is tagged "Docker/edge unavailable" — distinct
        # from a Caddy config rejection — yet still raises ReconcileError so the
        # reload-pending marker survives for the next retry.
        import docker.errors

        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "changed config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_reload.side_effect = docker.errors.DockerException("daemon connection failed")

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert result["error"] == (
            "Caddy reload failed: Docker/edge unavailable: daemon connection failed"
        )
        mock_health.assert_not_called()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "failed"
        reload_pending = Path(tmp_data_dir) / "generated" / svc.id / ".reload_pending"
        assert reload_pending.exists()

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
    def test_reload_connectionerror_is_classified_as_docker_unavailable(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # The same "Docker/edge unavailable" arm also catches a plain
        # ConnectionError (e.g. the Caddy admin-API socket refusing the connect).
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "changed config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_reload.side_effect = ConnectionError("admin api connection refused")

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert result["error"] == (
            "Caddy reload failed: Docker/edge unavailable: admin api connection refused"
        )
        mock_health.assert_not_called()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "failed"
        reload_pending = Path(tmp_data_dir) / "generated" / svc.id / ".reload_pending"
        assert reload_pending.exists()

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
    def test_reload_unexpected_error_is_classified_as_unexpected(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # Reload differentiation: anything that is neither a Caddy config
        # rejection (RuntimeError) nor a Docker/edge outage is tagged
        # "(unexpected)", but is STILL a reload failure — it raises ReconcileError
        # so the reload-pending marker survives for the next retry.
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "changed config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_reload.side_effect = ValueError("totally unexpected")

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert result["error"] == "Caddy reload failed (unexpected): totally unexpected"
        mock_health.assert_not_called()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "failed"
        reload_pending = Path(tmp_data_dir) / "generated" / svc.id / ".reload_pending"
        assert reload_pending.exists()

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
    def test_renewed_cert_forces_reload_when_config_unchanged(
        self, mock_secret, mock_render, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # write_caddyfile is intentionally NOT mocked so config_changed reflects
        # the real on-disk Caddyfile. The cert file is managed by hand to simulate
        # a renewal landing on disk between reconciles. Regression for the HIGH
        # "renewed cert never served": Caddy never re-reads a file-based cert and
        # `caddy reload` skips it when the config text is unchanged, so the
        # reconciler MUST force a reload purely because the cert fingerprint moved.
        from datetime import datetime, timedelta

        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "stable config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"
        mock_reload.return_value = "ok"

        cert_dir = Path(tmp_data_dir) / "certs" / svc.hostname / "current"
        cert_dir.mkdir(parents=True)
        cert_path = cert_dir / "fullchain.pem"
        cert_path.write_text("CERT-V1")

        with patch(
            "app.certs.cert_manager.get_cert_expiry",
            return_value=datetime.now() + timedelta(days=365),
        ):
            # 1) First reconcile: config newly written + cert present -> reload
            # fires and the loaded-cert fingerprint is recorded.
            first = reconcile_service(db_session, svc)
            assert first["caddy_reloaded"] is True

            # 2) Steady state: config AND cert unchanged -> no reload.
            mock_reload.reset_mock()
            second = reconcile_service(db_session, svc)
            assert second["caddy_reloaded"] is False
            mock_reload.assert_not_called()

            # 3) A renewal lands on disk (identical Caddyfile) -> reload MUST be
            # forced by the cert fingerprint change alone.
            cert_path.write_text("CERT-V2")
            mock_reload.reset_mock()
            third = reconcile_service(db_session, svc)
            assert third["caddy_reloaded"] is True
            mock_reload.assert_called_once()

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
    def test_cert_current_symlink_swap_forces_reload(
        self, mock_secret, mock_render, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # On-disk cert redesign: each issuance lands at certs/<hostname>/gen-<...>/
        # and a single relative `current` symlink is repointed at it; readers and
        # the reconciler's fingerprint go through certs/<hostname>/current/.
        # A renewal repoints `current` to a NEW gen dir, leaving the Caddyfile
        # byte-identical. The fingerprint read MUST follow the symlink so the
        # swap is detected and a reload is forced — Caddy never re-reads a file
        # cert and `caddy reload` skips it when the config text is unchanged.
        # Regression for the symlink-follow assumption the redesign relies on.
        import os
        from datetime import datetime, timedelta

        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "stable config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"
        mock_reload.return_value = "ok"

        host_dir = Path(tmp_data_dir) / "certs" / svc.hostname
        gen1 = host_dir / "gen-1"
        gen2 = host_dir / "gen-2"
        gen1.mkdir(parents=True)
        gen2.mkdir(parents=True)
        (gen1 / "fullchain.pem").write_text("CERT-GEN1")
        (gen1 / "privkey.pem").write_text("KEY-GEN1")
        (gen2 / "fullchain.pem").write_text("CERT-GEN2")
        (gen2 / "privkey.pem").write_text("KEY-GEN2")
        current = host_dir / "current"
        current.symlink_to("gen-1")  # relative target, exactly like production

        def _swap_current(target: str) -> None:
            # Atomic repoint: stage a temp symlink, then rename it over `current`.
            tmp = host_dir / ".current.tmp"
            tmp.symlink_to(target)
            os.replace(tmp, current)

        with (
            patch(
                "app.certs.cert_manager.get_cert_expiry",
                return_value=datetime.now() + timedelta(days=365),
            ),
            patch("app.certs.cert_manager.cert_key_pair_matches", return_value=True),
        ):
            # 1) First reconcile establishes the loaded-cert baseline (gen-1).
            first = reconcile_service(db_session, svc)
            assert first["caddy_reloaded"] is True

            # 2) Steady state: config and cert (still gen-1) unchanged -> no reload.
            mock_reload.reset_mock()
            second = reconcile_service(db_session, svc)
            assert second["caddy_reloaded"] is False
            mock_reload.assert_not_called()

            # 3) Renewal repoints `current` -> gen-2 (different bytes). The
            # fingerprint, read THROUGH the symlink, must move and force a reload
            # even though the Caddyfile is byte-identical and the cert was never
            # re-issued by this reconcile.
            _swap_current("gen-2")
            mock_reload.reset_mock()
            third = reconcile_service(db_session, svc)
            assert third["caddy_reloaded"] is True
            mock_reload.assert_called_once()

        # The reload was driven purely by the symlink swap, not a re-issuance.
        mock_cert.assert_not_called()

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
    def test_mismatched_cert_key_pair_triggers_reissue(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        # A valid, unexpired cert whose private key does not match (e.g. a crash
        # between cert_manager's two atomic renames left fullchain.pem and
        # privkey.pem from different issuances) must be healed at reconcile time,
        # not only by the daily renewal scan. The expiry-based checks never notice.
        from datetime import datetime, timedelta

        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        cert_dir = Path(tmp_data_dir) / "certs" / svc.hostname / "current"
        cert_dir.mkdir(parents=True)
        (cert_dir / "fullchain.pem").write_text("cert")

        far_future = datetime.now() + timedelta(days=365)
        # Matching pair, not expiring -> no re-issue.
        with (
            patch("app.certs.cert_manager.get_cert_expiry", return_value=far_future),
            patch("app.certs.cert_manager.cert_key_pair_matches", return_value=True),
        ):
            reconcile_service(db_session, svc)
        mock_cert.assert_not_called()

        # Same cert, but the on-disk key no longer matches -> heal via re-issue.
        mock_cert.reset_mock()
        with (
            patch("app.certs.cert_manager.get_cert_expiry", return_value=far_future),
            patch("app.certs.cert_manager.cert_key_pair_matches", return_value=False),
        ):
            reconcile_service(db_session, svc)
        mock_cert.assert_called_once()

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

    def test_thread_start_failure_discards_active_retry_key(self):
        # When Thread.start() raises (e.g. thread exhaustion), schedule_probe_retry
        # MUST re-raise AND remove the key it optimistically registered in
        # _ACTIVE_RETRIES — otherwise the failed attempt would permanently block
        # every future retry for that (service, socket) under the dedup guard.
        from app.reconciler import probe_retry

        key = ("svc_fail", None)
        probe_retry._ACTIVE_RETRIES.clear()
        try:
            failing = MagicMock()
            failing.start.side_effect = RuntimeError("can't start new thread")
            with (
                patch.object(probe_retry.threading, "Thread", return_value=failing),
                pytest.raises(RuntimeError, match="can't start new thread"),
            ):
                probe_retry.schedule_probe_retry("svc_fail")
            # The optimistic registration was rolled back, not left dangling.
            assert key not in probe_retry._ACTIVE_RETRIES

            # Proof of consequence: a later reschedule is no longer blocked by the
            # dedup guard and successfully spawns a fresh thread.
            ok = MagicMock()
            with patch.object(probe_retry.threading, "Thread", return_value=ok) as mock_thread:
                probe_retry.schedule_probe_retry("svc_fail")
            mock_thread.assert_called_once()
            ok.start.assert_called_once()
            assert key in probe_retry._ACTIVE_RETRIES
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


class TestForgetReconcileLock:
    """Registry lifecycle: forget_reconcile_lock keeps _RECONCILE_LOCKS bounded so
    deleted services no longer leak per-service RLock entries forever."""

    def test_forget_removes_entry_and_allows_fresh_lock(self):
        from app.locks import (
            _RECONCILE_LOCKS,
            forget_reconcile_lock,
            reconcile_lock_for,
        )

        sid = "svc_forget_x"
        try:
            lock1 = reconcile_lock_for(sid)
            assert _RECONCILE_LOCKS.get(sid) is lock1

            forget_reconcile_lock(sid)
            assert sid not in _RECONCILE_LOCKS

            # A later acquire creates a brand-new entry, not the dropped object.
            lock2 = reconcile_lock_for(sid)
            assert _RECONCILE_LOCKS.get(sid) is lock2
            assert lock2 is not lock1
        finally:
            _RECONCILE_LOCKS.pop(sid, None)

    def test_forget_unknown_id_is_noop(self):
        from app.locks import _RECONCILE_LOCKS, forget_reconcile_lock

        sid = "svc_never_seen_q"
        assert sid not in _RECONCILE_LOCKS
        # Must not raise KeyError for an id that was never registered.
        forget_reconcile_lock(sid)
        assert sid not in _RECONCILE_LOCKS


class TestMarkerWriteDurability:
    """AR8: the crash-desync guard markers (.reload_pending / .cert_loaded) must
    be published through fsutil.atomic_write_text (fsync + atomic rename), not a
    bare write_text that a power-loss could lose."""

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
    def test_markers_written_via_atomic_write_text(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):
        from pathlib import Path

        from app.reconciler import steps

        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "changed config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"
        mock_reload.return_value = "ok"

        cert_dir = Path(tmp_data_dir) / "certs" / svc.hostname / "current"
        cert_dir.mkdir(parents=True)
        (cert_dir / "fullchain.pem").write_text("CERT-V1")

        real_atomic = steps.atomic_write_text
        written: list[str] = []

        def _spy(path, text, **kwargs):
            written.append(Path(path).name)
            return real_atomic(path, text, **kwargs)

        with patch.object(steps, "atomic_write_text", side_effect=_spy):
            result = reconcile_service(db_session, svc)

        assert result["caddy_reloaded"] is True
        # New config -> .reload_pending written durably; successful reload ->
        # .cert_loaded fingerprint recorded durably.
        assert ".reload_pending" in written
        assert ".cert_loaded" in written
        gen_dir = Path(tmp_data_dir) / "generated" / svc.id
        assert (gen_dir / ".cert_loaded").read_text(encoding="utf-8").strip() != ""
