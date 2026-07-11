"""Orchestration tests for the reconciler engine."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.locks import _RECONCILE_LOCKS
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler.reconciler import reconcile_service
from tests._reconciler_helpers import (
    _P_AGGREGATE,
    _P_CERT,
    _P_CREATE_EDGE,
    _P_DNS,
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


class TestReconcileService:
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

    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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

    @patch(_P_SECRET)
    def test_deleted_service_while_locked_reports_deleted_and_forgets_lock(
        self, mock_secret, db_session, tmp_data_dir
    ):
        """A service deleted after this reconcile acquired (or while it waited
        for) its per-service reconcile lock resolves to None on the in-lock fresh
        read: reconcile_service must report phase='deleted' AND drop the
        registry entry that acquiring the lock re-created, so _RECONCILE_LOCKS
        stays bounded by live + in-flight ids. Sibling of the disabled-branch
        guard (above) and the health sweep's deleted-mid-sweep test, for the
        reconcile path itself."""
        mock_secret.return_value = "ts-key"
        svc = _create_service(db_session)
        service_id = svc.id
        # Drop the row (and its cascaded status) so the in-lock fresh read
        # resolves to None. Hand reconcile_service the pre-delete snapshot it
        # would have loaded before the delete landed — only its .id is read
        # before that fresh read fires.
        db_session.delete(svc)
        db_session.flush()
        stale = Service(id=service_id)
        assert service_id not in _RECONCILE_LOCKS  # clean precondition

        result = reconcile_service(db_session, stale)

        assert result["phase"] == "deleted"
        assert result["error"] is None
        # The registry entry that acquiring the lock re-created must be forgotten.
        assert service_id not in _RECONCILE_LOCKS

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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
    def test_naive_expiring_cert_timestamp_triggers_renewal(
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
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
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
