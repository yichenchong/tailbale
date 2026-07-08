"""Tests for edge container lifecycle management and Tailscale IP detection."""

import json
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from app.edge.container_manager import _wait_for_running
from app.edge.tailscale_ops import detect_tailscale_ip


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
    svc.custom_caddy_snippet = overrides.get("custom_caddy_snippet")
    svc.edge_container_name = overrides.get("edge_container_name", "edge_nextcloud")
    svc.network_name = overrides.get("network_name", "edge_net_nextcloud")
    svc.ts_hostname = overrides.get("ts_hostname", "edge-nextcloud")
    return svc


class _ConnectStubMixin:
    """Stub the under-test-blocked ``connect`` with a throwaway client.

    These lifecycle helpers route through ``_find_edge_container_for_use``, which
    opens a Docker client via ``connect`` directly (the masking fallback is gone).
    The conftest blocks real Docker access and the tests mock the container
    *lookup*, so a stand-in client is all that's needed to flow through and close.
    """

    @pytest.fixture(autouse=True)
    def _stub_connect(self):
        with patch("app.edge.container_manager.connect", return_value=MagicMock()):
            yield


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
    def test_ignores_name_match_for_different_service_label(self, mock_cls):
        from app.edge.container_manager import _find_edge_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        wrong_container = MagicMock()
        wrong_container.labels = {"tailbale.service_id": "svc_other"}
        mock_client.containers.get.return_value = wrong_container
        mock_client.containers.list.return_value = []

        result = _find_edge_container("svc_123", "edge_test")

        assert result is None
        mock_client.containers.list.assert_called_once_with(
            all=True, filters={"label": "tailbale.service_id=svc_123"}
        )

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


class TestContainerServiceId:
    """Direct coverage of the now-public label helpers (consumed by the health
    checker) and the EDG3 guard against a non-dict ``labels`` attribute."""

    def test_returns_label_value(self):
        from app.edge.container_manager import container_service_id

        container = MagicMock()
        container.labels = {"tailbale.service_id": "svc_123"}
        assert container_service_id(container) == "svc_123"

    def test_returns_none_when_label_absent(self):
        from app.edge.container_manager import container_service_id

        container = MagicMock()
        container.labels = {"tailbale.managed": "true"}
        assert container_service_id(container) is None

    def test_returns_none_when_labels_not_dict(self):
        """EDG3: a container whose ``labels`` is not a dict (e.g. None) must not
        blow up ``.get`` — the guard returns None."""
        from app.edge.container_manager import container_service_id

        container = MagicMock()
        container.labels = None
        assert container_service_id(container) is None

    def test_is_container_for_service_matrix(self):
        from app.edge.container_manager import is_container_for_service

        match = MagicMock()
        match.labels = {"tailbale.service_id": "svc_123"}
        assert is_container_for_service(match, "svc_123") is True

        other = MagicMock()
        other.labels = {"tailbale.service_id": "svc_other"}
        assert is_container_for_service(other, "svc_123") is False

        # An unlabelled (legacy) container is tolerated as a match.
        legacy = MagicMock()
        legacy.labels = {}
        assert is_container_for_service(legacy, "svc_123") is True

        # Non-dict labels fall through the guard and are treated as a match.
        broken = MagicMock()
        broken.labels = None
        assert is_container_for_service(broken, "svc_123") is True


