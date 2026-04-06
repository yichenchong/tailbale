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
    @patch("app.edge.container_manager.docker.DockerClient")
    def test_creates_container(self, mock_cls, tmp_path):
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

    @patch("app.edge.container_manager.docker.DockerClient")
    def test_creates_host_directories(self, mock_cls, tmp_path):
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

        assert (tmp_path / "certs" / "nextcloud.example.com").is_dir()
        assert (tmp_path / "tailscale" / "edge_nextcloud").is_dir()


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
        mock_container.exec_run.return_value = (0, b"100.64.0.1\n")
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.1"

    @patch("app.edge.container_manager.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_detects_ip_via_status_json(self, mock_find, mock_sleep):
        from app.edge.container_manager import detect_tailscale_ip

        mock_container = MagicMock()
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
        # Returns a non-Tailscale IP first, then status with Tailscale IP
        mock_container.exec_run.side_effect = [
            (0, b"192.168.1.1\n"),  # Not a 100.x IP
            (0, json.dumps({"Self": {"TailscaleIPs": ["100.64.0.5"]}}).encode()),
        ]
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.5"
