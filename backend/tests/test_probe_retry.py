"""Tests for the background HTTPS probe-retry loop (app.reconciler.probe_retry).

The loop runs in a daemon thread in production.  Here we drive
``_probe_retry_loop`` synchronously with ``time.sleep`` stubbed and
``SessionLocal`` pointed at the in-memory test database.
"""

import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

import app.database as database_module
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler import probe_retry


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


def _checks(*, https=True, critical_ok=True, warning_ok=True):
    """Build a full 12-key health-check dict with controllable failures."""
    base = {
        "upstream_container_present": True,
        "upstream_network_connected": True,
        "edge_container_present": critical_ok,
        "edge_container_running": critical_ok,
        "tailscale_ready": True,
        "tailscale_ip_present": True,
        "cert_present": True,
        "caddy_config_present": True,
        "cert_not_expiring": True,
        "dns_record_present": warning_ok,
        "dns_matches_ip": warning_ok,
        "https_probe_ok": https,
    }
    return base


def _run_loop(db_session, svc, *, checks, max_retries=1):
    """Run _probe_retry_loop synchronously against the test DB."""
    TestSession = sessionmaker(bind=db_session.get_bind())
    with (
        patch.object(database_module, "SessionLocal", TestSession),
        patch.object(probe_retry, "SessionLocal", TestSession),
        patch.object(probe_retry, "MAX_RETRIES", max_retries),
        patch.object(probe_retry, "_compute_delay", return_value=0),
        patch.object(probe_retry.time, "sleep") as mock_sleep,
        patch.object(probe_retry, "get_runtime_paths", return_value={
            "generated_dir": "/tmp/generated", "certs_dir": "/tmp/certs",
        }),
        patch.object(probe_retry, "run_health_checks") as mock_health,
    ):
        if callable(checks):
            mock_health.side_effect = checks
        else:
            mock_health.return_value = checks
        probe_retry._probe_retry_loop(svc.id, None)
    db_session.expire_all()
    return mock_health, mock_sleep


# ---------------------------------------------------------------------------
# Backoff math
# ---------------------------------------------------------------------------


class TestComputeDelay:
    def test_exponential_sequence(self):
        assert [probe_retry._compute_delay(a) for a in range(7)] == [
            15, 30, 60, 120, 240, 480, 960,
        ]

    def test_capped_at_max_delay(self):
        # Large attempts saturate at the cap, never overflow.
        for attempt in (8, 12, 19, 64):
            assert probe_retry._compute_delay(attempt) == probe_retry.MAX_DELAY

    def test_first_attempt_is_initial_delay(self):
        assert probe_retry._compute_delay(0) == probe_retry.INITIAL_DELAY


# ---------------------------------------------------------------------------
# _update_retry_state: attempt counter + timestamp + stop conditions
# ---------------------------------------------------------------------------


class TestUpdateRetryState:
    def test_records_attempt_and_future_time(self, db_session):
        svc = _create_service(db_session)
        TestSession = sessionmaker(bind=db_session.get_bind())
        before = datetime.now(UTC)
        with patch.object(database_module, "SessionLocal", TestSession):
            assert probe_retry._update_retry_state(svc.id, 3, 120) is True
        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.probe_retry_attempt == 3
        assert status.probe_retry_at is not None
        # Stored naive-UTC ~= now + delay; compare against a naive reference.
        expected = (before + timedelta(seconds=120)).replace(tzinfo=None)
        assert abs((status.probe_retry_at - expected).total_seconds()) < 30

    def test_stops_and_clears_when_healthy(self, db_session):
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "healthy"
        status.probe_retry_at = datetime.now(UTC) + timedelta(minutes=5)
        status.probe_retry_attempt = 2
        db_session.commit()
        TestSession = sessionmaker(bind=db_session.get_bind())
        with patch.object(database_module, "SessionLocal", TestSession):
            assert probe_retry._update_retry_state(svc.id, 4, 60) is False
        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.probe_retry_at is None
        assert status.probe_retry_attempt is None

    def test_stops_and_clears_when_disabled(self, db_session):
        svc = _create_service(db_session, enabled=False)
        status = db_session.get(ServiceStatus, svc.id)
        status.probe_retry_at = datetime.now(UTC) + timedelta(minutes=5)
        status.probe_retry_attempt = 2
        db_session.commit()
        TestSession = sessionmaker(bind=db_session.get_bind())
        with patch.object(database_module, "SessionLocal", TestSession):
            assert probe_retry._update_retry_state(svc.id, 4, 60) is False
        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.probe_retry_at is None
        assert status.probe_retry_attempt is None

    def test_stops_when_status_missing(self, db_session):
        TestSession = sessionmaker(bind=db_session.get_bind())
        with patch.object(database_module, "SessionLocal", TestSession):
            assert probe_retry._update_retry_state("svc_missing", 1, 15) is False

    def test_unexpected_error_logged_at_warning_and_keeps_retrying(self, db_session, caplog):
        # A DB/lock error while persisting the retry state is the same
        # unexpected-failure class the main loop deliberately logs at WARNING. It
        # MUST surface at WARNING (visible under a production WARNING+ filter) and
        # the loop keeps going (returns True) instead of silently stalling with an
        # invisible failure. Pre-fix this was logged at INFO.
        import logging as _logging

        svc = _create_service(db_session)
        TestSession = sessionmaker(bind=db_session.get_bind())

        def _boom(_service_id):
            raise RuntimeError("lock acquisition blew up")

        with (
            patch.object(database_module, "SessionLocal", TestSession),
            patch.object(probe_retry, "service_reconcile_lock", _boom),
            caplog.at_level(_logging.WARNING, logger="app.reconciler.probe_retry"),
        ):
            assert probe_retry._update_retry_state(svc.id, 1, 15) is True

        warnings = [
            r for r in caplog.records
            if r.levelno == _logging.WARNING and "Failed to update retry state" in r.getMessage()
        ]
        assert warnings, "retry-state write failures must be logged at WARNING, not INFO"


