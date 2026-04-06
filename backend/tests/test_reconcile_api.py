"""Tests for the manual reconcile API endpoint."""

from unittest.mock import patch


class TestReconcileEndpoint:
    def _create_via_api(self, client, **overrides):
        body = {
            "name": "Nextcloud", "upstream_container_id": "abc123def456",
            "upstream_container_name": "nextcloud", "upstream_scheme": "http",
            "upstream_port": 80, "hostname": "nextcloud.example.com",
            "base_domain": "example.com",
        }
        body.update(overrides)
        return client.post("/api/services", json=body)

    @patch("app.reconciler.reconcile_loop.reconcile_service")
    def test_reconcile_returns_result(self, mock_reconcile, client):
        svc_id = self._create_via_api(client).json()["id"]
        mock_reconcile.return_value = {"phase": "healthy", "error": None}

        resp = client.post(f"/api/services/{svc_id}/reconcile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["phase"] == "healthy"

    @patch("app.reconciler.reconcile_loop.reconcile_service")
    def test_reconcile_returns_failed_phase(self, mock_reconcile, client):
        svc_id = self._create_via_api(client).json()["id"]
        mock_reconcile.return_value = {"phase": "failed", "error": "TS auth key missing"}

        resp = client.post(f"/api/services/{svc_id}/reconcile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["phase"] == "failed"
        assert data["error"] == "TS auth key missing"

    def test_reconcile_404_for_missing_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/reconcile")
        assert resp.status_code == 404

    def test_reconcile_replaces_501_stub(self, client):
        """The old 501 stub should no longer exist."""
        svc_id = self._create_via_api(client).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_service") as mock:
            mock.return_value = {"phase": "healthy", "error": None}
            resp = client.post(f"/api/services/{svc_id}/reconcile")
            assert resp.status_code != 501
