"""Tests for Docker network management."""

import logging
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from app.edge.network_manager import (
    connect_container,
    create_network,
    ensure_network,
    reconcile_additional_edge_networks,
    remove_network,
)


class TestCreateNetwork:
    @patch("app.edge.network_manager.docker.DockerClient")
    def test_creates_new_network(self, mock_cls):

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

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.networks.get.side_effect = docker.errors.NotFound("not found")
        mock_client.networks.create.return_value = MagicMock(id="net_456")

        create_network("edge_net_test", socket_path="tcp://localhost:2375")
        mock_cls.assert_called_with(base_url="tcp://localhost:2375")

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_recovers_from_concurrent_create_race(self, mock_cls):
        """If another caller creates the network between our get and create
        (modern daemons reject duplicate names with 409), recover by returning
        the existing network instead of propagating the error."""

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        existing = MagicMock(id="net_existing")
        mock_client.networks.get.side_effect = [
            docker.errors.NotFound("not found"),  # initial check: absent
            existing,                              # post-race lookup: present
        ]
        mock_client.networks.create.side_effect = docker.errors.APIError(
            "network with name edge_net_test already exists"
        )

        result = create_network("edge_net_test")
        assert result == "net_existing"
        assert mock_client.networks.get.call_count == 2

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_create_failure_without_existing_reraises(self, mock_cls):
        """A genuine create failure (network still absent afterwards) surfaces
        the original APIError rather than a confusing NotFound."""

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_client.networks.get.side_effect = docker.errors.NotFound("not found")
        mock_client.networks.create.side_effect = docker.errors.APIError("driver boom")

        with pytest.raises(docker.errors.APIError, match="driver boom"):
            create_network("edge_net_test")


class TestRemoveNetwork:
    @patch("app.edge.network_manager.docker.DockerClient")
    def test_removes_existing(self, mock_cls):

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_client.networks.get.return_value = mock_network

        remove_network("edge_net_test")
        mock_network.remove.assert_called_once()

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_noop_if_not_found(self, mock_cls):

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
    def test_connect_race_already_connected_is_idempotent(self, mock_cls):

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_client.networks.get.return_value = mock_network
        mock_container = MagicMock()
        mock_container.id = "container_123"
        mock_container.attrs = {"NetworkSettings": {"Networks": {}}}
        mock_client.containers.get.return_value = mock_container

        def connect_then_report_connected(container):
            mock_container.attrs = {"NetworkSettings": {"Networks": {"edge_net_test": {}}}}
            raise docker.errors.APIError("endpoint already exists in network")

        mock_network.connect.side_effect = connect_then_report_connected

        result = connect_container("edge_net_test", "container_123")

        assert result == "container_123"
        mock_network.connect.assert_called_once_with(mock_container)

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_falls_back_to_container_name_when_id_is_stale(self, mock_cls):

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

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_connect_reraises_unexpected_api_error(self, mock_cls):
        """An APIError that is NOT an 'already connected/exists' race must
        propagate — it is a real failure, not idempotent recovery."""

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_network = MagicMock()
        mock_client.networks.get.return_value = mock_network
        mock_container = MagicMock()
        mock_container.attrs = {"NetworkSettings": {"Networks": {}}}
        mock_client.containers.get.return_value = mock_container
        mock_network.connect.side_effect = docker.errors.APIError("500 server error: boom")

        with pytest.raises(docker.errors.APIError):
            connect_container("edge_net_test", "container_123")


