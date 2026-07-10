"""Tests for reconcile loop and trigger helpers."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

import app.database as database_module
from app.locks import _RECONCILE_LOCKS, forget_reconcile_lock, reconcile_lock_for
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler import reconcile_loop as loop_mod
from app.reconciler.reconcile_loop import reconcile_all, reconcile_one
from tests._services_helpers import create_service_db


def _create_service(db, name="TestApp", enabled=True, **overrides):
    slug = name.lower().replace(" ", "")
    values = {
        "name": name,
        "upstream_container_name": slug,
        "hostname": f"{slug}.example.com",
        "edge_container_name": f"edge_{slug}",
        "network_name": f"edge_net_{slug}",
        "ts_hostname": f"edge-{slug}",
        "enabled": enabled,
    }
    values.update(overrides)
    return create_service_db(db, **values)


class TestReconcileAll:
    @patch("app.reconciler.reconcile_loop.reconcile_service")
    def test_reconciles_enabled_services(self, mock_reconcile, db_session):
        _create_service(db_session, name="App1")
        _create_service(db_session, name="App2")
        _create_service(db_session, name="Disabled", enabled=False)

        mock_reconcile.return_value = {"phase": "healthy"}

        count = reconcile_all(db_session)
        assert count == 2
        assert mock_reconcile.call_count == 2

    @patch("app.reconciler.reconcile_loop.reconcile_service")
    def test_counts_failures(self, mock_reconcile, db_session):
        _create_service(db_session, name="App1")
        _create_service(db_session, name="App2")

        mock_reconcile.side_effect = [RuntimeError("fail"), {"phase": "healthy"}]

        count = reconcile_all(db_session)
        assert count == 2  # Both counted even if one fails

    @patch("app.reconciler.reconcile_loop.reconcile_service")
    def test_no_services(self, mock_reconcile, db_session):
        count = reconcile_all(db_session)
        assert count == 0
        mock_reconcile.assert_not_called()

    def test_skips_service_deleted_mid_sweep(self, db_session):
        # A delete that lands after the id snapshot but before a service's turn
        # must skip ONLY that service, never abort the sweep. reconcile_service
        # commits per service (expiring the session), so the loop re-fetches each
        # id and a deleted row resolves to None (Session.get returns None, not
        # ObjectDeletedError).
        a = _create_service(db_session, name="A")
        b = _create_service(db_session, name="B")
        seen = []

        def fake_reconcile(db, svc, *, socket_path=None):
            seen.append(svc.id)
            # Simulate a concurrent delete landing mid-sweep: remove the other
            # service before the loop reaches it.
            other_id = b.id if svc.id == a.id else a.id
            target = db.get(Service, other_id)
            if target is not None:
                db.delete(target)
                db.flush()
            return {"phase": "healthy"}

        with patch(
            "app.reconciler.reconcile_loop.reconcile_service",
            side_effect=fake_reconcile,
        ):
            count = reconcile_all(db_session)

        # First service reconciled; the second was deleted before its turn, so it
        # is skipped (not counted) and the sweep completes without error.
        assert len(seen) == 1
        assert count == 1


class TestReconcileOne:
    @patch("app.reconciler.reconcile_loop.reconcile_service")
    def test_reconciles_single_service(self, mock_reconcile, db_session):
        svc = _create_service(db_session)
        mock_reconcile.return_value = {"phase": "healthy", "tailscale_ip": "100.64.0.1"}

        result = reconcile_one(db_session, svc.id)
        assert result["phase"] == "healthy"
        mock_reconcile.assert_called_once()

    def test_raises_for_missing_service(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            reconcile_one(db_session, "svc_nonexistent")


class TestReconcileLoop:
    """Behaviour of the async background loop itself: startup delay, interval
    sleep, docker-socket forwarding, exception backoff, clean cancellation, and
    session cleanup."""

    @staticmethod
    def _fake_sleep(sleeps):
        async def fake_sleep(secs):
            # Record each sleep; break out of the otherwise-infinite loop on the
            # second sleep (startup delay is first, the post-iteration sleep is
            # second) by simulating shutdown cancellation.
            sleeps.append(secs)
            if len(sleeps) >= 2:
                raise asyncio.CancelledError()
        return fake_sleep

    def test_runs_sweep_then_sleeps_interval_and_cancels_cleanly(self):

        mock_session = MagicMock()
        session_factory = MagicMock(return_value=mock_session)
        sleeps: list[int] = []

        with (
            patch.object(database_module, "SessionLocal", session_factory),
            patch("app.settings_store.get_positive_int_setting", return_value=42),
            patch("app.edge.docker_client.resolve_socket", return_value="unix:///custom.sock"),
            patch.object(loop_mod, "reconcile_all", return_value=3) as mock_all,
            patch.object(loop_mod.asyncio, "sleep", self._fake_sleep(sleeps)),
            pytest.raises(asyncio.CancelledError),
        ):
            asyncio.run(loop_mod.reconcile_loop())

        # 5s startup delay, one sweep, then the configured interval.
        assert sleeps == [5, 42]
        mock_all.assert_called_once()
        # The docker socket from settings is forwarded to the sweep.
        assert mock_all.call_args.kwargs.get("socket_path") == "unix:///custom.sock"
        # The per-sweep session is always closed.
        mock_session.close.assert_called_once()

    def test_blank_docker_socket_forwards_none(self):

        mock_session = MagicMock()
        session_factory = MagicMock(return_value=mock_session)
        sleeps: list[int] = []

        with (
            patch.object(database_module, "SessionLocal", session_factory),
            patch("app.settings_store.get_positive_int_setting", return_value=10),
            patch("app.edge.docker_client.resolve_socket", return_value=None),
            patch.object(loop_mod, "reconcile_all", return_value=0) as mock_all,
            patch.object(loop_mod.asyncio, "sleep", self._fake_sleep(sleeps)),
            pytest.raises(asyncio.CancelledError),
        ):
            asyncio.run(loop_mod.reconcile_loop())

        # An empty docker_socket_path setting must collapse to None, never "".
        assert mock_all.call_args.kwargs.get("socket_path") is None

    def test_sweep_error_backs_off_30s_and_does_not_crash(self):

        mock_session = MagicMock()
        session_factory = MagicMock(return_value=mock_session)
        sleeps: list[int] = []

        with (
            patch.object(database_module, "SessionLocal", session_factory),
            # Fail inside the sweep thread, before reconcile_all runs.
            patch(
                "app.settings_store.get_positive_int_setting",
                side_effect=RuntimeError("settings unavailable"),
            ),
            patch("app.settings_store.get_setting", return_value=None),
            patch.object(loop_mod, "reconcile_all") as mock_all,
            patch.object(loop_mod.asyncio, "sleep", self._fake_sleep(sleeps)),
            pytest.raises(asyncio.CancelledError),
        ):
            asyncio.run(loop_mod.reconcile_loop())

        # Startup delay, then the error backoff — the loop never propagates the
        # sweep error, it backs off 30s and would retry.
        assert sleeps == [5, 30]
        mock_all.assert_not_called()
        # The session is still closed despite the sweep raising.
        mock_session.close.assert_called_once()

    def test_health_loop_uses_its_own_interval_and_sweep(self):
        # The health loop duplicates the reconcile loop's shape; this pins that it
        # reads its OWN interval setting and drives the lightweight sweep (not the
        # full reconcile_all) — a copy-paste of the reconcile loop would otherwise
        # silently sweep on the wrong cadence with the wrong function.

        mock_session = MagicMock()
        session_factory = MagicMock(return_value=mock_session)
        sleeps: list[int] = []

        with (
            patch.object(database_module, "SessionLocal", session_factory),
            patch(
                "app.settings_store.get_positive_int_setting", return_value=20
            ) as mock_setting,
            patch("app.edge.docker_client.resolve_socket", return_value="unix:///custom.sock"),
            patch.object(loop_mod, "health_check_all", return_value=5) as mock_sweep,
            patch.object(loop_mod, "reconcile_all") as mock_full,
            patch.object(loop_mod.asyncio, "sleep", self._fake_sleep(sleeps)),
            pytest.raises(asyncio.CancelledError),
        ):
            asyncio.run(loop_mod.health_check_loop())

        # 5s startup delay, one sweep, then the health-check interval.
        assert sleeps == [5, 20]
        mock_sweep.assert_called_once()
        # The full reconcile sweep is NOT the loop's job.
        mock_full.assert_not_called()
        # Reads the health interval setting, never the reconcile one.
        assert mock_setting.call_args.args[1] == "health_check_interval_seconds"
        # The resolved socket is forwarded to the sweep.
        assert mock_sweep.call_args.kwargs.get("socket_path") == "unix:///custom.sock"
        mock_session.close.assert_called_once()


class TestHealthCheckAll:
    """The lightweight health sweep: silent persist when healthy, escalate to a
    full reconcile when not, and resilience to per-service failures."""

    def test_healthy_persists_without_event_and_does_not_escalate(self, db_session):
        _create_service(db_session)
        with (
            patch("app.health.health_checker.run_health_checks", return_value={"ok": True}),
            patch("app.health.health_checker.aggregate_status", return_value="healthy"),
            patch("app.reconciler.reconcile_loop._persist_status") as mock_persist,
            patch.object(loop_mod, "reconcile_one") as mock_reconcile,
        ):
            count = loop_mod.health_check_all(db_session, socket_path=None)

        assert count == 1
        # A passing health check shows healthy in status but emits NO event.
        mock_persist.assert_called_once()
        assert mock_persist.call_args.kwargs["phase"] == "healthy"
        assert mock_persist.call_args.kwargs["event"] is None
        # Healthy steady state never triggers the expensive full reconcile.
        mock_reconcile.assert_not_called()

    def test_unhealthy_persists_then_escalates_to_full_reconcile(self, db_session):
        svc = _create_service(db_session)
        with (
            patch("app.health.health_checker.run_health_checks", return_value={"ok": False}),
            patch("app.health.health_checker.aggregate_status", return_value="error"),
            patch("app.reconciler.reconcile_loop._persist_status") as mock_persist,
            patch.object(loop_mod, "reconcile_one") as mock_reconcile,
        ):
            count = loop_mod.health_check_all(db_session, socket_path="unix:///custom.sock")

        assert count == 1
        # Status is still persisted (no event) before escalation.
        assert mock_persist.call_args.kwargs["phase"] == "error"
        assert mock_persist.call_args.kwargs["event"] is None
        # Drift escalates to a full reconcile, forwarding the resolved socket.
        mock_reconcile.assert_called_once_with(
            db_session, svc.id, socket_path="unix:///custom.sock"
        )

    def test_warning_phase_also_escalates_to_full_reconcile(self, db_session):
        # The sweep escalates on ANY non-healthy phase, not just "error": the
        # contract is "anything other than healthy is escalated" (a warning such
        # as a missing/mismatched DNS record is a real drift a full reconcile can
        # repair). Pins that a regression narrowing this to `phase == "error"`
        # would leave warning-phase services un-repaired between hourly sweeps.
        svc = _create_service(db_session)
        with (
            patch("app.health.health_checker.run_health_checks", return_value={"ok": False}),
            patch("app.health.health_checker.aggregate_status", return_value="warning"),
            patch("app.reconciler.reconcile_loop._persist_status") as mock_persist,
            patch.object(loop_mod, "reconcile_one") as mock_reconcile,
        ):
            count = loop_mod.health_check_all(db_session, socket_path=None)

        assert count == 1
        assert mock_persist.call_args.kwargs["phase"] == "warning"
        assert mock_persist.call_args.kwargs["event"] is None
        mock_reconcile.assert_called_once_with(db_session, svc.id, socket_path=None)

    def test_only_sweeps_enabled_services(self, db_session):
        _create_service(db_session, name="On", enabled=True)
        _create_service(db_session, name="Off", enabled=False)
        with (
            patch("app.health.health_checker.run_health_checks", return_value={"ok": True}),
            patch("app.health.health_checker.aggregate_status", return_value="healthy"),
            patch("app.reconciler.reconcile_loop._persist_status"),
            patch.object(loop_mod, "reconcile_one"),
        ):
            count = loop_mod.health_check_all(db_session)
        assert count == 1

    def test_one_failure_does_not_abort_sweep(self, db_session):
        _create_service(db_session, name="A")
        _create_service(db_session, name="B")
        with (
            patch(
                "app.health.health_checker.run_health_checks",
                side_effect=[RuntimeError("boom"), {"ok": True}],
            ),
            patch("app.health.health_checker.aggregate_status", return_value="healthy"),
            patch("app.reconciler.reconcile_loop._persist_status"),
            patch.object(loop_mod, "reconcile_one"),
        ):
            count = loop_mod.health_check_all(db_session)
        # Both counted as processed even though the first raised.
        assert count == 2

    def test_recovery_clears_stale_failure_message(self, db_session):
        # A prior failed reconcile leaves phase=failed plus an error message. When
        # the lightweight sweep later finds the service healthy it MUST clear that
        # stale message so the UI never shows "healthy" next to a failure message.
        # The full reconcile's health persist always sets message=None for every
        # non-failure phase; the fast sweep must honour the same invariant instead
        # of leaving the old message until the next hourly full reconcile.
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "failed"
        status.message = "Caddy reload failed: boom"
        db_session.commit()
        with (
            patch("app.health.health_checker.run_health_checks", return_value={"ok": True}),
            patch("app.health.health_checker.aggregate_status", return_value="healthy"),
            patch.object(loop_mod, "reconcile_one") as mock_reconcile,
        ):
            count = loop_mod.health_check_all(db_session, socket_path=None)
        assert count == 1
        # Healthy steady state never escalates, so nothing else clears the message.
        mock_reconcile.assert_not_called()
        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "healthy"
        assert status.message is None

    def test_skips_service_whose_reconcile_lock_is_held(self, db_session):
        # REC3: a service whose per-service reconcile lock is already held (a
        # reconcile / op in progress, e.g. a minutes-long lego DNS-01 issuance)
        # must be SKIPPED by the sweep — no health check, no status write, no
        # escalation, not counted — so one stuck service can never stall the whole
        # sweep (the head-of-line blocking this fix removes). The next sweep
        # retries it.
        svc = _create_service(db_session)
        lock = reconcile_lock_for(svc.id)
        held = threading.Event()
        release = threading.Event()

        def holder():
            with lock:
                held.set()
                release.wait(timeout=5)

        worker = threading.Thread(target=holder)
        worker.start()
        assert held.wait(timeout=5)
        try:
            with (
                patch("app.health.health_checker.run_health_checks") as mock_health,
                patch("app.health.health_checker.aggregate_status") as mock_aggregate,
                patch("app.reconciler.reconcile_loop._persist_status") as mock_persist,
                patch.object(loop_mod, "reconcile_one") as mock_reconcile,
            ):
                count = loop_mod.health_check_all(db_session, socket_path=None)

            # The contended service is skipped entirely and not counted.
            assert count == 0
            mock_health.assert_not_called()
            mock_aggregate.assert_not_called()
            mock_persist.assert_not_called()
            mock_reconcile.assert_not_called()
        finally:
            release.set()
            worker.join(timeout=5)
        assert not worker.is_alive()

    def test_checks_uncontended_service_normally(self, db_session):
        # REC3 counterpart: when the per-service reconcile lock is FREE the sweep
        # acquires it, runs the checks, and persists the status as usual.
        svc = _create_service(db_session)
        # The lock is free going in (non-blocking acquire succeeds, then released).
        lock = reconcile_lock_for(svc.id)
        assert lock.acquire(blocking=False)
        lock.release()
        with (
            patch(
                "app.health.health_checker.run_health_checks", return_value={"ok": True}
            ) as mock_health,
            patch("app.health.health_checker.aggregate_status", return_value="healthy"),
            patch("app.reconciler.reconcile_loop._persist_status") as mock_persist,
            patch.object(loop_mod, "reconcile_one") as mock_reconcile,
        ):
            count = loop_mod.health_check_all(db_session, socket_path=None)

        assert count == 1
        mock_health.assert_called_once()
        mock_persist.assert_called_once()
        assert mock_persist.call_args.kwargs["phase"] == "healthy"
        mock_reconcile.assert_not_called()

    def test_deleted_mid_sweep_does_not_leak_reconcile_lock(self, db_session):
        # REC1: the sweep try-acquires each service's per-service reconcile lock
        # (reconcile_lock_for re-creates the registry entry) BEFORE confirming the
        # row still exists. A service deleted after the id snapshot but before its
        # turn resolves to None here; that re-created entry MUST be forgotten — the
        # same thing reconcile_service does in its 'service gone' branch — so
        # _RECONCILE_LOCKS stays bounded instead of leaking one orphan entry per
        # delete-during-sweep for the whole process lifetime.
        a = _create_service(db_session, name="Alive")
        b = _create_service(db_session, name="Doomed")

        def delete_b_during_a(db, svc, generated_dir, certs_dir, socket_path):
            # Simulate a concurrent delete landing mid-sweep: drop B directly (NOT
            # via the delete path, so it does NOT forget B's lock for us) while A
            # is being checked, so B resolves to None when the sweep reaches it.
            if svc.id == a.id:
                target = db.get(Service, b.id)
                if target is not None:
                    db.delete(target)
                    db.flush()
            return {"ok": True}

        assert b.id not in _RECONCILE_LOCKS  # clean precondition
        try:
            with (
                patch(
                    "app.health.health_checker.run_health_checks",
                    side_effect=delete_b_during_a,
                ),
                patch("app.health.health_checker.aggregate_status", return_value="healthy"),
                patch.object(loop_mod, "reconcile_one") as mock_reconcile,
            ):
                count = loop_mod.health_check_all(db_session, socket_path=None)

            # Only the live service was checked; the deleted one was skipped.
            assert count == 1
            mock_reconcile.assert_not_called()
            # The orphan entry the sweep re-created for the deleted service must
            # NOT survive — the registry stays bounded by live + in-flight ids.
            assert b.id not in _RECONCILE_LOCKS
        finally:
            # The live service's entry was legitimately (re-)created; drop it so
            # the process-global registry stays clean for other tests.
            forget_reconcile_lock(a.id)

    def test_disabled_mid_sweep_does_not_clobber_disabled_status(self, db_session):
        # A disable that commits after the enabled-id snapshot but before this
        # service's turn must NOT be overwritten by a health-derived phase.
        # disable_service sets phase="disabled" (cleared message/checks/probe
        # retry) and releases the per-service reconcile lock BEFORE the sweep
        # acquires it, so without an in-lock re-check the sweep would run health
        # checks and persist a phase over "disabled" — and since neither loop
        # ever sweeps a disabled service, that wrong phase (here "healthy", the
        # worst case: the edge has not stopped yet) would stick forever.
        alive = _create_service(db_session, name="Alive")
        doomed = _create_service(db_session, name="Doomed")
        # Pre-set the disabled status exactly as disable_service leaves it.
        doomed_status = db_session.get(ServiceStatus, doomed.id)
        doomed_status.phase = "disabled"
        doomed_status.message = "Service disabled by user"
        doomed_status.health_checks = None
        db_session.commit()

        def disable_doomed_during_alive(db, svc, generated_dir, certs_dir, socket_path):
            # Land the disable mid-sweep: flip enabled=False on the doomed row
            # (committed via _persist_status below) while Alive is checked, so the
            # sweep re-reads it as disabled when it reaches it.
            if svc.id == alive.id:
                target = db.get(Service, doomed.id)
                target.enabled = False
                db.flush()
            return {"ok": True}

        with (
            patch(
                "app.health.health_checker.run_health_checks",
                side_effect=disable_doomed_during_alive,
            ),
            patch("app.health.health_checker.aggregate_status", return_value="healthy"),
            patch.object(loop_mod, "reconcile_one") as mock_reconcile,
        ):
            count = loop_mod.health_check_all(db_session, socket_path=None)

        # Only the live service was health-checked; the disabled one was skipped.
        assert count == 1
        mock_reconcile.assert_not_called()
        db_session.expire_all()
        doomed_status = db_session.get(ServiceStatus, doomed.id)
        # The "disabled" status survives — not clobbered with a health phase.
        assert doomed_status.phase == "disabled"
        assert doomed_status.message == "Service disabled by user"
        assert doomed_status.health_checks is None

    def test_healthy_sweep_clears_stale_probe_retry_schedule(self, db_session):
        # A background probe-retry thread schedules the NEXT retry (probe_retry_at
        # / probe_retry_attempt) and then sleeps — up to an hour on later attempts.
        # If the service recovers to healthy through the fast sweep (not through
        # the probe-retry thread itself), the sweep MUST clear that pending
        # schedule so the UI never shows a "next retry at ..." on an already-
        # healthy service until the sleeping thread finally wakes and clears it.
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.phase = "warning"
        status.probe_retry_at = datetime.now(UTC) + timedelta(minutes=55)
        status.probe_retry_attempt = 10
        db_session.commit()
        with (
            patch("app.health.health_checker.run_health_checks", return_value={"ok": True}),
            patch("app.health.health_checker.aggregate_status", return_value="healthy"),
            patch.object(loop_mod, "reconcile_one") as mock_reconcile,
            patch("app.reconciler.probe_retry.cancel_probe_retry") as mock_cancel,
        ):
            count = loop_mod.health_check_all(db_session, socket_path=None)
        assert count == 1
        # Recovered via the silent healthy path — no escalation clears it for us.
        mock_reconcile.assert_not_called()
        # The lingering probe-retry thread is actively cancelled, not just left to
        # notice on its next wake.
        mock_cancel.assert_called_once_with(svc.id)
        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "healthy"
        assert status.probe_retry_at is None
        assert status.probe_retry_attempt is None
