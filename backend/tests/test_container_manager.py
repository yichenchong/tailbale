"""Tests for edge container lifecycle management and Tailscale IP detection."""

import json
from unittest.mock import MagicMock, patch

import docker.errors


def _make_service(**overrides):
    """Create a mock Service object."""
    svc = MagicMock()
    svc.id = overrides.get("id", "svc_abc123")
    svc.name = overrides.get("name", "Nextcloud")
    svc.hostname = overrides.get("hostname", "nextcloud.example.com")
    svc.upstream_container_name = overrides.get("upstream_container_name", "nextcloud")
    svc.upstream_port = overrides.get("upstream_port", 80)
    svc.upstream_scheme = overrides.get("upstream_scheme", "http")
    svc.preserve_host_header = overrides.get("preserve_host_header", True)
    svc.custom_caddy_snippet = overrides.get("custom_caddy_snippet", None)
    svc.edge_container_name = overrides.get("edge_container_name", "edge_nextcloud")
    svc.network_name = overrides.get("network_name", "edge_net_nextcloud")
    svc.ts_hostname = overrides.get("ts_hostname", "edge-nextcloud")
    return svc


class TestFindEdgeContainer:
    @patch("app.edge.container_manager.docker.DockerClient")
    def test_finds_by_name(self, mock_cls):
        from app.edge.container_manager import _find_edge_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_client.containers.get.return_value = mock_container

        result = _find_edge_container("svc_123", "edge_test")
        assert result == mock_container

    @patch("app.edge.container_manager.docker.DockerClient")
    def test_falls_back_to_label_search(self, mock_cls):
        from app.edge.container_manager import _find_edge_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_container = MagicMock()
        mock_client.containers.list.return_value = [mock_container]

        result = _find_edge_container("svc_123", "edge_test")
        assert result == mock_container
        mock_client.containers.list.assert_called_once_with(
            all=True, filters={"label": "tailbale.service_id=svc_123"}
        )

    @patch("app.edge.container_manager.docker.DockerClient")
    def test_returns_none_when_not_found(self, mock_cls):
        from app.edge.container_manager import _find_edge_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_client.containers.get.side_effect = docker.errors.NotFound("not found")
        mock_client.containers.list.return_value = []

        result = _find_edge_container("svc_123", "edge_test")
        assert result is None


class TestCreateEdgeContainer:
    @patch("app.edge.container_manager.ensure_edge_image")
    @patch("app.edge.container_manager.docker.DockerClient")
    def test_creates_container(self, mock_cls, mock_ensure, tmp_path):
        from app.edge.container_manager import create_edge_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_container.id = "new_container_id"
        mock_client.containers.create.return_value = mock_container

        svc = _make_service()

        # Create Caddyfile for the mount
        caddyfile_dir = tmp_path / "generated" / svc.id
        caddyfile_dir.mkdir(parents=True)
        (caddyfile_dir / "Caddyfile").write_text("test")

        result = create_edge_container(
            svc,
            ts_authkey="tskey-auth-test",
            generated_dir=tmp_path / "generated",
            certs_dir=tmp_path / "certs",
            tailscale_state_dir=tmp_path / "tailscale",
        )

        assert result == "new_container_id"
        mock_client.containers.create.assert_called_once()

        # Verify call args
        call_kwargs = mock_client.containers.create.call_args
        assert call_kwargs.kwargs["name"] == "edge_nextcloud"
        assert call_kwargs.kwargs["network"] == "edge_net_nextcloud"
        assert call_kwargs.kwargs["labels"]["tailbale.managed"] == "true"
        assert call_kwargs.kwargs["labels"]["tailbale.service_id"] == "svc_abc123"
        assert call_kwargs.kwargs["environment"]["TS_AUTHKEY"] == "tskey-auth-test"
        assert call_kwargs.kwargs["environment"]["TS_HOSTNAME"] == "edge-nextcloud"

    @patch("app.edge.container_manager.ensure_edge_image")
    @patch("app.edge.container_manager.docker.DockerClient")
    def test_uses_host_paths_for_mounts(self, mock_cls, mock_ensure, tmp_path):
        """Directories are created by the reconciler before this is called.

        This test verifies that the provided paths are used for Docker
        bind mounts without the function trying to create them itself.
        """
        from app.edge.container_manager import create_edge_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_client.containers.create.return_value = MagicMock(id="c1")

        svc = _make_service()

        create_edge_container(
            svc,
            ts_authkey="tskey-auth-test",
            generated_dir=tmp_path / "generated",
            certs_dir=tmp_path / "certs",
            tailscale_state_dir=tmp_path / "tailscale",
        )

        # Verify mounts use the provided paths
        call_kwargs = mock_client.containers.create.call_args
        mounts = call_kwargs.kwargs["mounts"]
        sources = [m["Source"] for m in mounts]
        assert str(tmp_path / "generated" / "svc_abc123" / "Caddyfile") in sources
        assert str(tmp_path / "certs" / "nextcloud.example.com") in sources
        assert str(tmp_path / "tailscale" / "edge_nextcloud") in sources