class TestFindEdgeContainerPublic:
    """``find_edge_container`` operates on an already-open client (the health
    checker's usage pattern), distinct from the client-owning ``_find_*`` wrapper."""

    def test_returns_named_container_for_service(self):
        from app.edge.container_manager import find_edge_container

        client = MagicMock()
        container = MagicMock()
        container.labels = {"tailbale.service_id": "svc_123"}
        client.containers.get.return_value = container

        assert find_edge_container(client, "svc_123", "edge_test") is container
        client.containers.list.assert_not_called()

    def test_ignores_named_container_of_other_service_and_label_searches(self):
        from app.edge.container_manager import find_edge_container

        client = MagicMock()
        wrong = MagicMock()
        wrong.labels = {"tailbale.service_id": "svc_other"}
        client.containers.get.return_value = wrong
        labelled = MagicMock()
        client.containers.list.return_value = [labelled]

        result = find_edge_container(client, "svc_123", "edge_test")

        assert result is labelled
        client.containers.list.assert_called_once_with(
            all=True, filters={"label": "tailbale.service_id=svc_123"}
        )

    def test_non_notfound_lookup_error_propagates_by_default(self):
        # Lifecycle callers (the default) must NOT mistake a transient daemon
        # fault for "container absent": a non-NotFound error on the named lookup
        # propagates instead of silently falling through to the label search
        # (which could resolve a stale/other container and drive a duplicate).
        from app.edge.container_manager import find_edge_container

        client = MagicMock()
        client.containers.get.side_effect = docker.errors.APIError("500 daemon boom")

        with pytest.raises(docker.errors.APIError, match="daemon boom"):
            find_edge_container(client, "svc_123", "edge_test")
        client.containers.list.assert_not_called()

    def test_tolerate_lookup_errors_falls_back_to_label_search(self):
        # Health path (tolerate_lookup_errors=True): a transient non-NotFound
        # fault on the named lookup must degrade to the label search (matching the
        # pre-refactor _find_edge_container_for_health broad-catch), so a blip does
        # not spuriously report the edge missing and escalate it unhealthy.
        from app.edge.container_manager import find_edge_container

        client = MagicMock()
        client.containers.get.side_effect = docker.errors.APIError("500 daemon boom")
        labelled = MagicMock()
        client.containers.list.return_value = [labelled]

        result = find_edge_container(
            client, "svc_123", "edge_test", tolerate_lookup_errors=True
        )

        assert result is labelled
        client.containers.list.assert_called_once_with(
            all=True, filters={"label": "tailbale.service_id=svc_123"}
        )

    def test_tolerate_lookup_errors_does_not_swallow_label_list_error(self):
        # Even in tolerant mode, an error from the label ``list`` itself still
        # propagates: the pre-refactor helper only broadened the named-get step
        # and let a failing fallback surface (a truly-down daemon is not health).
        from app.edge.container_manager import find_edge_container

        client = MagicMock()
        client.containers.get.side_effect = docker.errors.APIError("named boom")
        client.containers.list.side_effect = docker.errors.APIError("list boom")

        with pytest.raises(docker.errors.APIError, match="list boom"):
            find_edge_container(
                client, "svc_123", "edge_test", tolerate_lookup_errors=True
            )


class TestFindEdgeContainerForUse:
    """Connection failures must propagate, not be masked by a fallback re-search."""

    @patch("app.edge.container_manager._find_edge_container")
    @patch("app.edge.container_manager.connect")
    def test_socket_failure_propagates(self, mock_connect, mock_find):
        from app.edge.container_manager import _find_edge_container_for_use

        mock_connect.side_effect = docker.errors.DockerException("cannot connect to socket")

        with pytest.raises(docker.errors.DockerException, match="cannot connect"):
            _find_edge_container_for_use("svc_123", "edge_test", "unix:///nonexistent.sock")

        # The masking fallback re-search is gone: the connect error surfaces
        # directly instead of being swallowed and retried against the same socket.
        mock_find.assert_not_called()

    @patch("app.edge.container_manager._find_edge_container")
    @patch("app.edge.container_manager.connect")
    def test_caller_surfaces_socket_failure(self, mock_connect, mock_find):
        from app.edge.container_manager import stop_edge

        mock_connect.side_effect = docker.errors.DockerException("socket unreachable")
        # A fallback lookup would report "nothing to stop"; a connect failure must
        # surface instead of degrading to a silent best-effort no-op.
        mock_find.return_value = None

        with pytest.raises(docker.errors.DockerException, match="socket unreachable"):
            stop_edge("svc_123", "edge_test", "unix:///nonexistent.sock")


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
        assert str(tmp_path / "generated" / "svc_abc123") in sources
        assert str(tmp_path / "certs" / "nextcloud.example.com") in sources
        assert str(tmp_path / "tailscale" / "edge_nextcloud") in sources


class TestStartEdge(_ConnectStubMixin):
    @patch("app.edge.container_manager._find_edge_container")
    def test_starts_container(self, mock_find):
        from app.edge.container_manager import start_edge

        mock_container = MagicMock()
        mock_find.return_value = mock_container

        start_edge("svc_123", "edge_test")
        mock_container.start.assert_called_once()

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_if_not_found(self, mock_find):
        import pytest

        from app.edge.container_manager import start_edge

        mock_find.return_value = None
        with pytest.raises(RuntimeError, match="Edge container not found"):
            start_edge("svc_123", "edge_test")