class TestClearRetryState:
    def test_unexpected_error_logged_at_warning(self, db_session, caplog):
        # A swallowed clear leaves the probe-retry fields set forever, so the UI
        # shows a pending retry that never comes. That failure MUST be visible at
        # WARNING under a production WARNING+ filter. Pre-fix it was logged at INFO.
        import logging as _logging

        svc = _create_service(db_session)
        TestSession = sessionmaker(bind=db_session.get_bind())

        def _boom(_service_id):
            raise RuntimeError("lock acquisition blew up")

        with (
            patch.object(database_module, "SessionLocal", TestSession),
            patch.object(probe_retry, "service_reconcile_lock", _boom),
            caplog.at_level(_logging.WARNING, logger="app.reconciler.probe_retry"),
        ):
            probe_retry._clear_retry_state(svc.id)

        warnings = [
            r for r in caplog.records
            if r.levelno == _logging.WARNING and "Failed to clear retry state" in r.getMessage()
        ]
        assert warnings, "retry-state clear failures must be logged at WARNING, not INFO"


# ---------------------------------------------------------------------------
# _probe_retry_loop: stop conditions + bounded retries + last_probe_at
# ---------------------------------------------------------------------------


class TestProbeRetryLoop:
    def test_healthy_stops_early_and_clears_state(self, db_session):
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.probe_retry_at = datetime.now(UTC) + timedelta(minutes=5)
        status.probe_retry_attempt = 1
        db_session.commit()

        mock_health, _ = _run_loop(
            db_session, svc, checks=_checks(https=True), max_retries=5,
        )

        # Stopped on the first healthy result rather than running all 5 attempts.
        assert mock_health.call_count == 1
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "healthy"
        assert status.probe_retry_at is None
        assert status.probe_retry_attempt is None

    def test_exhaustion_clears_state_and_runs_every_attempt(self, db_session):
        svc = _create_service(db_session)
        # Probe keeps failing -> phase stays "warning" -> never healthy.
        mock_health, _ = _run_loop(
            db_session, svc, checks=_checks(https=False), max_retries=4,
        )

        assert mock_health.call_count == 4
        status = db_session.get(ServiceStatus, svc.id)
        # Retry state is cleared after exhaustion so the UI stops showing a
        # pending retry that will never come.
        assert status.probe_retry_at is None
        assert status.probe_retry_attempt is None

    def test_unchanged_phase_still_records_last_probe_and_checks(self, db_session):
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "warning"  # already warning; probe keeps it warning
        db_session.commit()

        _run_loop(db_session, svc, checks=_checks(https=False), max_retries=1)

        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "warning"
        assert status.last_probe_at is not None
        assert status.health_checks["https_probe_ok"] is False

    def test_gone_service_stops_without_crashing(self, db_session):
        svc = _create_service(db_session)
        sid = svc.id
        TestSession = sessionmaker(bind=db_session.get_bind())
        # Delete the service before the loop wakes from sleep.
        with (
            patch.object(database_module, "SessionLocal", TestSession),
            patch.object(probe_retry, "SessionLocal", TestSession),
            patch.object(probe_retry, "MAX_RETRIES", 1),
            patch.object(probe_retry, "_compute_delay", return_value=0),
            patch.object(probe_retry.time, "sleep"),
            patch.object(probe_retry, "get_runtime_paths", return_value={
                "generated_dir": "/tmp/generated", "certs_dir": "/tmp/certs",
            }),
            patch.object(probe_retry, "run_health_checks") as mock_health,
        ):
            db_session.delete(db_session.get(Service, sid))
            db_session.commit()
            probe_retry._probe_retry_loop(sid, None)
        # Service gone -> health checks never run.
        mock_health.assert_not_called()

    def test_unexpected_health_error_logged_at_warning_and_loop_survives(self, db_session, caplog):
        # An unexpected error from run_health_checks (not a normal failing probe)
        # MUST be surfaced at WARNING — visible under a production WARNING+ filter
        # — and MUST NOT crash the loop: it rolls back and continues to
        # exhaustion. Pre-fix this was logged at INFO, hiding genuine failures.
        import logging as _logging

        svc = _create_service(db_session)

        def boom(*args, **kwargs):
            raise RuntimeError("health probe blew up")

        with caplog.at_level(_logging.WARNING, logger="app.reconciler.probe_retry"):
            mock_health, _ = _run_loop(db_session, svc, checks=boom, max_retries=2)

        # The loop survived the unexpected error and ran every bounded attempt.
        assert mock_health.call_count == 2
        warnings = [
            r for r in caplog.records
            if r.levelno == _logging.WARNING and "Probe retry error" in r.getMessage()
        ]
        assert warnings, "unexpected probe-retry errors must be logged at WARNING, not INFO"

    def test_concurrent_status_change_during_probe_is_not_clobbered(self, db_session):
        # Lost-update guard: the loop snapshots the status BEFORE running the (slow)
        # health checks, then re-reads it under the reconcile lock. If a concurrent
        # reconcile changed the status meanwhile, the loop MUST leave that newer
        # status intact (skip this attempt) rather than overwrite it with its now-
        # stale aggregate — otherwise the reconcile's write is silently lost.
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "error"
        db_session.commit()

        def change_status_mid_probe(db, service, generated_dir, certs_dir, socket_path):
            # Stand in for a concurrent reconcile that lands a status change in the
            # window between the pre-probe snapshot and the post-probe re-read:
            # flush a different phase so the loop's populate_existing re-read sees
            # it diverge from the snapshot. Checks would otherwise aggregate to
            # "healthy", so a missing guard would clobber the row to healthy + event.
            st = db.get(ServiceStatus, service.id)
            st.phase = "warning"
            db.flush()
            return _checks(https=True)

        mock_health, _ = _run_loop(
            db_session, svc, checks=change_status_mid_probe, max_retries=1,
        )

        assert mock_health.call_count == 1
        # The probe must NOT have overwritten the concurrently-changed status with
        # its own "healthy" aggregate.
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase != "healthy"
        # ...and must NOT have emitted a phase-change event for the skipped attempt.
        clobber_event = (
            db_session.query(Event)
            .filter(
                Event.service_id == svc.id,
                Event.kind == "probe_retry_phase_change",
            )
            .first()
        )
        assert clobber_event is None