class TestStartEdge:
    @patch("app.edge.container_manager._find_edge_container")
    def test_starts_container(self, mock_find):
        from app.edge.container_manager import start_edge

        mock_container = MagicMock()
        mock_find.return_value = mock_container

        start_edge("svc_123", "edge_test")
        mock_container.start.assert_called_once()

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_if_not_found(self, mock_find):
        from app.edge.container_manager import start_edge
        import pytest

        mock_find.return_value = None
        with pytest.raises(RuntimeError, match="Edge container not found"):
            start_edge("svc_123", "edge_test")


class TestStopEdge:
    @patch("app.edge.container_manager._find_edge_container")
    def test_stops_container(self, mock_find):
        from app.edge.container_manager import stop_edge

        mock_container = MagicMock()
        mock_find.return_value = mock_container

        stop_edge("svc_123", "edge_test")
        mock_container.stop.assert_called_once_with(timeout=10)

    @patch("app.edge.container_manager._find_edge_container")
    def test_noop_if_not_found(self, mock_find):
        from app.edge.container_manager import stop_edge

        mock_find.return_value = None
        # Should not raise
        stop_edge("svc_123", "edge_test")


class TestRestartEdge:
    @patch("app.edge.container_manager._find_edge_container")
    def test_restarts_container(self, mock_find):
        from app.edge.container_manager import restart_edge

        mock_container = MagicMock()
        mock_find.return_value = mock_container

        restart_edge("svc_123", "edge_test")
        mock_container.restart.assert_called_once_with(timeout=10)

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_if_not_found(self, mock_find):
        from app.edge.container_manager import restart_edge
        import pytest

        mock_find.return_value = None
        with pytest.raises(RuntimeError, match="Edge container not found"):
            restart_edge("svc_123", "edge_test")


class TestRemoveEdge:
    @patch("app.edge.container_manager._find_edge_container")
    def test_removes_container(self, mock_find):
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_find.return_value = mock_container

        remove_edge("svc_123", "edge_test")
        mock_container.remove.assert_called_once_with(force=True)

    @patch("app.edge.container_manager._find_edge_container")
    def test_noop_if_not_found(self, mock_find):
        from app.edge.container_manager import remove_edge

        mock_find.return_value = None
        # Should not raise
        remove_edge("svc_123", "edge_test")


class TestRecreateEdge:
    @patch("app.edge.container_manager.start_edge")
    @patch("app.edge.container_manager.create_edge_container")
    @patch("app.edge.container_manager.remove_edge")
    def test_removes_creates_starts(self, mock_remove, mock_create, mock_start, tmp_path):
        from app.edge.container_manager import recreate_edge

        mock_create.return_value = "new_id"
        svc = _make_service()

        result = recreate_edge(
            svc,
            ts_authkey="tskey-auth-test",
            generated_dir=tmp_path / "generated",
            certs_dir=tmp_path / "certs",
            tailscale_state_dir=tmp_path / "tailscale",
        )

        assert result == "new_id"
        mock_remove.assert_called_once_with(svc.id, svc.edge_container_name, None)
        mock_create.assert_called_once()
        mock_start.assert_called_once_with(svc.id, svc.edge_container_name, None)


class TestGetEdgeLogs:
    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_logs(self, mock_find):
        from app.edge.container_manager import get_edge_logs

        mock_container = MagicMock()
        mock_container.logs.return_value = b"2026-04-05T00:00:00 [edge] Starting..."
        mock_find.return_value = mock_container

        result = get_edge_logs("svc_123", "edge_test", tail=50)

        assert "Starting" in result
        mock_container.logs.assert_called_once_with(tail=50, timestamps=True)

    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_empty_if_not_found(self, mock_find):
        from app.edge.container_manager import get_edge_logs

        mock_find.return_value = None
        result = get_edge_logs("svc_123", "edge_test")
        assert result == ""