class TestStopEdge(_ConnectStubMixin):
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


class TestRestartEdge(_ConnectStubMixin):
    @patch("app.edge.container_manager._find_edge_container")
    def test_restarts_container(self, mock_find):
        from app.edge.container_manager import restart_edge

        mock_container = MagicMock()
        mock_find.return_value = mock_container

        restart_edge("svc_123", "edge_test")
        mock_container.restart.assert_called_once_with(timeout=10)

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_if_not_found(self, mock_find):
        import pytest

        from app.edge.container_manager import restart_edge

        mock_find.return_value = None
        with pytest.raises(RuntimeError, match="Edge container not found"):
            restart_edge("svc_123", "edge_test")


class TestRemoveEdge(_ConnectStubMixin):
    @patch("app.edge.container_manager._find_edge_container")
    def test_removes_container(self, mock_find):
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_container.status = "exited"  # not running — skips API call
        mock_find.return_value = mock_container

        remove_edge("svc_123", "edge_test")
        mock_container.remove.assert_called_once_with(force=True)

    @patch("app.edge.container_manager._find_edge_container")
    def test_noop_if_not_found(self, mock_find):
        from app.edge.container_manager import remove_edge

        mock_find.return_value = None
        # Should not raise
        remove_edge("svc_123", "edge_test")

    @patch("app.edge.container_manager._delete_tailscale_device")
    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager._find_edge_container")
    def test_calls_tailscale_api_on_removal(self, mock_find, mock_read_secret, mock_delete):
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_container.status = "running"
        status_json = json.dumps({"Self": {"ID": "node123abc"}}).encode()
        mock_container.exec_run.return_value = (0, status_json)
        mock_find.return_value = mock_container
        mock_read_secret.return_value = "tskey-api-mykey"
        mock_delete.return_value = True

        remove_edge("svc_123", "edge_test")

        mock_delete.assert_called_once_with("node123abc", "tskey-api-mykey")
        mock_container.remove.assert_called_once_with(force=True)

    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager._find_edge_container")
    def test_skips_api_when_no_api_key(self, mock_find, mock_read_secret):
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_container.status = "running"
        status_json = json.dumps({"Self": {"ID": "node123abc"}}).encode()
        mock_container.exec_run.return_value = (0, status_json)
        mock_find.return_value = mock_container
        mock_read_secret.return_value = None

        remove_edge("svc_123", "edge_test")
        # Should still remove the container even without API key
        mock_container.remove.assert_called_once_with(force=True)

    @patch("app.edge.container_manager._find_edge_container")
    def test_remove_edge_ignores_missing_container_on_remove(self, mock_find):
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.remove.side_effect = docker.errors.NotFound("gone")
        mock_find.return_value = mock_container

        remove_edge("svc_123", "edge_test")

    @patch("app.edge.container_manager._delete_tailscale_device")
    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager._find_edge_container")
    def test_delete_device_false_skips_device_deletion(
        self, mock_find, mock_read_secret, mock_delete
    ):
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_container.status = "running"
        status_json = json.dumps({"Self": {"ID": "node123abc"}}).encode()
        mock_container.exec_run.return_value = (0, status_json)
        mock_find.return_value = mock_container
        mock_read_secret.return_value = "tskey-api-mykey"

        remove_edge("svc_123", "edge_test", delete_device=False)

        # Identity-preserving swap: the tailnet device must NOT be deleted.
        mock_delete.assert_not_called()
        mock_container.remove.assert_called_once_with(force=True)

    @patch("app.edge.container_manager._find_edge_container")
    def test_raise_on_error_reraises_api_error(self, mock_find):
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.remove.side_effect = docker.errors.APIError("boom")
        mock_find.return_value = mock_container

        with pytest.raises(docker.errors.APIError):
            remove_edge("svc_123", "edge_test", raise_on_error=True)

    @patch("app.edge.container_manager._find_edge_container")
    def test_default_swallows_api_error(self, mock_find):
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.remove.side_effect = docker.errors.APIError("boom")
        mock_find.return_value = mock_container

        # Default is best-effort: the APIError is logged and swallowed.
        remove_edge("svc_123", "edge_test")
        mock_container.remove.assert_called_once_with(force=True)

    @patch("app.edge.container_manager._delete_tailscale_device")
    @patch("app.edge.container_manager._find_edge_container")
    def test_skips_device_deletion_when_not_running(self, mock_find, mock_delete):
        """A non-running container can't be exec'd for its node id, so device
        deletion is skipped entirely (no exec, no API call) — but the container
        is still removed."""
        from app.edge.container_manager import remove_edge

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_find.return_value = mock_container

        remove_edge("svc_123", "edge_test")

        mock_container.exec_run.assert_not_called()
        mock_delete.assert_not_called()
        mock_container.remove.assert_called_once_with(force=True)