# ---------------------------------------------------------------------------
# Event accuracy: the loop runs a FULL health check, so the phase can move in
# either direction.  The emitted event MUST reflect the real direction/level.
# ---------------------------------------------------------------------------


class TestProbeRetryEventAccuracy:
    def _last_probe_event(self, db_session, svc_id):
        events = (
            db_session.query(Event)
            .filter(Event.service_id == svc_id)
            .order_by(Event.id.desc())
            .all()
        )
        return next((e for e in events if "probe retry" in (e.message or "")), None)

    def test_improvement_is_reported_as_improved(self, db_session):
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "error"
        db_session.commit()
        # Critical checks now pass but a warning check (dns) still fails ->
        # aggregate "warning": a genuine improvement from "error".
        _run_loop(
            db_session, svc,
            checks=_checks(https=True, critical_ok=True, warning_ok=False),
            max_retries=1,
        )
        evt = self._last_probe_event(db_session, svc.id)
        assert evt is not None
        assert "improved from error to warning" in evt.message
        assert evt.level == "warning"

    def test_degradation_is_not_mislabeled_as_improved(self, db_session):
        """Regression: warning -> error must NOT be logged as an improvement."""
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "warning"
        db_session.commit()
        # A critical check now fails -> aggregate "error": a degradation.
        _run_loop(
            db_session, svc,
            checks=_checks(https=False, critical_ok=False),
            max_retries=1,
        )
        evt = self._last_probe_event(db_session, svc.id)
        assert evt is not None
        assert "improved" not in evt.message
        assert "degraded from warning to error" in evt.message
        # An error-phase event must carry error severity, not "warning".
        assert evt.level == "error"

    def test_recovery_to_healthy_is_info_level(self, db_session):
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "warning"
        db_session.commit()
        _run_loop(db_session, svc, checks=_checks(https=True), max_retries=1)
        evt = self._last_probe_event(db_session, svc.id)
        assert evt is not None
        assert "improved from warning to healthy" in evt.message
        assert evt.level == "info"

