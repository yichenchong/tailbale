"""Caddy reload and certificate-triggered reload tests for the reconciler."""

import os
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors

from app.models.service_status import ServiceStatus
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


class TestReconcileReload:
    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_TS_IP)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
