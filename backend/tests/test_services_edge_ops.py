"""Edge-action endpoint tests: recreate-edge / update-edge job flow, the enabled-service guard, version precheck, async error mapping, and edge-version graceful degradation.

Mirrors app.services.edge_ops (split from test_services_api.py)."""

from unittest.mock import patch

from tests._services_helpers import (
    _create_service,
)


class TestDisabledServiceActionEndpoints:
    @patch("app.edge.caddy_admin.reload_caddy")
    def test_reload_rejects_disabled_service(self, mock_reload, client):
        svc_id = _create_service(client, enabled=False).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/reload")

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Service is disabled"
        mock_reload.assert_not_called()

    @patch("app.edge.container_manager.restart_edge")
    def test_restart_rejects_disabled_service(self, mock_restart, client):
        svc_id = _create_service(client, enabled=False).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/restart-edge")

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Service is disabled"
        mock_restart.assert_not_called()

    @patch("app.secrets.read_secret", return_value="ts-key")
    @patch("app.edge.container_manager.recreate_edge")
    def test_recreate_rejects_disabled_service(self, mock_recreate, mock_secret, client):
        svc_id = _create_service(client, enabled=False).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/recreate-edge")

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Service is disabled"
        mock_recreate.assert_not_called()

    @patch("app.edge.image_builder.ensure_edge_image")
    @patch("app.edge.container_manager.recreate_edge")
    @patch("app.edge.container_manager.get_edge_version", return_value="old")
    def test_update_edge_rejects_disabled_service(
        self, mock_version, mock_recreate, mock_build, client,
    ):
        svc_id = _create_service(client, enabled=False).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/update-edge")

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Service is disabled"
        mock_version.assert_not_called()
        mock_build.assert_not_called()
        mock_recreate.assert_not_called()


class TestUpdateEdgeFastPath:
    """update-edge short-circuits (no recreate) when already at target version."""

    @patch("app.edge.image_builder.ensure_edge_image")
    @patch("app.edge.container_manager.recreate_edge")
    def test_update_edge_already_current_returns_success(
        self, mock_recreate, mock_build, client,
    ):
        from app.version import __version__

        svc_id = _create_service(client).json()["id"]
        with patch(
            "app.edge.container_manager.get_edge_version", return_value=__version__
        ):
            resp = client.post(f"/api/services/{svc_id}/update-edge")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["version"] == __version__
        assert "already at version" in data["message"]
        assert "container_id" not in data
        mock_recreate.assert_not_called()
        mock_build.assert_not_called()


class TestEdgeActionMissingAuthKey:
    """recreate-edge and update-edge must report a missing Tailscale auth key
    identically: a clear, actionable 400 (not an opaque 500). Regression: update-edge
    raised RuntimeError, which the generic handler masked as a 500, while recreate-edge
    already returned 400 for the same condition."""

    @patch("app.edge.container_manager.recreate_edge")
    @patch("app.secrets.read_secret", return_value=None)
    def test_recreate_missing_authkey_returns_400(self, mock_secret, mock_recreate, client):
        svc_id = _create_service(client).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/recreate-edge")

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Tailscale auth key not configured"
        mock_recreate.assert_not_called()

    @patch("app.edge.image_builder.ensure_edge_image")
    @patch("app.edge.container_manager.recreate_edge")
    @patch("app.edge.container_manager.get_edge_version", return_value="old")
    @patch("app.secrets.read_secret", return_value=None)
    def test_update_edge_missing_authkey_returns_400(
        self, mock_secret, mock_version, mock_recreate, mock_build, client,
    ):
        svc_id = _create_service(client).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/update-edge")

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Tailscale auth key not configured"
        mock_build.assert_not_called()
        mock_recreate.assert_not_called()


class TestAsyncEdgeEndpointErrorMapping:
    """The async /reconcile and /update-edge endpoints must map a Docker-unavailable
    failure to 503 (matching the sync reload/restart/recreate endpoints via
    edge_action), not a generic 500 — while still returning 200 on success."""

    def test_reconcile_docker_unreachable_returns_503(self, client):
        import docker

        svc_id = _create_service(client).json()["id"]
        with patch(
            "app.reconciler.reconcile_loop.spawn_reconcile",
            side_effect=docker.errors.DockerException("unix:///run/docker.sock refused"),
        ):
            resp = client.post(f"/api/services/{svc_id}/reconcile")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Docker is unavailable"

    def test_reconcile_returns_200_on_success(self, client):
        svc_id = _create_service(client).json()["id"]
        with patch(
            "app.reconciler.reconcile_loop.spawn_reconcile",
            return_value={"phase": "ready", "error": None},
        ):
            resp = client.post(f"/api/services/{svc_id}/reconcile")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["phase"] == "ready"

    @patch("app.edge.image_builder.ensure_edge_image")
    @patch("app.edge.container_manager.recreate_edge")
    @patch("app.edge.container_manager.get_edge_version", return_value="old")
    @patch("app.secrets.read_secret", return_value="ts-key")
    def test_update_edge_docker_unreachable_returns_503(
        self, mock_secret, mock_version, mock_recreate, mock_build, client,
    ):
        import docker

        mock_recreate.side_effect = docker.errors.DockerException("DOCKER_HOST unreachable")
        svc_id = _create_service(client).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/update-edge")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Docker is unavailable"

    @patch("app.edge.image_builder.ensure_edge_image")
    @patch("app.edge.container_manager.recreate_edge", return_value="new_cid")
    @patch("app.edge.container_manager.get_edge_version", return_value="old")
    @patch("app.secrets.read_secret", return_value="ts-key")
    def test_update_edge_returns_200_on_success(
        self, mock_secret, mock_version, mock_recreate, mock_build, client,
    ):
        svc_id = _create_service(client).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/update-edge")

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["container_id"] == "new_cid"


class TestEdgeVersionDockerDownGracefulDegrade:
    """A Docker-unreachable daemon makes ``container_manager.get_edge_version``
    raise, but the endpoint suppresses it and reports edge_version=None at 200 —
    the deliberate read-vs-action asymmetry against the edge-logs 503 path. This
    pins it so a future "unify the edge endpoints to 503" refactor can't silently
    break the version-badge poll (existing tests only mock a None *return*, i.e.
    Docker-up-but-no-container, never the daemon-down *raise*)."""

    @patch("app.edge.container_manager.get_edge_version")
    def test_docker_unavailable_returns_200_with_null_version(self, mock_ver, client):
        import docker

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        mock_ver.side_effect = docker.errors.DockerException("daemon down")

        resp = client.get(f"/api/services/{svc_id}/edge-version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edge_version"] is None
        assert data["up_to_date"] is False
        assert "orchestrator_version" in data


class TestEdgeReadEndpointsNotFound:
    """The read-only edge-version endpoint guards existence (via
    get_service_for_edge_query) BEFORE the graceful Docker-error suppression, so
    a nonexistent service is a clean 404 — never a 200 with a null version from
    the suppress swallowing the ServiceNotFound."""

    def test_edge_version_nonexistent_service_returns_404(self, client):
        resp = client.get("/api/services/svc_nonexistent/edge-version")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Service not found"