# ---------------------------------------------------------------------------
# Concurrency: every service_status read-modify-write MUST run under the
# per-service reconcile lock so a concurrent reconcile cannot lost-update the
# status row.  Pre-fix these writes ran under db_write_section ALONE (no
# reconcile lock), which is the bug these tests pin.
# ---------------------------------------------------------------------------


class _LockOrderTracker:
    """Records reconcile-lock enter/exit depth and the lock depth observed at
    each DB commit, so a test can prove a commit happened while the per-service
    reconcile lock was held (depth > 0)."""

    def __init__(self):
        self.depth = 0
        self.entered_ids: list[str] = []
        self.commit_depths: list[int] = []

    def lock(self, service_id):
        tracker = self

        @contextlib.contextmanager
        def _cm():
            tracker.entered_ids.append(service_id)
            tracker.depth += 1
            try:
                yield
            finally:
                tracker.depth -= 1

        return _cm()

    def wrap_commit(self, real_commit):
        tracker = self

        def _tracked(db):
            tracker.commit_depths.append(tracker.depth)
            return real_commit(db)

        return _tracked


class TestStatusWriteUnderReconcileLock:
    """Regression for the lost-update race: the status persistence in each
    write path must be entered under service_reconcile_lock(service_id)."""

    def _patches(self, tracker):
        return (
            patch.object(probe_retry, "service_reconcile_lock", tracker.lock),
            patch.object(
                probe_retry, "commit_with_lock",
                tracker.wrap_commit(probe_retry.commit_with_lock),
            ),
        )

    def test_main_loop_commits_under_reconcile_lock(self, db_session):
        # phase pending -> warning triggers the status read-modify-write commit.
        svc = _create_service(db_session)
        tracker = _LockOrderTracker()
        lock_patch, commit_patch = self._patches(tracker)
        with lock_patch, commit_patch:
            _run_loop(db_session, svc, checks=_checks(https=False), max_retries=1)
        assert tracker.commit_depths, "expected the main loop to commit a status write"
        # Pre-fix the reconcile lock was never taken: every commit ran at depth 0.
        assert all(d > 0 for d in tracker.commit_depths)
        assert svc.id in tracker.entered_ids

    def test_update_retry_state_commits_under_reconcile_lock(self, db_session):
        svc = _create_service(db_session)
        tracker = _LockOrderTracker()
        TestSession = sessionmaker(bind=db_session.get_bind())
        lock_patch, commit_patch = self._patches(tracker)
        with patch.object(database_module, "SessionLocal", TestSession), lock_patch, commit_patch:
            assert probe_retry._update_retry_state(svc.id, 1, 15) is True
        assert tracker.commit_depths
        assert all(d > 0 for d in tracker.commit_depths)
        assert tracker.entered_ids == [svc.id]

    def test_clear_retry_state_commits_under_reconcile_lock(self, db_session):
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.probe_retry_at = datetime.now(UTC) + timedelta(minutes=5)
        status.probe_retry_attempt = 2
        db_session.commit()
        tracker = _LockOrderTracker()
        TestSession = sessionmaker(bind=db_session.get_bind())
        lock_patch, commit_patch = self._patches(tracker)
        with patch.object(database_module, "SessionLocal", TestSession), lock_patch, commit_patch:
            probe_retry._clear_retry_state(svc.id)
        assert tracker.commit_depths
        assert all(d > 0 for d in tracker.commit_depths)
        assert tracker.entered_ids == [svc.id]
