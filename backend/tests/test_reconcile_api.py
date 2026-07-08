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

    @patch("app.reconciler.reconcile_loop.reconcile_one")
    def test_reconcile_returns_result(self, mock_reconcile, client):
        svc_id = self._create_via_api(client).json()["id"]
        mock_reconcile.return_value = {"phase": "healthy", "error": None}

        resp = client.post(f"/api/services/{svc_id}/reconcile")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["phase"] == "healthy"

    @patch("app.reconciler.reconcile_loop.reconcile_one")
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
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock:
            mock.return_value = {"phase": "healthy", "error": None}
            resp = client.post(f"/api/services/{svc_id}/reconcile")
            assert resp.status_code != 501

    @patch("app.reconciler.reconcile_loop.spawn_reconcile")
    def test_reconcile_accepts_disabled_service(self, mock_spawn, client):
        """Manual /reconcile must NOT reject a disabled service the way the edge
        actions (reload/restart/recreate/update-edge) do. The reconciler
        intentionally has NO enabled-filter on the manual trigger — it honors a
        disable by converging to phase='disabled' rather than a 409, so the
        router guards existence only (_get_service_or_404), never enabled
        (get_enabled_service_for_edge_action). A regression swapping the guard
        would 409 before reconcile ever runs.
        """
        svc_id = self._create_via_api(client, enabled=False).json()["id"]
        mock_spawn.return_value = {"phase": "disabled", "error": None}

        resp = client.post(f"/api/services/{svc_id}/reconcile")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["phase"] == "disabled"
        # The guard passed the disabled service through to reconcile.
        mock_spawn.assert_called_once()
