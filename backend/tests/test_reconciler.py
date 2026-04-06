"""Tests for the reconciler engine."""

from pathlib import Path
from unittest.mock import MagicMock, patch

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