class TestEnsureNetwork:
    @patch("app.edge.network_manager.docker.DockerClient")
    def test_creates_and_connects(self, mock_cls):

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

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_idempotent_when_network_and_connection_exist(self, mock_cls):
        """ED4: fully-converged state — the per-service network already exists
        and the app container is already attached to it. ensure_network must be
        a true no-op: it creates no network and connects nothing, returning the
        existing ids. Guards the composed idempotency the reconciler relies on
        to run every cadence without churning the isolation network."""
        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        existing_net = MagicMock()
        existing_net.id = "net_existing"
        mock_client.networks.get.return_value = existing_net
        mock_container = MagicMock()
        mock_container.id = "app_cid"
        mock_container.attrs = {"NetworkSettings": {"Networks": {"edge_net_test": {}}}}
        mock_client.containers.get.return_value = mock_container

        result = ensure_network("edge_net_test", "app_cid")

        assert result == ("net_existing", "app_cid")
        mock_client.networks.create.assert_not_called()
        existing_net.connect.assert_not_called()


class TestReconcileAdditionalEdgeNetworks:
    @patch("app.edge.network_manager.docker.DockerClient")
    def test_connects_edge_to_existing_network_with_aliases(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_container.id = "edge_cid"
        mock_container.name = "edge_opencloud"
        mock_container.attrs = {
            "Name": "/edge_opencloud",
            "NetworkSettings": {"Networks": {"edge_net_opencloud": {}}},
        }
        mock_client.containers.get.return_value = mock_container
        additional = MagicMock()
        additional.name = "opencloud_opencloud-net"
        mock_client.networks.get.return_value = additional

        reconcile_additional_edge_networks(
            "edge_opencloud",
            "edge_net_opencloud",
            [{"name": "opencloud_opencloud-net", "aliases": ["cloud.example.com"]}],
        )

        mock_client.networks.create.assert_not_called()
        additional.connect.assert_called_once_with(
            mock_container,
            aliases=["cloud.example.com"],
        )

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_reconnects_when_aliases_change(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_container.id = "edge_cid"
        mock_container.name = "edge_opencloud"
        mock_container.attrs = {
            "Name": "/edge_opencloud",
            "NetworkSettings": {
                "Networks": {
                    "edge_net_opencloud": {},
                    "opencloud_opencloud-net": {"Aliases": ["old.example.com"]},
                },
            },
        }
        mock_client.containers.get.return_value = mock_container
        additional = MagicMock()
        additional.name = "opencloud_opencloud-net"
        mock_client.networks.get.return_value = additional

        reconcile_additional_edge_networks(
            "edge_opencloud",
            "edge_net_opencloud",
            [{"name": "opencloud_opencloud-net", "aliases": ["cloud.example.com"]}],
        )

        additional.disconnect.assert_called_once_with(mock_container, force=True)
        additional.connect.assert_called_once_with(
            mock_container,
            aliases=["cloud.example.com"],
        )

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_disconnects_unconfigured_non_primary_networks(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_container.id = "edge_cid"
        mock_container.name = "edge_opencloud"
        mock_container.attrs = {
            "Name": "/edge_opencloud",
            "NetworkSettings": {
                "Networks": {
                    "edge_net_opencloud": {},
                    "old_opencloud-net": {"Aliases": ["old.example.com"]},
                },
            },
        }
        mock_client.containers.get.return_value = mock_container
        old_network = MagicMock()
        old_network.name = "old_opencloud-net"
        mock_client.networks.get.return_value = old_network

        reconcile_additional_edge_networks("edge_opencloud", "edge_net_opencloud", [])

        old_network.disconnect.assert_called_once_with(mock_container, force=True)
        old_network.connect.assert_not_called()

    @patch("app.edge.network_manager.docker.DockerClient")
    def test_never_disconnects_primary_network(self, mock_cls):
        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_container.id = "edge_cid"
        mock_container.name = "edge_opencloud"
        mock_container.attrs = {
            "Name": "/edge_opencloud",
            "NetworkSettings": {
                "Networks": {
                    "edge_net_opencloud": {"Aliases": ["edge_opencloud"]},
                },
            },
        }
        mock_client.containers.get.return_value = mock_container

        reconcile_additional_edge_networks("edge_opencloud", "edge_net_opencloud", [])

        mock_client.networks.get.assert_not_called()
