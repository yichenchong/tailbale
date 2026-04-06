"""Tests for reconcile loop and trigger helpers."""

from unittest.mock import patch

from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler.reconcile_loop import reconcile_all, reconcile_one


def _create_service(db, name="TestApp", enabled=True, **overrides):
    slug = name.lower().replace(" ", "")
    defaults = {
        "name": name, "upstream_container_id": "abc123",
        "upstream_container_name": slug, "upstream_scheme": "http",
        "upstream_port": 80, "hostname": f"{slug}.example.com",
        "base_domain": "example.com", "edge_container_name": f"edge_{slug}",
        "network_name": f"edge_net_{slug}", "ts_hostname": f"edge-{slug}",
        "enabled": enabled,
    }
    defaults.update(overrides)
    svc = Service(**defaults)
    db.add(svc)
    db.flush()
    db.add(ServiceStatus(service_id=svc.id, phase="pending"))
    db.commit()
    return svc


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


class TestReconcileOne:
    @patch("app.reconciler.reconcile_loop.reconcile_service")
    def test_reconciles_single_service(self, mock_reconcile, db_session):
        svc = _create_service(db_session)
        mock_reconcile.return_value = {"phase": "healthy", "tailscale_ip": "100.64.0.1"}

        result = reconcile_one(db_session, svc.id)
        assert result["phase"] == "healthy"
        mock_reconcile.assert_called_once()

    def test_raises_for_missing_service(self, db_session):
        import pytest
        with pytest.raises(ValueError, match="not found"):
            reconcile_one(db_session, "svc_nonexistent")
