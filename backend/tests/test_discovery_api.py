"""Tests for the Discovery API endpoints."""

from unittest.mock import MagicMock, patch

import docker
import requests

from app.settings_store import set_setting


def _make_mock_container(
    id="abc123",
    name="myapp",
    image_tags=None,
    status="running",
    state="running",
    ports=None,
    networks=None,
    labels=None,
    config_image=None,
):
    """Create a mock Docker container."""
    container = MagicMock()
    container.id = id
    container.name = name
    container.status = status
    container.labels = labels or {}

    # Image mock
    image = MagicMock()
    image.tags = image_tags or ["myapp:latest"]
    container.image = image

    # Attrs mock
    container.attrs = {
        "State": {"Status": state},
        "NetworkSettings": {
            "Ports": ports if ports is not None else {"80/tcp": [{"HostPort": "8080"}]},
            "Networks": networks or {"bridge": {}},
        },
        "Config": {"Image": config_image or (image.tags[0] if image.tags else "myapp:latest")},
    }

    return container


class TestListContainers:
    @patch("app.services.diagnostics.docker.DockerClient")
    def test_lists_containers(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="nginx", image_tags=["nginx:latest"]),
            _make_mock_container(id="c2", name="postgres", image_tags=["postgres:15"]),
        ]

        resp = client.get("/api/discovery/containers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["containers"]) == 2
        names = [c["name"] for c in data["containers"]]
        assert "nginx" in names
        assert "postgres" in names

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_closes_docker_client(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = []

        resp = client.get("/api/discovery/containers")

        assert resp.status_code == 200
        mock_client.close.assert_called_once()

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_closes_docker_client_when_list_fails(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.side_effect = docker.errors.DockerException("boom")

        resp = client.get("/api/discovery/containers")

        assert resp.status_code == 200
        assert resp.json() == {"containers": [], "total": 0}
        mock_client.close.assert_called_once()

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_list_connection_error_degrades_to_empty(self, mock_docker_cls, client):
        # The client constructs fine (daemon reachable at version-probe time) but
        # the daemon dies before containers.list(); the SDK surfaces a raw
        # requests.exceptions.ConnectionError, which is NOT a DockerException.
        # Discovery must degrade to 200/empty (matching the DockerException path
        # and the services router's edge-action mapping), not 500.
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.side_effect = requests.exceptions.ConnectionError(
            "daemon went away mid-call"
        )

        resp = client.get("/api/discovery/containers")

        assert resp.status_code == 200
        assert resp.json() == {"containers": [], "total": 0}
        mock_client.close.assert_called_once()

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_container_fields(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(
                id="c1",
                name="nextcloud",
                image_tags=["nextcloud:28"],
                ports={"80/tcp": [{"HostPort": "9080"}], "443/tcp": None},
                networks={"bridge": {}, "custom_net": {}},
                labels={"com.docker.compose.project": "mystack"},
            )
        ]

        resp = client.get("/api/discovery/containers")
        data = resp.json()
        c = data["containers"][0]
        assert c["id"] == "c1"
        assert c["name"] == "nextcloud"
        assert c["image"] == "nextcloud:28"
        assert c["state"] == "running"
        assert len(c["ports"]) == 2
        assert c["ports"][0]["container_port"] == "80"
        assert c["ports"][0]["host_port"] == "9080"
        assert c["ports"][1]["container_port"] == "443"
        assert c["ports"][1]["host_port"] is None
        assert "bridge" in c["networks"]
        assert "custom_net" in c["networks"]
        assert c["labels"]["com.docker.compose.project"] == "mystack"

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_hides_managed_containers(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="myapp"),
            _make_mock_container(id="c2", name="edge_myapp", labels={"tailbale.managed": "true"}),
        ]

        resp = client.get("/api/discovery/containers?hide_managed=true")
        data = resp.json()
        assert data["total"] == 1
        assert data["containers"][0]["name"] == "myapp"

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_shows_main_orchestrator_container(self, mock_docker_cls, client):
        """The tailBale orchestrator itself (labeled tailbale.main=true, per
        docker-compose) IS offered as an exposure candidate so the admin UI can be
        wrapped as a service under a custom domain. Only edge containers
        (tailbale.managed) are hidden; tailbale.main is intentionally discoverable."""
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="myapp"),
            _make_mock_container(id="c2", name="tailbale", labels={"tailbale.main": "true"}),
        ]

        resp = client.get("/api/discovery/containers?hide_managed=true")
        data = resp.json()
        assert data["total"] == 2
        names = {c["name"] for c in data["containers"]}
        assert names == {"myapp", "tailbale"}

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_shows_managed_when_not_hidden(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="myapp"),
            _make_mock_container(id="c2", name="edge_myapp", labels={"tailbale.managed": "true"}),
        ]

        resp = client.get("/api/discovery/containers?hide_managed=false")
        data = resp.json()
        assert data["total"] == 2

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_search_by_name(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="nginx"),
            _make_mock_container(id="c2", name="postgres"),
            _make_mock_container(id="c3", name="nextcloud"),
        ]

        resp = client.get("/api/discovery/containers?search=next")
        data = resp.json()
        assert data["total"] == 1
        assert data["containers"][0]["name"] == "nextcloud"

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_search_by_image(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="web", image_tags=["nginx:latest"]),
            _make_mock_container(id="c2", name="db", image_tags=["postgres:15"]),
        ]

        resp = client.get("/api/discovery/containers?search=nginx")
        data = resp.json()
        assert data["total"] == 1
        assert data["containers"][0]["name"] == "web"

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_search_case_insensitive(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="Nextcloud"),
        ]

        resp = client.get("/api/discovery/containers?search=NEXTCLOUD")
        data = resp.json()
        assert data["total"] == 1

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_search_trims_whitespace(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="nextcloud"),
        ]

        resp = client.get("/api/discovery/containers?search=%20next%20")
        data = resp.json()

        assert data["total"] == 1
        assert data["containers"][0]["name"] == "nextcloud"

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_running_only_default(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = []

        client.get("/api/discovery/containers")
        mock_client.containers.list.assert_called_once_with(all=False)

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_running_only_false(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = []

        client.get("/api/discovery/containers?running_only=false")
        mock_client.containers.list.assert_called_once_with(all=True)

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_docker_unavailable_returns_empty(self, mock_docker_cls, client):
        mock_docker_cls.side_effect = docker.errors.DockerException("Docker not available")

        resp = client.get("/api/discovery/containers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["containers"] == []

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_container_without_image_tags(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client

        container = _make_mock_container(id="c1", name="custom")
        container.image.tags = []  # No tags
        mock_client.containers.list.return_value = [container]

        resp = client.get("/api/discovery/containers")
        data = resp.json()
        assert data["containers"][0]["image"] == "myapp:latest"  # Falls back to Config.Image

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_uses_config_image_instead_of_lazy_image_tags(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(
                id="c1",
                name="nextcloud",
                image_tags=["nginx:latest"],
                config_image="linuxserver/nextcloud:28",
            )
        ]

        resp = client.get("/api/discovery/containers")
        data = resp.json()

        assert data["containers"][0]["image"] == "linuxserver/nextcloud:28"

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_container_no_ports(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client

        container = _make_mock_container(id="c1", name="worker")
        container.attrs["NetworkSettings"]["Ports"] = {}
        mock_client.containers.list.return_value = [container]

        resp = client.get("/api/discovery/containers")
        data = resp.json()
        assert data["containers"][0]["ports"] == []

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_container_ports_fall_back_to_exposed_ports(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client

        container = _make_mock_container(id="c1", name="worker", ports={})
        container.attrs["Config"]["ExposedPorts"] = {"8080/tcp": {}, "8443/tcp": {}}
        mock_client.containers.list.return_value = [container]

        resp = client.get("/api/discovery/containers")
        data = resp.json()

        assert data["containers"][0]["ports"] == [
            {"container_port": "8080", "host_port": None, "protocol": "tcp"},
            {"container_port": "8443", "host_port": None, "protocol": "tcp"},
        ]

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_container_ports_use_first_available_host_port(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client

        container = _make_mock_container(
            id="c1",
            name="web",
            ports={"80/tcp": [{"HostIp": "127.0.0.1"}, {"HostPort": "8080"}]},
        )
        mock_client.containers.list.return_value = [container]

        resp = client.get("/api/discovery/containers")
        data = resp.json()

        assert data["containers"][0]["ports"] == [
            {"container_port": "80", "host_port": "8080", "protocol": "tcp"}
        ]

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_container_empty_host_port_normalized_to_none(self, mock_docker_cls, client):
        # A stopped/unassigned published port can surface a binding with an empty
        # HostPort ("") in `docker inspect` (discovery lists stopped containers
        # when running_only=false). Empty string is not a real host port; the
        # `host_port: str | None` field must report None ("unpublished"), never a
        # blank string the expose form would treat as a usable port.
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client

        container = _make_mock_container(
            id="c1",
            name="stopped",
            status="exited",
            state="exited",
            ports={"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": ""}]},
        )
        mock_client.containers.list.return_value = [container]

        resp = client.get("/api/discovery/containers?running_only=false")
        data = resp.json()

        assert data["containers"][0]["ports"] == [
            {"container_port": "80", "host_port": None, "protocol": "tcp"}
        ]

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_uses_base_url_when_socket_path_configured(self, mock_docker_cls, client):
        """A configured docker_socket_path is passed verbatim as base_url."""
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = []

        resp = client.get("/api/discovery/containers")

        assert resp.status_code == 200
        # Default setting is the local unix socket -> constructor (not from_env).
        mock_docker_cls.assert_called_once_with(base_url="unix:///var/run/docker.sock")
        mock_docker_cls.from_env.assert_not_called()

    @patch("app.services.diagnostics.docker.DockerClient")
    def test_uses_from_env_when_socket_path_blank(self, mock_docker_cls, client, db_session):
        """When docker_socket_path is cleared (operator opts into DOCKER_HOST),
        discovery must resolve the daemon via from_env() so it honors DOCKER_HOST,
        mirroring _validate_upstream / the edge managers. Passing base_url="" would
        silently query the default local socket and ignore DOCKER_HOST."""

        set_setting(db_session, "docker_socket_path", "")
        db_session.commit()

        mock_from_env_client = MagicMock()
        mock_docker_cls.from_env.return_value = mock_from_env_client
        mock_from_env_client.containers.list.return_value = []

        resp = client.get("/api/discovery/containers")

        assert resp.status_code == 200
        mock_docker_cls.from_env.assert_called_once_with()
        mock_docker_cls.assert_not_called()  # base_url constructor must not be used
        mock_from_env_client.close.assert_called_once()
