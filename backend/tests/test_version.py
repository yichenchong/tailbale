"""Tests for version module, /api/version endpoint, edge version detection, and edge update."""

from unittest.mock import MagicMock, patch


class TestVersionModule:
    def test_reads_version_file(self, tmp_path):
        vfile = tmp_path / "VERSION"
        vfile.write_text("1.2.3\n")

        with patch("app.version._VERSION_FILE_LOCATIONS", [vfile]):
            # Re-call get_version since __version__ is cached at import time
            from app.version import get_version
            assert get_version() == "1.2.3"

    def test_returns_dev_when_missing(self, tmp_path):
        missing = tmp_path / "NOPE"
        with patch("app.version._VERSION_FILE_LOCATIONS", [missing]):
            from app.version import get_version
            assert get_version() == "dev"

    def test_strips_whitespace(self, tmp_path):
        vfile = tmp_path / "VERSION"
        vfile.write_text("  2.0.0  \n")

        with patch("app.version._VERSION_FILE_LOCATIONS", [vfile]):
            from app.version import get_version
            assert get_version() == "2.0.0"


class TestVersionEndpoint:
    def test_returns_version(self, client):
        resp = client.get("/api/version")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0


class TestEdgeVersionEndpoint:
    def _create(self, client):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "app.example.com",
            "base_domain": "example.com",
        }
        return client.post("/api/services", json=body)

    @patch("app.edge.container_manager.get_edge_version", return_value="0.1.0")
    def test_returns_version_comparison(self, mock_ver, client):
        svc_id = self._create(client).json()["id"]
        resp = client.get(f"/api/services/{svc_id}/edge-version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edge_version"] == "0.1.0"
        assert "orchestrator_version" in data
        assert isinstance(data["up_to_date"], bool)

    @patch("app.edge.container_manager.get_edge_version", return_value=None)
    def test_returns_null_when_no_container(self, mock_ver, client):
        svc_id = self._create(client).json()["id"]
        resp = client.get(f"/api/services/{svc_id}/edge-version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["edge_version"] is None
        assert data["up_to_date"] is False

    def test_404_for_missing_service(self, client):
        resp = client.get("/api/services/svc_nonexistent/edge-version")
        assert resp.status_code == 404


class TestGetEdgeVersion:
    @patch("app.edge.container_manager._find_edge_container")
    def test_reads_label(self, mock_find):
        from app.edge.container_manager import get_edge_version

        mock_container = MagicMock()
        mock_container.labels = {"tailbale.version": "0.2.0", "tailbale.managed": "true"}
        mock_find.return_value = mock_container

        assert get_edge_version("svc_1", "edge_app") == "0.2.0"

    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_when_no_label(self, mock_find):
        from app.edge.container_manager import get_edge_version

        mock_container = MagicMock()
        mock_container.labels = {"tailbale.managed": "true"}
        mock_find.return_value = mock_container

        assert get_edge_version("svc_1", "edge_app") is None

    @patch("app.edge.container_manager._find_edge_container")
    def test_returns_none_when_no_container(self, mock_find):
        from app.edge.container_manager import get_edge_version

        mock_find.return_value = None
        assert get_edge_version("svc_1", "edge_app") is None


class TestUpdateEdgeEndpoint:
    def _create(self, client):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "app.example.com",
            "base_domain": "example.com",
        }
        return client.post("/api/services", json=body)

    @patch("app.edge.container_manager.get_edge_version")
    def test_already_up_to_date(self, mock_ver, client):
        from app.version import __version__
        mock_ver.return_value = __version__

        svc_id = self._create(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/update-edge")
        assert resp.status_code == 200
        assert "already at version" in resp.json()["message"]

    def test_404_for_missing_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/update-edge")
        assert resp.status_code == 404


class TestContainerVersionLabel:
    @patch("app.edge.container_manager.ensure_edge_image")
    @patch("app.edge.container_manager.docker.DockerClient")
    def test_create_stamps_version_label(self, mock_cls, mock_ensure, tmp_path):
        from app.edge.container_manager import create_edge_container
        from app.version import __version__

        mock_client = MagicMock()
        mock_cls.from_env.return_value = mock_client
        mock_container = MagicMock()
        mock_container.id = "c123"
        mock_client.containers.create.return_value = mock_container

        svc = MagicMock()
        svc.id = "svc_1"
        svc.name = "App"
        svc.hostname = "app.example.com"
        svc.upstream_container_name = "app"
        svc.upstream_port = 80
        svc.upstream_scheme = "http"
        svc.edge_container_name = "edge_app"
        svc.network_name = "edge_net_app"
        svc.ts_hostname = "edge-app"

        create_edge_container(
            svc, "tskey-test",
            tmp_path / "gen", tmp_path / "certs", tmp_path / "ts",
        )

        call_kwargs = mock_client.containers.create.call_args
        assert call_kwargs.kwargs["labels"]["tailbale.version"] == __version__