class TestRecreateEdge(_ConnectStubMixin):
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
        mock_remove.assert_called_once_with(
            svc.id, svc.edge_container_name, None, delete_device=False, raise_on_error=True
        )
        mock_create.assert_called_once()
        mock_start.assert_called_once_with(svc.id, svc.edge_container_name, None)

    @patch("app.edge.container_manager.start_edge")
    @patch("app.edge.container_manager.create_edge_container")
    @patch("app.edge.container_manager._delete_tailscale_device")
    @patch("app.edge.container_manager._find_edge_container")
    def test_recreate_preserves_tailscale_device(
        self, mock_find, mock_delete, mock_create, mock_start, tmp_path
    ):
        """recreate_edge swaps the container but keeps the tailnet identity, so
        the Tailscale device must not be deleted even for a running container."""
        from app.edge.container_manager import recreate_edge

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_find.return_value = mock_container
        mock_create.return_value = "new_id"

        recreate_edge(
            _make_service(),
            ts_authkey="tskey-auth-test",
            generated_dir=tmp_path / "generated",
            certs_dir=tmp_path / "certs",
            tailscale_state_dir=tmp_path / "tailscale",
        )

        mock_delete.assert_not_called()
        mock_container.remove.assert_called_once_with(force=True)

    @patch("app.edge.container_manager.start_edge")
    @patch("app.edge.container_manager.create_edge_container")
    @patch("app.edge.container_manager._find_edge_container")
    def test_recreate_propagates_removal_failure(
        self, mock_find, mock_create, mock_start, tmp_path
    ):
        """A docker APIError during removal aborts recreate (raise_on_error=True)
        so we never create a new container over a stale name (opaque 409)."""
        from app.edge.container_manager import recreate_edge

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_container.remove.side_effect = docker.errors.APIError("name conflict")
        mock_find.return_value = mock_container

        with pytest.raises(docker.errors.APIError):
            recreate_edge(
                _make_service(),
                ts_authkey="tskey-auth-test",
                generated_dir=tmp_path / "generated",
                certs_dir=tmp_path / "certs",
                tailscale_state_dir=tmp_path / "tailscale",
            )

        mock_create.assert_not_called()
        mock_start.assert_not_called()


class TestGetEdgeLogs(_ConnectStubMixin):
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


class TestGetEdgeVersion:
    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_version_label(self, mock_find):
        from app.edge.container_manager import get_edge_version

        mock_container = MagicMock()
        mock_container.labels = {"tailbale.version": "1.2.3"}
        mock_find.return_value = mock_container

        assert get_edge_version("svc_123", "edge_test") == "1.2.3"

    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_if_not_found(self, mock_find):
        from app.edge.container_manager import get_edge_version

        mock_find.return_value = None
        assert get_edge_version("svc_123", "edge_test") is None

    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_when_label_absent(self, mock_find):
        from app.edge.container_manager import get_edge_version

        mock_container = MagicMock()
        mock_container.labels = {"tailbale.managed": "true"}
        mock_find.return_value = mock_container

        assert get_edge_version("svc_123", "edge_test") is None

    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_when_labels_not_dict(self, mock_find):
        from app.edge.container_manager import get_edge_version

        mock_container = MagicMock()
        mock_container.labels = None
        mock_find.return_value = mock_container

        assert get_edge_version("svc_123", "edge_test") is None


