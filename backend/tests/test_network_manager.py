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

        connect_container("edge_net_test", "container_123")
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

        connect_container("edge_net_test", "container_123")
        mock_network.connect.assert_not_called()


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
        assert result == "new_net_id"
