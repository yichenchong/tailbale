"""Tests for the reconciler per-step units (``app.reconciler.steps``)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from app import settings_store
from app.models.event import Event
from app.models.service_status import ServiceStatus
from app.reconciler import steps
from app.reconciler.errors import ReconcileError
from app.reconciler.reconciler import reconcile_service
from tests._reconciler_helpers import _STEP_MODULES, patch_across
from tests._services_helpers import create_service_db

# Patch at source modules: reconciler imports them via the module-reference
# pattern (e.g. ``secrets.read_secret``), so the attribute resolves on the source
# module at call time and patching the source still takes effect.
_P_SECRET = "app.secrets.read_secret"
_P_RENDER = "app.edge.config_renderer.render_caddyfile"
_P_WRITE = "app.edge.config_renderer.write_caddyfile"
_P_CERT = "app.certs.renewal_task.process_service_cert"
_P_NETWORK = "app.edge.network_manager.ensure_network"
_P_CREATE_EDGE = "app.edge.container_manager.create_edge_container"
_P_FIND_EDGE = "app.edge.container_session._find_edge_container"
_P_START = "app.edge.container_manager.start_edge"
_P_TS_IP = "app.edge.tailscale_ops.detect_tailscale_ip"
_P_RELOAD = "app.edge.caddy_admin.reload_caddy"
_P_HEALTH = "app.health.health_checker.run_health_checks"
_P_AGGREGATE = "app.health.health_checker.aggregate_status"
_P_DNS = "app.adapters.dns_reconciler.reconcile_dns"


# ---------------------------------------------------------------------------
# AR-R3-13: focused per-step unit tests. Each reconcile step in
# ``app.reconciler.steps`` is a cohesive, independently-callable unit; these
# assert its contract (inputs -> status writes -> return value / side effects)
# directly, complementing the full-pass orchestration + integration tests.
# ---------------------------------------------------------------------------


def _runtime_paths(base):
    d = Path(base)
    return steps._RuntimePaths(
        generated_dir=d / "g",
        certs_dir=d / "c",
        ts_state_dir=d / "t",
        host_generated_dir=d / "g",
        host_certs_dir=d / "c",
        host_ts_state_dir=d / "t",
    )


class TestValidateAndPrepareStep:
    def test_returns_key_and_creates_per_service_dirs(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        with patch(_P_SECRET, return_value="ts-key"):
            authkey, paths = steps.validate_and_prepare(db_session, svc)
        assert authkey == "ts-key"
        assert (paths.generated_dir / svc.id).is_dir()
        assert (paths.certs_dir / svc.hostname).is_dir()
        assert (paths.ts_state_dir / svc.edge_container_name).is_dir()
        assert db_session.get(ServiceStatus, svc.id).phase == "validating"

    def test_missing_authkey_raises_after_setting_phase(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        with patch(_P_SECRET, return_value=None), pytest.raises(ReconcileError, match="auth key"):
            steps.validate_and_prepare(db_session, svc)
        # The phase advanced to validating before the guard fired (observable).
        assert db_session.get(ServiceStatus, svc.id).phase == "validating"


class TestEnsureNetworkStep:
    def test_heals_stale_upstream_id(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session, upstream_container_id="old_id")
        with patch(_P_NETWORK, return_value=("edge_net", "new_id")):
            steps.ensure_network(db_session, svc, None)
        assert svc.upstream_container_id == "new_id"
        assert db_session.get(ServiceStatus, svc.id).phase == "creating_network"

    def test_unchanged_upstream_id_is_not_rewritten(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session, upstream_container_id="same")
        with patch(_P_NETWORK, return_value=("edge_net", "same")):
            steps.ensure_network(db_session, svc, None)
        assert svc.upstream_container_id == "same"
        assert db_session.get(ServiceStatus, svc.id).phase == "creating_network"


class TestEnsureAdditionalNetworksStep:
    _P_RECONCILE = "app.edge.network_manager.reconcile_additional_edge_networks"

    def test_null_short_circuits_without_touching_docker(self, db_session, tmp_data_dir):
        # NULL means "feature not configured": the step must not touch edge
        # network attachments (the safety valve for legacy/unmanaged services).
        svc = create_service_db(db_session)
        assert svc.additional_networks is None
        with patch(self._P_RECONCILE) as mock_reconcile:
            steps.ensure_additional_networks(db_session, svc, None)
        mock_reconcile.assert_not_called()

    def test_empty_list_converges(self, db_session, tmp_data_dir):
        # [] is an explicit desired state: converge (disconnect unmanaged nets).
        svc = create_service_db(db_session, additional_networks=[])
        with patch(self._P_RECONCILE) as mock_reconcile:
            steps.ensure_additional_networks(db_session, svc, None)
        mock_reconcile.assert_called_once_with(
            svc.edge_container_name, svc.network_name, [], None
        )
        # The step runs last and must NOT write a terminal phase (that would
        # clobber the health step's phase); create_service_db seeds "pending".
        assert db_session.get(ServiceStatus, svc.id).phase == "pending"

    def test_configured_networks_converge(self, db_session, tmp_data_dir):
        networks = [{"name": "opencloud_net", "aliases": ["cloud.example.com"]}]
        svc = create_service_db(db_session, additional_networks=networks)
        with patch(self._P_RECONCILE) as mock_reconcile:
            steps.ensure_additional_networks(db_session, svc, None)
        mock_reconcile.assert_called_once_with(
            svc.edge_container_name, svc.network_name, networks, None
        )


class TestEnsureCertStep:
    _P_EXPIRY = "app.certs.cert_manager.get_cert_expiry"
    _P_MATCH = "app.certs.cert_manager.cert_key_pair_matches"
    _P_WINDOW = "app.settings_store.get_positive_int_setting"

    def _cert(self, tmp_data_dir):
        cert = Path(tmp_data_dir) / "fullchain.pem"
        cert.write_text("CERTDATA")
        return cert

    def test_missing_cert_triggers_issuance(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        with patch(_P_CERT) as mock_issue:
            steps.ensure_cert(db_session, svc, Path(tmp_data_dir) / "absent.pem")
        mock_issue.assert_called_once()
        assert db_session.get(ServiceStatus, svc.id).phase == "ensuring_cert"

    def test_unreadable_expiry_triggers_issuance(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        with (
            patch(self._P_EXPIRY, return_value=None),
            patch(self._P_WINDOW, return_value=30),
            patch(_P_CERT) as mock_issue,
        ):
            steps.ensure_cert(db_session, svc, self._cert(tmp_data_dir))
        mock_issue.assert_called_once()

    def test_cert_within_renewal_window_triggers_issuance(self, db_session, tmp_data_dir):

        svc = create_service_db(db_session)
        soon = datetime.now(UTC) + timedelta(days=5)  # inside a 30-day window
        with (
            patch(self._P_EXPIRY, return_value=soon),
            patch(self._P_WINDOW, return_value=30),
            patch(self._P_MATCH, return_value=True),
            patch(_P_CERT) as mock_issue,
        ):
            steps.ensure_cert(db_session, svc, self._cert(tmp_data_dir))
        mock_issue.assert_called_once()

    def test_healthy_matching_cert_is_not_reissued(self, db_session, tmp_data_dir):

        svc = create_service_db(db_session)
        far = datetime.now(UTC) + timedelta(days=200)  # well outside the window
        with (
            patch(self._P_EXPIRY, return_value=far),
            patch(self._P_WINDOW, return_value=30),
            patch(self._P_MATCH, return_value=True),
            patch(_P_CERT) as mock_issue,
        ):
            steps.ensure_cert(db_session, svc, self._cert(tmp_data_dir))
        mock_issue.assert_not_called()

    def test_unexpired_but_mismatched_key_triggers_reissue(self, db_session, tmp_data_dir):

        svc = create_service_db(db_session)
        far = datetime.now(UTC) + timedelta(days=200)
        with (
            patch(self._P_EXPIRY, return_value=far),
            patch(self._P_WINDOW, return_value=30),
            patch(self._P_MATCH, return_value=False),
            patch(_P_CERT) as mock_issue,
        ):
            steps.ensure_cert(db_session, svc, self._cert(tmp_data_dir))
        mock_issue.assert_called_once()

    def test_overflow_window_renews_eagerly(self, db_session, tmp_data_dir):
        # AR-R3-8: days_from_now(window) returns None only when the loosely-bounded
        # renewal window overflows the datetime range. steps.ensure_cert treats
        # None as "renew eagerly" (an absurd window means every cert is within it),
        # instead of the pre-migration bare ``datetime.now(UTC) + timedelta(...)``
        # that raised OverflowError up into the reconcile's unexpected-error path.

        svc = create_service_db(db_session)
        far = datetime.now(UTC) + timedelta(days=200)
        with (
            patch(self._P_EXPIRY, return_value=far),
            patch(self._P_WINDOW, return_value=10**12),  # days -> timedelta overflow
            patch(self._P_MATCH, return_value=True),
            patch(_P_CERT) as mock_issue,
        ):
            steps.ensure_cert(db_session, svc, self._cert(tmp_data_dir))
        mock_issue.assert_called_once()


class TestRenderAndStageConfigStep:
    def test_new_config_marks_reload_pending_and_writes(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        gen = Path(tmp_data_dir) / "gen"
        (gen / svc.id).mkdir(parents=True)  # validate step normally creates this
        cert = Path(tmp_data_dir) / "cert.pem"  # absent -> current_cert_fp is None
        with patch(_P_RENDER, return_value="new config"), patch(_P_WRITE) as mock_write:
            stage = steps.render_and_stage_config(db_session, svc, gen, cert)
        assert stage.config_changed is True
        assert stage.reload_pending_path.exists()
        mock_write.assert_called_once()
        assert db_session.get(ServiceStatus, svc.id).phase == "rendering_config"

    def test_unchanged_config_and_cert_leaves_no_reload_pending(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        gen = Path(tmp_data_dir) / "gen"
        (gen / svc.id).mkdir(parents=True)
        (gen / svc.id / "Caddyfile").write_text("same config", encoding="utf-8")
        cert = Path(tmp_data_dir) / "cert.pem"  # absent -> no cert change
        with patch(_P_RENDER, return_value="same config"), patch(_P_WRITE) as mock_write:
            stage = steps.render_and_stage_config(db_session, svc, gen, cert)
        assert stage.config_changed is False
        assert not stage.reload_pending_path.exists()
        mock_write.assert_not_called()

    def test_cert_change_forces_reload_when_config_unchanged(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        gen = Path(tmp_data_dir) / "gen"
        (gen / svc.id).mkdir(parents=True)
        (gen / svc.id / "Caddyfile").write_text("same config", encoding="utf-8")
        cert = Path(tmp_data_dir) / "cert.pem"
        cert.write_text("RENEWED")  # fp present, no .cert_loaded marker -> cert changed
        with patch(_P_RENDER, return_value="same config"), patch(_P_WRITE) as mock_write:
            stage = steps.render_and_stage_config(db_session, svc, gen, cert)
        assert stage.config_changed is False
        assert stage.current_cert_fp is not None
        assert stage.reload_pending_path.exists()  # cert renewal forced a reload
        mock_write.assert_not_called()


class TestEnsureEdgeStep:
    def test_creates_edge_when_absent(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        created = MagicMock(id="cid", status="running")
        with (
            patch(_P_FIND_EDGE, side_effect=[None, created]),
            patch(_P_CREATE_EDGE, return_value="cid") as mock_create,
            patch(_P_START) as mock_start,
        ):
            steps.ensure_edge(db_session, svc, "ts-key", _runtime_paths(tmp_data_dir), None)
        mock_create.assert_called_once()
        mock_start.assert_not_called()
        status = db_session.get(ServiceStatus, svc.id)
        assert status.edge_container_id == "cid"
        assert status.phase == "ensuring_edge"
        kinds = {e.kind for e in db_session.query(Event).filter(Event.service_id == svc.id)}
        assert "edge_started" in kinds

    def test_running_edge_persists_id_without_starting(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        running = MagicMock(id="rid", status="running")
        with (
            patch(_P_FIND_EDGE, side_effect=[running, running]),
            patch(_P_CREATE_EDGE) as mock_create,
            patch(_P_START) as mock_start,
        ):
            steps.ensure_edge(db_session, svc, "ts-key", _runtime_paths(tmp_data_dir), None)
        mock_create.assert_not_called()
        mock_start.assert_not_called()
        assert db_session.get(ServiceStatus, svc.id).edge_container_id == "rid"

    def test_stopped_edge_is_started(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        stopped = MagicMock(id="eid", status="exited")
        with (
            patch(_P_FIND_EDGE, side_effect=[stopped, stopped]),
            patch(_P_CREATE_EDGE) as mock_create,
            patch(_P_START) as mock_start,
        ):
            steps.ensure_edge(db_session, svc, "ts-key", _runtime_paths(tmp_data_dir), None)
        mock_create.assert_not_called()
        mock_start.assert_called_once()
        assert db_session.get(ServiceStatus, svc.id).edge_container_id == "eid"


class TestDetectAndPersistIpStep:
    def test_first_acquire_persists_ip_and_emits_event(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        with patch(_P_TS_IP, return_value="100.64.0.9"):
            ip = steps.detect_and_persist_ip(db_session, svc, None)
        assert ip == "100.64.0.9"
        status = db_session.get(ServiceStatus, svc.id)
        assert status.tailscale_ip == "100.64.0.9"
        assert status.phase == "detecting_ip"
        kinds = {e.kind for e in db_session.query(Event).filter(Event.service_id == svc.id)}
        assert "tailscale_ip_acquired" in kinds

    def test_unchanged_ip_skips_persist_and_emits_no_event(self, db_session, tmp_data_dir):
        # An unchanged IP must NOT re-persist (redundant lock + commit) and must
        # emit no acquired event. Patch the persist call to prove it is skipped.
        svc = create_service_db(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.tailscale_ip = "100.64.0.9"
        db_session.commit()
        with (
            patch(_P_TS_IP, return_value="100.64.0.9"),
            patch("app.reconciler.steps.ip_step._persist_status") as mock_persist,
        ):
            steps.detect_and_persist_ip(db_session, svc, None)
        mock_persist.assert_not_called()
        kinds = [e.kind for e in db_session.query(Event).filter(Event.service_id == svc.id)]
        assert "tailscale_ip_acquired" not in kinds

    def test_no_ip_returns_none_and_does_not_persist(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        with patch(_P_TS_IP, return_value=None):
            ip = steps.detect_and_persist_ip(db_session, svc, None)
        assert ip is None
        assert db_session.get(ServiceStatus, svc.id).tailscale_ip is None


class TestEnsureDnsStep:
    def test_short_circuits_without_token(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        with patch(_P_SECRET, return_value=None), patch(_P_DNS) as mock_dns:
            steps.ensure_dns(db_session, svc, "100.64.0.1")
        mock_dns.assert_not_called()
        assert db_session.get(ServiceStatus, svc.id).phase == "ensuring_dns"

    def test_reconciles_dns_when_fully_configured(self, db_session, tmp_data_dir):

        svc = create_service_db(db_session)
        settings_store.set_setting(db_session, "cf_zone_id", "zone1")
        db_session.commit()
        with patch(_P_SECRET, return_value="cf-token"), patch(_P_DNS) as mock_dns:
            steps.ensure_dns(db_session, svc, "100.64.0.1")
        mock_dns.assert_called_once()

    def test_dns_failure_emits_warning_event_without_raising(self, db_session, tmp_data_dir):

        svc = create_service_db(db_session)
        settings_store.set_setting(db_session, "cf_zone_id", "zone1")
        db_session.commit()
        with (
            patch(_P_SECRET, return_value="cf-token"),
            patch(_P_DNS, side_effect=RuntimeError("cloudflare down")),
        ):
            steps.ensure_dns(db_session, svc, "100.64.0.1")  # best-effort: must not raise
        events = (
            db_session.query(Event)
            .filter(Event.service_id == svc.id, Event.kind == "dns_update_failed")
            .all()
        )
        assert len(events) == 1


class TestReloadIfNeededStep:
    def _stage(self, tmp_data_dir, *, config_changed, cert_fp="fp123", pending=True):
        base = Path(tmp_data_dir)
        reload_pending = base / ".reload_pending"
        if pending:
            reload_pending.write_text("1")
        return steps._ConfigStage(
            config_changed=config_changed,
            reload_pending_path=reload_pending,
            cert_state_path=base / ".cert_loaded",
            current_cert_fp=cert_fp,
        )

    def test_success_clears_marker_records_cert_and_emits_event(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        stage = self._stage(tmp_data_dir, config_changed=True)
        result = {"caddy_reloaded": False}
        with patch(_P_RELOAD) as mock_reload:
            steps.reload_if_needed(db_session, svc, stage, None, result)
        mock_reload.assert_called_once()
        assert result["caddy_reloaded"] is True
        assert not stage.reload_pending_path.exists()
        assert stage.cert_state_path.read_text(encoding="utf-8").strip() == "fp123"
        kinds = {e.kind for e in db_session.query(Event).filter(Event.service_id == svc.id)}
        assert "caddy_reloaded" in kinds

    def test_no_reload_when_nothing_pending(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        stage = self._stage(tmp_data_dir, config_changed=False, pending=False)
        result = {"caddy_reloaded": False}
        with patch(_P_RELOAD) as mock_reload:
            steps.reload_if_needed(db_session, svc, stage, None, result)
        mock_reload.assert_not_called()
        assert result["caddy_reloaded"] is False

    def test_runtime_error_raises_and_keeps_reload_pending(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        stage = self._stage(tmp_data_dir, config_changed=True)
        result = {"caddy_reloaded": False}
        with (
            patch(_P_RELOAD, side_effect=RuntimeError("bad Caddyfile")),
            pytest.raises(ReconcileError, match="Caddy reload failed"),
        ):
            steps.reload_if_needed(db_session, svc, stage, None, result)
        # The marker survives so the reload is retried on the next reconcile.
        assert stage.reload_pending_path.exists()
        assert result["caddy_reloaded"] is False

    def test_docker_error_classified_as_unavailable(self, db_session, tmp_data_dir):

        svc = create_service_db(db_session)
        stage = self._stage(tmp_data_dir, config_changed=True)
        result = {"caddy_reloaded": False}
        with (
            patch(_P_RELOAD, side_effect=docker.errors.DockerException("no daemon")),
            pytest.raises(ReconcileError, match="Docker/edge unavailable"),
        ):
            steps.reload_if_needed(db_session, svc, stage, None, result)
        assert stage.reload_pending_path.exists()


class TestRunAndPersistHealthStep:
    def test_persists_phase_checks_probe_time_and_event(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        checks = {"edge_container_running": True}
        with patch(_P_HEALTH, return_value=checks), patch(_P_AGGREGATE, return_value="healthy"):
            phase, out = steps.run_and_persist_health(
                db_session, svc, Path(tmp_data_dir) / "g", Path(tmp_data_dir) / "c", None
            )
        assert phase == "healthy"
        assert out == checks
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "healthy"
        assert status.health_checks == checks
        assert status.last_probe_at is not None
        assert status.last_reconciled_at is not None
        completed = (
            db_session.query(Event)
            .filter(Event.service_id == svc.id, Event.kind == "reconcile_completed")
            .all()
        )
        assert len(completed) == 1

    def test_reconcile_completed_event_level_tracks_phase(self, db_session, tmp_data_dir):
        # The reconcile_completed event level MUST track the aggregate phase so the
        # Events page shows the right severity (healthy->info, warning->warning,
        # error->error). Only the healthy path was pinned before; a regression
        # flattening the level ternary (e.g. always "info") would silently
        # mislabel every degraded reconcile's completion event.
        cases = [("healthy", "info"), ("warning", "warning"), ("error", "error")]
        for i, (phase, expected_level) in enumerate(cases):
            svc = create_service_db(
                db_session,
                hostname=f"lvl{i}.example.com",
                edge_container_name=f"edge_lvl{i}",
                network_name=f"edge_net_lvl{i}",
                ts_hostname=f"edge-lvl{i}",
            )
            checks = {"edge_container_running": True}
            with patch(_P_HEALTH, return_value=checks), patch(_P_AGGREGATE, return_value=phase):
                steps.run_and_persist_health(
                    db_session, svc, Path(tmp_data_dir) / "g", Path(tmp_data_dir) / "c", None
                )
            evt = (
                db_session.query(Event)
                .filter(Event.service_id == svc.id, Event.kind == "reconcile_completed")
                .one()
            )
            assert evt.level == expected_level
            assert evt.details == {"phase": phase, "checks": checks}

    def test_healthy_persist_clears_stale_probe_retry_schedule(self, db_session, tmp_data_dir):
        # A probe-retry thread may have scheduled the next retry (probe_retry_at /
        # probe_retry_attempt) and then gone to sleep. When a full reconcile's
        # health step finds the service healthy it MUST clear that pending schedule
        # so the frontend never shows a "next retry at ..." on a healthy service
        # (the sleeping thread would otherwise not clear it until it next wakes).
        svc = create_service_db(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.probe_retry_at = datetime.now(UTC) + timedelta(minutes=55)
        status.probe_retry_attempt = 10
        db_session.commit()
        checks = {"edge_container_running": True}
        with (
            patch(_P_HEALTH, return_value=checks),
            patch(_P_AGGREGATE, return_value="healthy"),
            patch("app.reconciler.probe_retry.cancel_probe_retry") as mock_cancel,
        ):
            steps.run_and_persist_health(
                db_session, svc, Path(tmp_data_dir) / "g", Path(tmp_data_dir) / "c", None
            )
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "healthy"
        assert status.probe_retry_at is None
        assert status.probe_retry_attempt is None
        mock_cancel.assert_called_once_with(svc.id)

    def test_non_healthy_persist_preserves_probe_retry_schedule(self, db_session, tmp_data_dir):
        # The converse: a still-degraded (warning/error) health result MUST NOT
        # wipe a legitimately-pending probe-retry schedule — clearing is scoped to
        # the healthy transition only, so an in-flight retry keeps its timing.
        svc = create_service_db(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        retry_at = datetime.now(UTC) + timedelta(minutes=5)
        status.probe_retry_at = retry_at
        status.probe_retry_attempt = 3
        db_session.commit()
        checks = {"https_probe_ok": False}
        with (
            patch(_P_HEALTH, return_value=checks),
            patch(_P_AGGREGATE, return_value="warning"),
            patch("app.reconciler.probe_retry.cancel_probe_retry") as mock_cancel,
        ):
            steps.run_and_persist_health(
                db_session, svc, Path(tmp_data_dir) / "g", Path(tmp_data_dir) / "c", None
            )
        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "warning"
        assert status.probe_retry_at is not None
        assert status.probe_retry_attempt == 3
        mock_cancel.assert_not_called()


class TestMaybeScheduleProbeRetryStep:
    """``_maybe_schedule_probe_retry`` was the only reconcile step in ``steps.py``
    with no direct per-step unit test (its guard was exercised solely through full
    ``reconcile_service`` runs). Pin its contract directly: schedule a probe retry
    IFF only the HTTPS probe failed while every CRITICAL check passed and the phase
    is degraded, and swallow a scheduling failure (best-effort).
    """

    _P_SCHEDULE = "app.reconciler.probe_retry.schedule_probe_retry"

    def _checks(self, *, https_ok, critical_ok=True):
        checks = {name: critical_ok for name in steps.CRITICAL_CHECKS}
        checks["https_probe_ok"] = https_ok
        return checks

    def test_schedules_when_only_probe_failed_and_critical_ok(self):
        with patch(self._P_SCHEDULE) as mock_schedule:
            steps.maybe_schedule_probe_retry(
                self._checks(https_ok=False), "warning", "svc_probe", "unix:///s.sock"
            )
        mock_schedule.assert_called_once_with("svc_probe", "unix:///s.sock")

    def test_not_scheduled_when_probe_passed(self):
        # The HTTPS probe passing is the sole reason no retry is scheduled here
        # (the phase is still degraded), isolating the ``not https_probe_ok`` guard.
        with patch(self._P_SCHEDULE) as mock_schedule:
            steps.maybe_schedule_probe_retry(
                self._checks(https_ok=True), "warning", "svc_probe", None
            )
        mock_schedule.assert_not_called()

    def test_not_scheduled_when_phase_healthy(self):
        # A healthy aggregate must not spawn a retry even with the probe flag
        # false: the guard requires a degraded (warning/error) phase.
        with patch(self._P_SCHEDULE) as mock_schedule:
            steps.maybe_schedule_probe_retry(
                self._checks(https_ok=False), "healthy", "svc_probe", None
            )
        mock_schedule.assert_not_called()

    def test_not_scheduled_when_a_critical_check_failed(self):
        # A failing CRITICAL check means a full reconcile — not a lightweight probe
        # retry — is the right repair, so no retry is scheduled.
        with patch(self._P_SCHEDULE) as mock_schedule:
            steps.maybe_schedule_probe_retry(
                self._checks(https_ok=False, critical_ok=False), "error", "svc_probe", None
            )
        mock_schedule.assert_not_called()

    def test_missing_critical_key_treated_as_ok_matches_aggregate(self):
        # A missing CRITICAL key must be treated as not-failing (mirroring
        # aggregate_status), so a probe-only failure with an absent critical key
        # still schedules a retry rather than silently disagreeing with the phase.
        checks = self._checks(https_ok=False)
        del checks[next(iter(steps.CRITICAL_CHECKS))]
        with patch(self._P_SCHEDULE) as mock_schedule:
            steps.maybe_schedule_probe_retry(checks, "warning", "svc_probe", None)
        mock_schedule.assert_called_once_with("svc_probe", None)

    def test_schedule_failure_is_swallowed(self):
        # Best-effort: a raising schedule_probe_retry (e.g. thread exhaustion) must
        # NOT propagate out of the step and flip an already-committed reconcile.
        with patch(self._P_SCHEDULE, side_effect=RuntimeError("no threads")) as mock_schedule:
            steps.maybe_schedule_probe_retry(
                self._checks(https_ok=False), "warning", "svc_probe", None
            )  # must not raise
        mock_schedule.assert_called_once()


class TestIntermediatePhaseVisibility:
    """AR-R3-5 (behavior-preserving guard): every phase-progress write must land
    as its own committed status transition so a UI polling mid-reconcile observes
    each phase. This is the observable contract AR-R3-5's commit-reduction must
    NOT break — the terminal health+reconcile_completed write is already a single
    commit, and no other pair may be collapsed without dropping a visible phase.
    """

    def test_each_intermediate_phase_is_individually_committed(self, db_session, tmp_data_dir):
        svc = create_service_db(db_session)
        committed_phases: list[str] = []
        real_update = steps._update_phase
        real_persist = steps._persist_status

        def rec_update(db, service_id, phase, message=None):
            real_update(db, service_id, phase, message)
            committed_phases.append(db.get(ServiceStatus, service_id).phase)

        def rec_persist(db, service_id, **kwargs):
            real_persist(db, service_id, **kwargs)
            committed_phases.append(db.get(ServiceStatus, service_id).phase)

        with (
            patch(_P_SECRET, return_value="ts-key"),
            patch(_P_RENDER, return_value="caddyfile content"),
            patch(_P_WRITE),
            patch(_P_CERT),
            patch(_P_NETWORK, return_value=("net123", "upstream123")),
            patch(_P_CREATE_EDGE, return_value="cid"),
            patch(_P_FIND_EDGE, return_value=None),
            patch(_P_START),
            patch(_P_TS_IP, return_value="100.64.0.1"),
            patch(_P_RELOAD),
            patch(_P_AGGREGATE, return_value="healthy"),
            patch(_P_HEALTH, return_value={"edge_container_running": True}),
            patch_across(_STEP_MODULES, "_update_phase", side_effect=rec_update),
            patch_across(_STEP_MODULES, "_persist_status", side_effect=rec_persist),
        ):
            result = reconcile_service(db_session, svc)

        assert result["phase"] == "healthy"
        expected_order = [
            "validating",
            "creating_network",
            "ensuring_cert",
            "rendering_config",
            "ensuring_edge",
            "detecting_ip",
            "ensuring_dns",
            "reloading_caddy",
            "checking_health",
        ]
        # Every intermediate phase was committed (observable to a mid-reconcile poll)
        # in spec order, each strictly before the terminal healthy state.
        seen_positions = [committed_phases.index(p) for p in expected_order]
        assert all(p in committed_phases for p in expected_order), committed_phases
        assert seen_positions == sorted(seen_positions), committed_phases
        assert committed_phases[-1] == "healthy"

    def test_additional_network_failure_runs_after_core_convergence(self, db_session, tmp_data_dir):
        # Blast-radius guard: an auxiliary additional-network failure must not
        # block the service's core convergence. ensure_additional_networks runs
        # LAST, so DNS/Caddy-reload/health execute and commit first; the missing
        # network then surfaces as a failed reconcile (loud, but non-blocking).
        svc = create_service_db(
            db_session,
            additional_networks=[{"name": "missing_net", "aliases": ["x.example.com"]}],
        )
        with (
            patch(_P_SECRET, return_value="ts-key"),
            patch(_P_RENDER, return_value="caddyfile content"),
            patch(_P_WRITE),
            patch(_P_CERT),
            patch(_P_NETWORK, return_value=("net123", "upstream123")),
            patch(_P_CREATE_EDGE, return_value="cid"),
            patch(_P_FIND_EDGE, return_value=None),
            patch(_P_START),
            patch(_P_TS_IP, return_value="100.64.0.1"),
            patch(_P_RELOAD) as mock_reload,
            patch(_P_AGGREGATE, return_value="healthy"),
            patch(_P_HEALTH, return_value={"edge_container_running": True}) as mock_health,
            patch(
                "app.edge.network_manager.reconcile_additional_edge_networks",
                side_effect=docker.errors.NotFound("no such network"),
            ),
        ):
            result = reconcile_service(db_session, svc)

        # Core steps ran before the auxiliary failure...
        mock_health.assert_called_once()
        mock_reload.assert_called_once()
        # ...and the missing network still surfaces loudly.
        assert result["phase"] == "failed"