class TestReloadCaddy(_ConnectStubMixin):
    @patch("app.edge.container_manager._find_edge_container")
    def test_reloads_caddy(self, mock_find):
        from app.edge.caddy_admin import reload_caddy

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"config reloaded")
        mock_find.return_value = mock_container

        result = reload_caddy("svc_123", "edge_test")

        assert "reloaded" in result
        mock_container.exec_run.assert_called_once_with(
            "caddy reload --config /etc/caddy/Caddyfile --force"
        )

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_on_failure(self, mock_find):
        import pytest

        from app.edge.caddy_admin import reload_caddy

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (1, b"error: invalid config")
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError, match="Caddy reload failed"):
            reload_caddy("svc_123", "edge_test")

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_if_not_found(self, mock_find):
        import pytest

        from app.edge.caddy_admin import reload_caddy

        mock_find.return_value = None
        with pytest.raises(RuntimeError, match="Edge container not found"):
            reload_caddy("svc_123", "edge_test")

    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_when_container_not_running(self, mock_find):
        """A non-running edge container can't be exec'd, so reload refuses early
        with a clear error instead of attempting (and failing) the exec."""
        from app.edge.caddy_admin import reload_caddy

        mock_container = MagicMock()
        mock_container.status = "exited"
        # Would succeed if the guard were bypassed — proves the guard, not luck.
        mock_container.exec_run.return_value = (0, b"config reloaded")
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError, match="cannot reload Caddy"):
            reload_caddy("svc_123", "edge_test")
        mock_container.exec_run.assert_not_called()

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_retries_when_container_is_restarting(self, mock_find, mock_sleep):
        from app.edge.caddy_admin import reload_caddy

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = [
            docker.errors.APIError(
                '409 Client Error for http+docker://localhost/v1.47/containers/x/exec: '
                'Conflict ("Container x is restarting, wait until the container is running")'
            ),
            (0, b"config reloaded"),
        ]
        mock_find.return_value = mock_container

        result = reload_caddy("svc_123", "edge_test", max_retries=2, retry_delay=0)

        assert "reloaded" in result
        assert mock_container.exec_run.call_count == 2
        mock_container.reload.assert_called()

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_raises_when_container_never_stabilizes(self, mock_find, mock_sleep):
        import pytest

        from app.edge.caddy_admin import reload_caddy

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = docker.errors.APIError(
            '409 Client Error for http+docker://localhost/v1.47/containers/x/exec: '
            'Conflict ("Container x is restarting, wait until the container is running")'
        )
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError, match="never reached a stable running container"):
            reload_caddy("svc_123", "edge_test", max_retries=2, retry_delay=0)

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_real_reload_failure_after_transient_conflict_is_not_masked(
        self, mock_find, mock_sleep
    ):
        """A genuine reload failure on a later attempt must surface the real
        error, not the transient "restarting" conflict from an earlier attempt."""
        import pytest

        from app.edge.caddy_admin import reload_caddy

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = [
            docker.errors.APIError(
                '409 Client Error for http+docker://localhost/v1.47/containers/x/exec: '
                'Conflict ("Container x is restarting, wait until the container is running")'
            ),
            (1, b"error: adapting config using caddyfile: invalid directive"),
        ]
        mock_find.return_value = mock_container

        with pytest.raises(RuntimeError) as excinfo:
            reload_caddy("svc_123", "edge_test", max_retries=2, retry_delay=0)

        message = str(excinfo.value)
        assert "Caddy reload failed (exit 1)" in message
        assert "invalid directive" in message
        assert "never reached a stable running container" not in message

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_retries_when_admin_api_connection_refused(self, mock_find, mock_sleep):
        """Caddy's admin API (:2019) may not be up immediately after the
        container starts: the reload exec returns non-zero with "connection
        refused". This is the documented reason reload_caddy retries, so it must
        retry and succeed once the API is ready instead of surfacing the
        transient error as a hard failure."""
        from app.edge.caddy_admin import reload_caddy

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = [
            (1, b"Error: dial tcp 127.0.0.1:2019: connect: connection refused"),
            (0, b"config reloaded"),
        ]
        mock_find.return_value = mock_container

        result = reload_caddy("svc_123", "edge_test", max_retries=3, retry_delay=0)

        assert "reloaded" in result
        assert mock_container.exec_run.call_count == 2


