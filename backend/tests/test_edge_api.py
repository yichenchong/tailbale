"""Tests for the M4 edge action API endpoints (reload, restart, recreate, logs)."""

from unittest.mock import patch


def _create_service(client, **overrides):
    """Helper to create a service with defaults."""
    body = {
        "name": "Nextcloud",
        "upstream_container_id": "abc123def456",
        "upstream_container_name": "nextcloud",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "nextcloud.example.com",
        "base_domain": "example.com",
    }
    body.update(overrides)
    return client.post("/api/services", json=body)


class TestReloadEndpoint:
    @patch("app.edge.container_manager.reload_caddy")
    def test_reload_success(self, mock_reload, client):
        svc_id = _create_service(client).json()["id"]
        mock_reload.return_value = "config reloaded"

        resp = client.post(f"/api/services/{svc_id}/reload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "reloaded" in data["message"].lower()

    @patch("app.edge.container_manager.reload_caddy")
    def test_reload_failure(self, mock_reload, client):
        svc_id = _create_service(client).json()["id"]
        mock_reload.side_effect = RuntimeError("Edge container not found")

        resp = client.post(f"/api/services/{svc_id}/reload")
        assert resp.status_code == 500

    def test_reload_nonexistent_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/reload")
        assert resp.status_code == 404

    @patch("app.edge.container_manager.reload_caddy")
    def test_reload_emits_event(self, mock_reload, client, db_session):
        from app.models.event import Event

        svc_id = _create_service(client).json()["id"]
        mock_reload.return_value = "ok"

        client.post(f"/api/services/{svc_id}/reload")
        events = db_session.query(Event).filter(Event.kind == "caddy_reloaded").all()
        assert len(events) == 1


class TestRestartEdgeEndpoint:
    @patch("app.edge.container_manager.restart_edge")
    def test_restart_success(self, mock_restart, client):
        svc_id = _create_service(client).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/restart-edge")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_restart.assert_called_once()

    @patch("app.edge.container_manager.restart_edge")
    def test_restart_failure(self, mock_restart, client):
        svc_id = _create_service(client).json()["id"]
        mock_restart.side_effect = RuntimeError("Edge container not found")

        resp = client.post(f"/api/services/{svc_id}/restart-edge")
        assert resp.status_code == 500

    def test_restart_nonexistent_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/restart-edge")
        assert resp.status_code == 404

    @patch("app.edge.container_manager.restart_edge")
    def test_restart_emits_event(self, mock_restart, client, db_session):
        from app.models.event import Event

        svc_id = _create_service(client).json()["id"]
        client.post(f"/api/services/{svc_id}/restart-edge")
        events = db_session.query(Event).filter(Event.kind == "edge_restarted").all()
        assert len(events) == 1


class TestRecreateEdgeEndpoint:
    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager.recreate_edge")
    def test_recreate_success(self, mock_recreate, mock_secret, client):
        svc_id = _create_service(client).json()["id"]
        mock_secret.return_value = "tskey-auth-test"
        mock_recreate.return_value = "new_container_id"

        resp = client.post(f"/api/services/{svc_id}/recreate-edge")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["container_id"] == "new_container_id"

    @patch("app.secrets.read_secret")
    def test_recreate_no_authkey(self, mock_secret, client):
        svc_id = _create_service(client).json()["id"]
        mock_secret.return_value = None

        resp = client.post(f"/api/services/{svc_id}/recreate-edge")
        assert resp.status_code == 400
        assert "auth key" in resp.json()["detail"].lower()

    def test_recreate_nonexistent_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/recreate-edge")
        assert resp.status_code == 404

    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager.recreate_edge")
    def test_recreate_emits_event(self, mock_recreate, mock_secret, client, db_session):
        from app.models.event import Event

        svc_id = _create_service(client).json()["id"]
        mock_secret.return_value = "tskey-auth-test"
        mock_recreate.return_value = "new_id"

        client.post(f"/api/services/{svc_id}/recreate-edge")
        events = db_session.query(Event).filter(Event.kind == "edge_recreated").all()
        assert len(events) == 1

    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager.recreate_edge")
    def test_recreate_updates_status(self, mock_recreate, mock_secret, client):
        svc_id = _create_service(client).json()["id"]
        mock_secret.return_value = "tskey-auth-test"
        mock_recreate.return_value = "new_container_id"

        client.post(f"/api/services/{svc_id}/recreate-edge")

        # Check status via GET
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.json()["status"]["edge_container_id"] == "new_container_id"


class TestEdgeLogsEndpoint:
    @patch("app.edge.container_manager.get_edge_logs")
    def test_logs_success(self, mock_logs, client):
        svc_id = _create_service(client).json()["id"]
        mock_logs.return_value = "2026-04-05T00:00:00 [edge] Starting..."

        resp = client.get(f"/api/services/{svc_id}/logs/edge")
        assert resp.status_code == 200
        assert "Starting" in resp.json()["logs"]

    @patch("app.edge.container_manager.get_edge_logs")
    def test_logs_empty(self, mock_logs, client):
        svc_id = _create_service(client).json()["id"]
        mock_logs.return_value = ""

        resp = client.get(f"/api/services/{svc_id}/logs/edge")
        assert resp.status_code == 200
        assert resp.json()["logs"] == ""

    def test_logs_nonexistent_service(self, client):
        resp = client.get("/api/services/svc_nonexistent/logs/edge")
        assert resp.status_code == 404

    @patch("app.edge.container_manager.get_edge_logs")
    def test_logs_tail_parameter(self, mock_logs, client):
        svc_id = _create_service(client).json()["id"]
        mock_logs.return_value = "line"

        client.get(f"/api/services/{svc_id}/logs/edge?tail=50")
        mock_logs.assert_called_once()
        call_args = mock_logs.call_args
        assert call_args.kwargs.get("tail") == 50 or (len(call_args) > 1 and call_args[1].get("tail") == 50)