class TestReloadCaddy:
    @patch("app.edge.container_manager._find_edge_container")
    def test_reloads_caddy(self, mock_find):
        from app.edge.container_manager import reload_caddy

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"config reloaded")
        mock_find.return_value = mock_container

        result = reload_caddy("svc_123", "edge_test")

        assert "reloaded" in result
        mock_container.exec_run.assert_called_once_with(
            "caddy reload --config /etc/caddy/Caddyfile"
        )

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_on_failure(self, mock_find):
        from app.edge.container_manager import reload_caddy
        import pytest

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (1, b"error: invalid config")
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError, match="Caddy reload failed"):
            reload_caddy("svc_123", "edge_test")

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_if_not_found(self, mock_find):
        from app.edge.container_manager import reload_caddy
        import pytest

        mock_find.return_value = None
        with pytest.raises(RuntimeError, match="Edge container not found"):
            reload_caddy("svc_123", "edge_test")


class TestDetectTailscaleIp:
    @patch("app.edge.container_manager.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_detects_ip_via_tailscale_ip(self, mock_find, mock_sleep):
        from app.edge.container_manager import detect_tailscale_ip

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"100.64.0.1\n")
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.1"

    @patch("app.edge.container_manager.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_detects_ip_via_status_json(self, mock_find, mock_sleep):
        from app.edge.container_manager import detect_tailscale_ip

        mock_container = MagicMock()
        mock_container.status = "running"
        # tailscale ip -4 fails
        status_json = json.dumps({
            "Self": {
                "TailscaleIPs": ["100.64.0.2", "fd7a::1"]
            }
        }).encode()
        mock_container.exec_run.side_effect = [
            (1, b""),  # tailscale ip fails
            (0, status_json),  # tailscale status --json succeeds
        ]
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.2"

    @patch("app.edge.container_manager.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_retries_on_failure(self, mock_find, mock_sleep):
        from app.edge.container_manager import detect_tailscale_ip

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = [
            (1, b""),  # attempt 1: tailscale ip fails
            (1, b""),  # attempt 1: tailscale status fails
            (0, b"100.64.0.3\n"),  # attempt 2: tailscale ip succeeds
        ]
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=2, retry_delay=0)
        assert result == "100.64.0.3"

    @patch("app.edge.container_manager.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_after_max_retries(self, mock_find, mock_sleep):
        from app.edge.container_manager import detect_tailscale_ip

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (1, b"not ready")
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=2, retry_delay=0)
        assert result is None

    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_if_container_not_found(self, mock_find):
        from app.edge.container_manager import detect_tailscale_ip

        mock_find.return_value = None
        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result is None

    @patch("app.edge.container_manager.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_ignores_non_tailscale_ips(self, mock_find, mock_sleep):
        from app.edge.container_manager import detect_tailscale_ip

        mock_container = MagicMock()
        mock_container.status = "running"
        # Returns a non-Tailscale IP first, then status with Tailscale IP
        mock_container.exec_run.side_effect = [
            (0, b"192.168.1.1\n"),  # Not a 100.x IP
            (0, json.dumps({"Self": {"TailscaleIPs": ["100.64.0.5"]}}).encode()),
        ]
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.5"


# ---------------------------------------------------------------------------
# Docker socket consistency across endpoints
# ---------------------------------------------------------------------------


def _create_service_via_api(client, **overrides):
    """Create a service through the API."""
    body = {
        "name": "App",
        "upstream_container_id": "abc123",
        "upstream_container_name": "app",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "app.example.com",
        "base_domain": "example.com",
    }
    body.update(overrides)
    return client.post("/api/services", json=body)


class TestDockerSocketConsistency:
    """Verify that action endpoints pass the configured Docker socket to helpers."""

    @patch("app.edge.container_manager.reload_caddy")
    def test_reload_passes_socket(self, mock_reload, client, db_session):
        from app.settings_store import set_setting
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
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/restart-edge")

        mock_restart.assert_called_once()
        call_args = mock_restart.call_args
        assert call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.get_edge_logs")
    def test_logs_passes_socket(self, mock_logs, client, db_session):
        from app.settings_store import set_setting
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
        from app.settings_store import set_setting
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
        from app.settings_store import set_setting
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
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_ver.return_value = "0.2.0"
        svc_id = _create_service_via_api(client).json()["id"]
        client.get(f"/api/services/{svc_id}/edge-version")

        mock_ver.assert_called_once()
        assert mock_ver.call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.reconciler.reconcile_loop.reconcile_one")
    def test_manual_reconcile_passes_socket(self, mock_reconcile, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_reconcile.return_value = {"phase": "healthy", "error": None}
        svc_id = _create_service_via_api(client).json()["id"]
        mock_reconcile.reset_mock()  # clear any calls from service creation
        client.post(f"/api/services/{svc_id}/reconcile")

        mock_reconcile.assert_called_once()
        assert mock_reconcile.call_args.kwargs.get("socket_path") == "unix:///custom/docker.sock"

    def test_default_socket_returns_default_value(self, db_session):
        from app.routers.services import _get_docker_socket
        result = _get_docker_socket(db_session)
        assert result == "unix:///var/run/docker.sock"

    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager.recreate_edge")
    def test_recreate_passes_socket(self, mock_recreate, mock_secret, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_secret.return_value = "tskey-auth-test"
        mock_recreate.return_value = "new_id"

        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/recreate-edge")

        mock_recreate.assert_called_once()
        call_args = mock_recreate.call_args[0]
        assert call_args[-1] == "unix:///custom/docker.sock"


# ---------------------------------------------------------------------------
# String-vs-Path acceptance
# ---------------------------------------------------------------------------


class TestStringPathAcceptance:
    """create_edge_container and recreate_edge should accept both str and Path."""

    @patch("app.edge.container_manager.ensure_edge_image")
    @patch("app.edge.container_manager._get_client")
    def test_create_accepts_strings(self, mock_get_client, mock_ensure, tmp_path):
        from app.edge.container_manager import create_edge_container

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.containers.create.return_value = MagicMock(id="c1")

        svc = MagicMock()
        svc.id = "svc_1"
        svc.hostname = "app.example.com"
        svc.edge_container_name = "edge_app"
        svc.network_name = "edge_net_app"
        svc.ts_hostname = "edge-app"

        result = create_edge_container(
            svc, "tskey-test",
            str(tmp_path / "gen"), str(tmp_path / "certs"), str(tmp_path / "ts"),
        )
        assert result == "c1"
        mock_client.containers.create.assert_called_once()

    @patch("app.edge.container_manager.ensure_edge_image")
    @patch("app.edge.container_manager._get_client")
    def test_create_accepts_paths(self, mock_get_client, mock_ensure, tmp_path):
        from app.edge.container_manager import create_edge_container

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.containers.create.return_value = MagicMock(id="c2")

        svc = MagicMock()
        svc.id = "svc_2"
        svc.hostname = "app.example.com"
        svc.edge_container_name = "edge_app"
        svc.network_name = "edge_net_app"
        svc.ts_hostname = "edge-app"

        result = create_edge_container(
            svc, "tskey-test",
            tmp_path / "gen", tmp_path / "certs", tmp_path / "ts",
        )
        assert result == "c2"

    @patch("app.edge.container_manager.start_edge")
    @patch("app.edge.container_manager.create_edge_container", return_value="c3")
    @patch("app.edge.container_manager.remove_edge")
    def test_recreate_accepts_strings(self, mock_rm, mock_create, mock_start, tmp_path):
        from app.edge.container_manager import recreate_edge

        svc = MagicMock()
        svc.id = "svc_3"
        svc.edge_container_name = "edge_app"

        result = recreate_edge(
            svc, "tskey-test",
            str(tmp_path / "gen"), str(tmp_path / "certs"), str(tmp_path / "ts"),
        )
        assert result == "c3"

    def test_type_hints_accept_str_or_path(self):
        import inspect
        from app.edge.container_manager import create_edge_container, recreate_edge

        sig_create = inspect.signature(create_edge_container)
        sig_recreate = inspect.signature(recreate_edge)
        for param_name in ("generated_dir", "certs_dir", "tailscale_state_dir"):
            annotation = str(sig_create.parameters[param_name].annotation)
            assert "str" in annotation and "Path" in annotation, \
                f"create_edge_container.{param_name} should accept str | Path"
            annotation_r = str(sig_recreate.parameters[param_name].annotation)
            assert "str" in annotation_r and "Path" in annotation_r, \
                f"recreate_edge.{param_name} should accept str | Path"
