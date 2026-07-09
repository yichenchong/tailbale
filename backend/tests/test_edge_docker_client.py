"""Tests for Docker socket propagation and Docker client primitives."""

from unittest.mock import MagicMock, patch

import pytest

from app.edge import docker_client as dc
from app.edge.docker_client import close_client, connect
from app.settings_store import set_setting
from tests._services_helpers import create_service_api


def _create_service_via_api(client, **overrides):
    """Create a service through the API."""
    body = {
        "name": "App",
        "upstream_container_id": "abc123",
        "upstream_container_name": "app",
        "hostname": "app.example.com",
    }
    body.update(overrides)
    return create_service_api(client, **body)

class TestDockerSocketConsistency:
    """Verify that action endpoints pass the configured Docker socket to helpers."""

    @patch("app.edge.caddy_admin.reload_caddy")
    def test_reload_passes_socket(self, mock_reload, client, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_reload.return_value = "ok"
        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/reload")

        mock_reload.assert_called_once()
        call_args = mock_reload.call_args
        assert call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.restart_edge")
    def test_restart_passes_socket(self, mock_restart, client, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/restart-edge")

        mock_restart.assert_called_once()
        call_args = mock_restart.call_args
        assert call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.get_edge_logs")
    def test_logs_passes_socket(self, mock_logs, client, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_logs.return_value = "some logs"
        svc_id = _create_service_via_api(client).json()["id"]
        client.get(f"/api/services/{svc_id}/logs/edge")

        mock_logs.assert_called_once()
        call_kwargs = mock_logs.call_args
        assert call_kwargs.kwargs.get("socket_path") == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_passes_socket(self, mock_stop, client, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/disable")

        mock_stop.assert_called_once()
        call_args = mock_stop.call_args
        assert call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_passes_socket(self, mock_remove_edge, mock_remove_net, client, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        client.delete(f"/api/services/{svc_id}")

        mock_remove_edge.assert_called_once()
        assert mock_remove_edge.call_args[0][2] == "unix:///custom/docker.sock"
        mock_remove_net.assert_called_once()
        assert mock_remove_net.call_args[0][1] == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.get_edge_version")
    def test_edge_version_passes_socket(self, mock_ver, client, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_ver.return_value = "0.2.0"
        svc_id = _create_service_via_api(client).json()["id"]
        client.get(f"/api/services/{svc_id}/edge-version")

        mock_ver.assert_called_once()
        assert mock_ver.call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.reconciler.reconcile_loop.reconcile_one")
    def test_manual_reconcile_passes_socket(self, mock_reconcile, client, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_reconcile.return_value = {"phase": "healthy", "error": None}
        svc_id = _create_service_via_api(client).json()["id"]
        mock_reconcile.reset_mock()  # clear any calls from service creation
        client.post(f"/api/services/{svc_id}/reconcile")

        mock_reconcile.assert_called_once()
        assert mock_reconcile.call_args.kwargs.get("socket_path") == "unix:///custom/docker.sock"

    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager.recreate_edge")
    def test_recreate_passes_socket(self, mock_recreate, mock_secret, client, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_secret.return_value = "tskey-auth-test"
        mock_recreate.return_value = "new_id"

        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/recreate-edge")

        mock_recreate.assert_called_once()
        call_args = mock_recreate.call_args[0]
        assert call_args[-1] == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.get_edge_version", return_value="old")
    @patch("app.secrets.read_secret", return_value="tskey-auth-test")
    @patch("app.edge.image_builder.ensure_edge_image")
    @patch("app.edge.container_manager.recreate_edge")
    def test_update_edge_passes_socket(
        self, mock_recreate, mock_ensure_image, mock_secret, mock_version, client, db_session,
    ):

        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()
        mock_recreate.return_value = "new_id"

        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/update-edge")

        assert resp.status_code == 200
        mock_ensure_image.assert_called_once_with("unix:///custom/docker.sock")
        mock_recreate.assert_called_once()
        assert mock_recreate.call_args[0][-1] == "unix:///custom/docker.sock"

class TestDockerClientPrimitives:
    """ED3: ``connect``/``close_client``/``docker_client`` are the single
    daemon-selection + client-lifecycle policy for the whole app, but were only
    exercised indirectly (network_manager/image_builder patch DockerClient). Pin
    the two branches that matter: an explicit socket path dials
    ``DockerClient(base_url=...)`` while a blank one falls through to
    ``from_env()`` (so DOCKER_HOST is honored), and close is always best-effort."""

    @patch("app.edge.docker_client.docker.DockerClient")
    def test_connect_without_socket_uses_from_env(self, mock_cls):
        connect(None)

        mock_cls.from_env.assert_called_once_with()
        # The base_url constructor path must NOT be taken (that would ignore
        # DOCKER_HOST and pin the local unix socket).
        mock_cls.assert_not_called()

    @patch("app.edge.docker_client.docker.DockerClient")
    def test_connect_with_socket_uses_base_url(self, mock_cls):
        connect("tcp://10.0.0.1:2375")

        mock_cls.assert_called_once_with(base_url="tcp://10.0.0.1:2375")
        mock_cls.from_env.assert_not_called()

    def test_close_client_handles_none(self):
        # No client to close — must be a silent no-op, never an AttributeError.
        close_client(None)

    def test_close_client_swallows_close_error(self):
        client = MagicMock()
        client.close.side_effect = RuntimeError("socket already gone")

        # Best-effort: a failing close must never propagate out of a finally.
        close_client(client)
        client.close.assert_called_once()

    def test_close_client_tolerates_missing_close_attr(self):
        class _NoClose:
            pass

        # A client-like object without a ``close`` attribute must not blow up.
        close_client(_NoClose())

    def test_docker_client_closes_on_normal_exit(self):
        fake = MagicMock()
        with (
            patch.object(dc, "connect", return_value=fake),
            dc.docker_client("unix:///x.sock") as client,
        ):
            assert client is fake
        fake.close.assert_called_once()

    def test_docker_client_closes_on_body_exception(self):
        fake = MagicMock()
        with (
            patch.object(dc, "connect", return_value=fake),
            pytest.raises(ValueError, match="boom"),
            dc.docker_client(),
        ):
            raise ValueError("boom")
        fake.close.assert_called_once()