class TestDetectTailscaleIp(_ConnectStubMixin):
    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_detects_ip_via_tailscale_ip(self, mock_find, mock_sleep):
        from app.edge.tailscale_ops import detect_tailscale_ip

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"100.64.0.1\n")
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.1"

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_detects_ip_via_status_json(self, mock_find, mock_sleep):
        from app.edge.tailscale_ops import detect_tailscale_ip

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

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_retries_on_failure(self, mock_find, mock_sleep):
        from app.edge.tailscale_ops import detect_tailscale_ip

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

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_after_max_retries(self, mock_find, mock_sleep):
        from app.edge.tailscale_ops import detect_tailscale_ip

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (1, b"not ready")
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=2, retry_delay=0)
        assert result is None

    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_if_container_not_found(self, mock_find):
        from app.edge.tailscale_ops import detect_tailscale_ip

        mock_find.return_value = None
        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result is None

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_ignores_non_tailscale_ips(self, mock_find, mock_sleep):
        from app.edge.tailscale_ops import detect_tailscale_ip

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

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_when_container_never_running(self, mock_find, mock_sleep):
        """The container exists but never reaches 'running' (e.g. stuck exited):
        the pre-loop guard bails with None and never exec's tailscale inside a
        non-running container."""

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=2, retry_delay=0)

        assert result is None
        mock_container.exec_run.assert_not_called()


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

    @patch("app.edge.caddy_admin.reload_caddy")
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
        from app.edge.docker_client import resolve_socket
        result = resolve_socket(db_session)
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

    @patch("app.edge.container_manager.get_edge_version", return_value="old")
    @patch("app.secrets.read_secret", return_value="tskey-auth-test")
    @patch("app.edge.image_builder.ensure_edge_image")
    @patch("app.edge.container_manager.recreate_edge")
    def test_update_edge_passes_socket(
        self, mock_recreate, mock_ensure_image, mock_secret, mock_version, client, db_session,
    ):
        from app.settings_store import set_setting

        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()
        mock_recreate.return_value = "new_id"

        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/update-edge")

        assert resp.status_code == 200
        mock_ensure_image.assert_called_once_with("unix:///custom/docker.sock")
        mock_recreate.assert_called_once()
        assert mock_recreate.call_args[0][-1] == "unix:///custom/docker.sock"


# ---------------------------------------------------------------------------
# String-vs-Path acceptance
# ---------------------------------------------------------------------------


class TestStringPathAcceptance:
    """create_edge_container and recreate_edge should accept both str and Path."""

    @patch("app.edge.container_manager.ensure_edge_image")
    @patch("app.edge.container_manager.docker.DockerClient")
    def test_create_accepts_strings(self, mock_cls, mock_ensure, tmp_path):
        from app.edge.container_manager import create_edge_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
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
    @patch("app.edge.container_manager.docker.DockerClient")
    def test_create_accepts_paths(self, mock_cls, mock_ensure, tmp_path):
        from app.edge.container_manager import create_edge_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
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


# ---------------------------------------------------------------------------
# Tailscale control-plane leaf (admin-API device delete + node-id extraction)
# ---------------------------------------------------------------------------


class TestTailscaleDeviceControlPlane:
    """Direct coverage of the control-plane leaf, otherwise exercised only
    indirectly via remove_edge (which stubs both functions out)."""

    def test_get_node_id_returns_self_id(self):
        from app.edge.tailscale_device import _get_tailscale_node_id

        container = MagicMock()
        container.exec_run.return_value = (0, json.dumps({"Self": {"ID": "n123abc"}}).encode())
        assert _get_tailscale_node_id(container) == "n123abc"

    def test_get_node_id_none_on_nonzero_exit(self):
        from app.edge.tailscale_device import _get_tailscale_node_id

        container = MagicMock()
        container.exec_run.return_value = (1, b"tailscale not up")
        assert _get_tailscale_node_id(container) is None

    def test_get_node_id_none_when_self_missing(self):
        from app.edge.tailscale_device import _get_tailscale_node_id

        container = MagicMock()
        container.exec_run.return_value = (0, json.dumps({}).encode())
        assert _get_tailscale_node_id(container) is None

    def test_get_node_id_none_on_invalid_json(self):
        from app.edge.tailscale_device import _get_tailscale_node_id

        container = MagicMock()
        container.exec_run.return_value = (0, b"not json{{{")
        assert _get_tailscale_node_id(container) is None

    @patch("app.edge.tailscale_device.httpx2.delete")
    def test_delete_device_returns_true_on_success(self, mock_delete):
        from app.edge.tailscale_device import _delete_tailscale_device

        resp = MagicMock()
        resp.is_success = True
        mock_delete.return_value = resp

        assert _delete_tailscale_device("n123", "tskey-api-x") is True
        # Basic auth: API key as username, empty password.
        assert mock_delete.call_args.kwargs["auth"] == ("tskey-api-x", "")

    @patch("app.edge.tailscale_device.httpx2.delete")
    def test_delete_device_returns_false_on_http_error(self, mock_delete):
        from app.edge.tailscale_device import _delete_tailscale_device

        resp = MagicMock()
        resp.is_success = False
        resp.status_code = 404
        resp.text = "device not found"
        mock_delete.return_value = resp

        assert _delete_tailscale_device("n123", "tskey-api-x") is False

    @patch("app.edge.tailscale_device.httpx2.delete")
    def test_delete_device_returns_false_on_exception(self, mock_delete):
        from app.edge.tailscale_device import _delete_tailscale_device

        mock_delete.side_effect = RuntimeError("network down")
        assert _delete_tailscale_device("n123", "tskey-api-x") is False


