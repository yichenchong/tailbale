"""Tests for the Discovery API endpoints."""

from unittest.mock import MagicMock, patch


def _make_mock_container(
    id="abc123",
    name="myapp",
    image_tags=None,
    status="running",
    state="running",
    ports=None,
    networks=None,
    labels=None,
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
            "Ports": ports or {"80/tcp": [{"HostPort": "8080"}]},
            "Networks": networks or {"bridge": {}},
        },
        "Config": {"Image": "myapp:latest"},
    }

    return container


class TestListContainers:
    @patch("app.routers.discovery.docker.DockerClient")
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

    @patch("app.routers.discovery.docker.DockerClient")
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

    @patch("app.routers.discovery.docker.DockerClient")
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

    @patch("app.routers.discovery.docker.DockerClient")
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

    @patch("app.routers.discovery.docker.DockerClient")
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

    @patch("app.routers.discovery.docker.DockerClient")
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

    @patch("app.routers.discovery.docker.DockerClient")
    def test_search_case_insensitive(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = [
            _make_mock_container(id="c1", name="Nextcloud"),
        ]

        resp = client.get("/api/discovery/containers?search=NEXTCLOUD")
        data = resp.json()
        assert data["total"] == 1

    @patch("app.routers.discovery.docker.DockerClient")
    def test_running_only_default(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = []

        client.get("/api/discovery/containers")
        mock_client.containers.list.assert_called_once_with(all=False)

    @patch("app.routers.discovery.docker.DockerClient")
    def test_running_only_false(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client
        mock_client.containers.list.return_value = []

        client.get("/api/discovery/containers?running_only=false")
        mock_client.containers.list.assert_called_once_with(all=True)

    @patch("app.routers.discovery.docker.DockerClient")
    def test_docker_unavailable_returns_empty(self, mock_docker_cls, client):
        mock_docker_cls.side_effect = Exception("Docker not available")

        resp = client.get("/api/discovery/containers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["containers"] == []

    @patch("app.routers.discovery.docker.DockerClient")
    def test_container_without_image_tags(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client

        container = _make_mock_container(id="c1", name="custom")
        container.image.tags = []  # No tags
        mock_client.containers.list.return_value = [container]

        resp = client.get("/api/discovery/containers")
        data = resp.json()
        assert data["containers"][0]["image"] == "myapp:latest"  # Falls back to Config.Image

    @patch("app.routers.discovery.docker.DockerClient")
    def test_container_no_ports(self, mock_docker_cls, client):
        mock_client = MagicMock()
        mock_docker_cls.return_value = mock_client

        container = _make_mock_container(id="c1", name="worker")
        container.attrs["NetworkSettings"]["Ports"] = {}
        mock_client.containers.list.return_value = [container]

        resp = client.get("/api/discovery/containers")
        data = resp.json()
        assert data["containers"][0]["ports"] == []
