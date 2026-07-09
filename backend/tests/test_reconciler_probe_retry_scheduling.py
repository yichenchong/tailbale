"""Probe-retry scheduling tests for the reconciler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

import app.database as database_module
from app.models.event import Event
from app.models.service_status import ServiceStatus
from app.reconciler import probe_retry
from app.reconciler.reconciler import reconcile_service
from tests._reconciler_helpers import (
    _P_AGGREGATE,
    _P_CERT,
    _P_CREATE_EDGE,
    _P_FIND_EDGE,
    _P_HEALTH,
    _P_NETWORK,
    _P_RELOAD,
    _P_RENDER,
    _P_SECRET,
    _P_START,
    _P_TS_IP,
    _P_WRITE,
)
from tests._services_helpers import _create_service_in_db as _create_service


class TestReconcileProbeRetry:
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


class TestProbeRetryScheduling:
    def test_deduplicates_active_retry_thread(self):

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
            patch.object(probe_retry, "SessionLocal", TestSession),
            patch.object(probe_retry, "MAX_RETRIES", 1),
            patch.object(probe_retry, "_compute_delay", return_value=0),
            patch.object(probe_retry.time, "sleep"),
            patch.object(probe_retry, "get_runtime_paths", return_value={
                "generated_dir": "/tmp/generated",
                "certs_dir": "/tmp/certs",
            }),
            patch.object(probe_retry, "run_health_checks", side_effect=change_status_during_probe),
            patch.object(probe_retry, "aggregate_status", return_value="healthy"),
        ):
            probe_retry._probe_retry_loop(svc.id, None)

        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "pending"
        assert status.message == "reconcile in progress"

    def test_probe_retry_stops_before_sleep_for_disabled_service(self, db_session):


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

        probe_retry._ACTIVE_RETRIES.clear()
        try:
            with patch.object(probe_retry.threading, "Thread") as mock_thread:
                probe_retry.schedule_probe_retry("svc_123", "unix:///old.sock")
                probe_retry.schedule_probe_retry("svc_123", "unix:///new.sock")

            assert mock_thread.call_count == 2
            assert mock_thread.return_value.start.call_count == 2
        finally:
            probe_retry._ACTIVE_RETRIES.clear()