# ---------------------------------------------------------------------------
# edge_container client-lifecycle primitive
# ---------------------------------------------------------------------------


class TestEdgeContainerLifecycle:
    """The single client-lifecycle primitive must close the Docker client on
    both normal exit and body exceptions; _find_edge_container_for_use must
    close it when the lookup faults after connect succeeds."""

    def test_closes_client_on_normal_exit(self):
        from app.edge.container_manager import edge_container

        fake_client = MagicMock()
        with (
            patch("app.edge.container_manager.connect", return_value=fake_client),
            patch("app.edge.container_manager._find_edge_container", return_value=MagicMock()),
            edge_container("svc_123", "edge_test") as (client, container),
        ):
            assert client is fake_client
            assert container is not None
        fake_client.close.assert_called_once()

    def test_closes_client_on_body_exception(self):
        from app.edge.container_manager import edge_container

        fake_client = MagicMock()
        with (
            patch("app.edge.container_manager.connect", return_value=fake_client),
            patch("app.edge.container_manager._find_edge_container", return_value=MagicMock()),
            pytest.raises(ValueError, match="boom"),
            edge_container("svc_123", "edge_test"),
        ):
            raise ValueError("boom")
        fake_client.close.assert_called_once()

    def test_for_use_closes_client_when_lookup_raises_after_connect(self):
        from app.edge.container_manager import _find_edge_container_for_use

        fake_client = MagicMock()
        with (
            patch("app.edge.container_manager.connect", return_value=fake_client),
            patch(
                "app.edge.container_manager._find_edge_container",
                side_effect=docker.errors.APIError("daemon boom"),
            ),
            pytest.raises(docker.errors.APIError, match="daemon boom"),
        ):
            _find_edge_container_for_use("svc_123", "edge_test")
        fake_client.close.assert_called_once()


class TestWaitForRunning:
    """Direct coverage of the exec-safety gate ``_wait_for_running``. Callers
    (reload_caddy, detect_tailscale_ip) rely on it to never exec into a
    non-running container; only the trivial 'running' path was exercised
    indirectly, leaving the terminal-state fast-exit and the timeout path
    (the actual guards) untested."""

    def test_returns_true_when_running(self):

        container = MagicMock()
        container.status = "running"
        assert _wait_for_running(container) is True
        container.reload.assert_called_once()

    @patch("app.edge.container_manager.time.sleep")
    def test_returns_false_immediately_on_terminal_state(self, mock_sleep):
        """A dead/exited/removing container will never reach running, so the
        wait returns at once — no polling sleep, no blocking for the timeout."""

        for terminal in ("exited", "dead", "removing"):
            container = MagicMock()
            container.status = terminal
            assert _wait_for_running(container) is False
        mock_sleep.assert_not_called()

    @patch("app.edge.container_manager.time.sleep")
    @patch("app.edge.container_manager.time.monotonic", side_effect=[0.0, 0.0, 40.0])
    def test_returns_false_on_timeout_when_never_running(self, mock_mono, mock_sleep):
        """A container stuck in a non-terminal, non-running state ('created'/
        'restarting' that never settles) polls until the deadline, then False."""

        container = MagicMock()
        container.status = "created"
        assert _wait_for_running(container, timeout=30.0) is False
        mock_sleep.assert_called_once()
        container.reload.assert_called_once()
