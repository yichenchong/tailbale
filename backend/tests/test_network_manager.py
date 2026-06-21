"""Tests for Docker network management."""

from unittest.mock import MagicMock, patch

import docker.errors


class TestCreateNetwork:
    @patch("app.edge.network_manager.docker.DockerClient")
    def test_creates_new_network(self, mock_cls):
        from app.edge.network_manager import create_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_client.networks.get.side_effect = docker.errors.NotFound("not found")
        mock_network = MagicMock()
        mock_network.id = "net_123"
        mock_client.networks.create.return_value = mock_network

        result = create_network("edge_net_test")

        assert result == "net_123"
        mock_client.networks.create.assert_called_once_with("edge_net_test", driver="bridge")

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_returns_existing_network(self, mock_cls):
        from app.edge.network_manager import create_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_existing = MagicMock()
        mock_existing.id = "existing_net_id"
        mock_client.networks.get.return_value = mock_existing

        result = create_network("edge_net_test")

        assert result == "existing_net_id"
        mock_client.networks.create.assert_not_called()

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_uses_socket_path(self, mock_cls):
        from app.edge.network_manager import create_network

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.networks.get.side_effect = docker.errors.NotFound("not found")
        mock_client.networks.create.return_value = MagicMock(id="net_456")

        create_network("edge_net_test", socket_path="tcp://localhost:2375")
        mock_cls.assert_called_with(base_url="tcp://localhost:2375")


class TestRemoveNetwork:
    @patch("app.edge.network_manager.docker.DockerClient")
    def test_removes_existing(self, mock_cls):
        from app.edge.network_manager import remove_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_client.networks.get.return_value = mock_network

        remove_network("edge_net_test")
        mock_network.remove.assert_called_once()

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_noop_if_not_found(self, mock_cls):
        from app.edge.network_manager import remove_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_client.networks.get.side_effect = docker.errors.NotFound("not found")

        # Should not raise
        remove_network("edge_net_nonexistent")

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_disconnects_endpoints_then_removes_on_active_endpoints(self, mock_cls):
        """Regression: the upstream container stays attached after the edge
        container is removed, so the first ``network.remove()`` fails with
        "has active endpoints". The network must be torn down by disconnecting
        the lingering endpoint(s) and retrying, not leaked."""
        from app.edge.network_manager import remove_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_network.name = "edge_net_test"
        mock_network.attrs = {"Containers": {"upstream_cid": {"Name": "app"}}}
        mock_network.remove.side_effect = [
            docker.errors.APIError(
                "error while removing network: network edge_net_test id has active endpoints"
            ),
            None,
        ]
        mock_client.networks.get.return_value = mock_network

        remove_network("edge_net_test")

        mock_network.disconnect.assert_called_once_with("upstream_cid", force=True)
        assert mock_network.remove.call_count == 2

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_disconnects_all_endpoints(self, mock_cls):
        """Every attached container is force-disconnected before the retry."""
        from app.edge.network_manager import remove_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_network.name = "edge_net_test"
        mock_network.attrs = {"Containers": {"c1": {}, "c2": {}}}
        mock_network.remove.side_effect = [docker.errors.APIError("has active endpoints"), None]
        mock_client.networks.get.return_value = mock_network

        remove_network("edge_net_test")

        disconnected = {c.args[0] for c in mock_network.disconnect.call_args_list}
        assert disconnected == {"c1", "c2"}
        for c in mock_network.disconnect.call_args_list:
            assert c.kwargs == {"force": True}

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_does_not_disconnect_when_remove_succeeds(self, mock_cls):
        """When the network has no lingering endpoints, remove succeeds directly
        and no containers are disconnected."""
        from app.edge.network_manager import remove_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_network.remove.return_value = None
        mock_client.networks.get.return_value = mock_network

        remove_network("edge_net_test")

        mock_network.remove.assert_called_once()
        mock_network.disconnect.assert_not_called()

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_retry_failure_does_not_raise_and_is_logged(self, mock_cls, caplog):
        """If the network still cannot be removed after disconnecting endpoints
        (e.g. a leftover endpoint Docker refuses to drop, or a transient daemon
        error), remove_network must not raise — the sole caller suppresses
        exceptions, so a raise would leak the network silently. The failure is
        logged so the leak is observable."""
        import logging

        from app.edge.network_manager import remove_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_network.name = "edge_net_test"
        mock_network.attrs = {"Containers": {"stuck_cid": {}}}
        mock_network.remove.side_effect = docker.errors.APIError("has active endpoints")
        mock_client.networks.get.return_value = mock_network

        with caplog.at_level(logging.WARNING):
            # Must not raise even though both remove attempts fail.
            remove_network("edge_net_test")

        assert mock_network.remove.call_count == 2
        mock_network.disconnect.assert_called_once_with("stuck_cid", force=True)
        assert any("may leak" in r.message for r in caplog.records)


class TestConnectContainer:
    @patch("app.edge.network_manager.docker.DockerClient")
    def test_connects_container(self, mock_cls):
        from app.edge.network_manager import connect_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_client.networks.get.return_value = mock_network
        mock_container = MagicMock()
        mock_container.attrs = {"NetworkSettings": {"Networks": {}}}
        mock_client.containers.get.return_value = mock_container

        result = connect_container("edge_net_test", "container_123")
        assert result == mock_container.id
        mock_network.connect.assert_called_once_with(mock_container)

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_noop_if_already_connected(self, mock_cls):
        from app.edge.network_manager import connect_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_client.networks.get.return_value = mock_network
        mock_container = MagicMock()
        mock_container.attrs = {
            "NetworkSettings": {"Networks": {"edge_net_test": {}}}
        }
        mock_client.containers.get.return_value = mock_container

        result = connect_container("edge_net_test", "container_123")
        assert result == mock_container.id
        mock_network.connect.assert_not_called()

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_falls_back_to_container_name_when_id_is_stale(self, mock_cls):
        from app.edge.network_manager import connect_container

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_client.networks.get.return_value = mock_network
        mock_container = MagicMock()
        mock_container.id = "resolved_456"
        mock_container.attrs = {"NetworkSettings": {"Networks": {}}}
        mock_client.containers.get.side_effect = [
            docker.errors.NotFound("not found"),
            mock_container,
        ]

        result = connect_container("edge_net_test", "stale_123", container_name="app")

        assert result == "resolved_456"
        mock_network.connect.assert_called_once_with(mock_container)


class TestEnsureNetwork:
    @patch("app.edge.network_manager.docker.DockerClient")
    def test_creates_and_connects(self, mock_cls):
        from app.edge.network_manager import ensure_network

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client

        # create_network: network not found, create new
        mock_client.networks.get.side_effect = [
            docker.errors.NotFound("not found"),  # create_network check
            MagicMock(),  # connect_container network.get
        ]
        mock_new_net = MagicMock()
        mock_new_net.id = "new_net_id"
        mock_client.networks.create.return_value = mock_new_net

        # connect_container: container not on network yet
        mock_container = MagicMock()
        mock_container.attrs = {"NetworkSettings": {"Networks": {}}}
        mock_client.containers.get.return_value = mock_container

        result = ensure_network("edge_net_test", "app_container_id")
        assert result == ("new_net_id", mock_container.id)
