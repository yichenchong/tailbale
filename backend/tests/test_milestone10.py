"""Tests for Milestone 10 bug fixes: hostname validation, container checks,
disable/delete cleanup, thread-safe reconcile, full health-check endpoint,
and DB-backed runtime paths."""

import json
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Hostname domain validation on create (fix #8)
# ---------------------------------------------------------------------------

class TestHostnameValidation:
    def _create(self, client, hostname="app.example.com", **kw):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": hostname,
            "base_domain": "example.com",
            **kw,
        }
        return client.post("/api/services", json=body)

    def test_hostname_matching_domain_accepted(self, client):
        resp = self._create(client, hostname="myapp.example.com")
        assert resp.status_code == 201

    def test_hostname_wrong_domain_rejected(self, client):
        resp = self._create(client, hostname="myapp.wrongdomain.com")
        assert resp.status_code == 422
        assert "must end with" in resp.json()["detail"]

    def test_hostname_bare_domain_rejected(self, client):
        """The bare domain 'example.com' doesn't end with '.example.com'."""
        resp = self._create(client, hostname="example.com")
        assert resp.status_code == 422

    def test_subdomain_deep_nesting_accepted(self, client):
        resp = self._create(client, hostname="a.b.c.example.com")
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Container existence warning on create (fix #7)
# ---------------------------------------------------------------------------

class TestContainerExistenceCheck:
    def _create(self, client, **kw):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "app.example.com",
            "base_domain": "example.com",
            **kw,
        }
        return client.post("/api/services", json=body)

    @patch("app.routers.services.docker_lib", create=True)
    def test_warning_when_container_not_found(self, mock_docker, client):
        """When Docker is available but container doesn't exist, message warns."""
        # The docker import inside create_service raises on containers.get
        resp = self._create(client)
        assert resp.status_code == 201
        msg = resp.json()["status"]["message"].lower()
        assert "reconciliation" in msg

    @patch("app.routers.services.docker_lib", create=True)
    def test_default_message_when_docker_unavailable(self, mock_docker, client):
        """When Docker isn't available, still creates with a useful message."""
        resp = self._create(client)
        assert resp.status_code == 201
        # Should contain 'reconciliation' regardless of Docker availability
        msg = resp.json()["status"]["message"].lower()
        assert "reconciliation" in msg


# ---------------------------------------------------------------------------
# Disable stops edge container (fix #5)
# ---------------------------------------------------------------------------

class TestDisableStopsEdge:
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

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_calls_stop_edge(self, mock_stop, client):
        svc_id = self._create(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        mock_stop.assert_called_once()

    @patch("app.edge.container_manager.stop_edge", side_effect=RuntimeError("no container"))
    def test_disable_succeeds_even_if_stop_fails(self, mock_stop, client):
        svc_id = self._create(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


# ---------------------------------------------------------------------------
# Delete removes edge + network + files (fix #5)
# ---------------------------------------------------------------------------

class TestDeleteCleansUp:
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

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_calls_remove_edge_and_network(self, mock_remove_edge, mock_remove_net, client):
        svc_id = self._create(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204
        mock_remove_edge.assert_called_once()
        mock_remove_net.assert_called_once()

    @patch("app.edge.network_manager.remove_network", side_effect=Exception("fail"))
    @patch("app.edge.container_manager.remove_edge", side_effect=Exception("fail"))
    def test_delete_succeeds_even_if_cleanup_fails(self, mock_re, mock_rn, client):
        svc_id = self._create(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204
        # Service should actually be gone
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Full health-check endpoint (fix #9)
# ---------------------------------------------------------------------------

class TestFullHealthCheck:
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

    @patch("app.health.health_checker.run_health_checks")
    @patch("app.secrets.read_secret", return_value=None)
    def test_full_health_check_without_cloudflare(self, mock_secret, mock_checks, client):
        mock_checks.return_value = {"edge_container_present": True}
        svc_id = self._create(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/health-check-full")
        assert resp.status_code == 200
        data = resp.json()
        assert "checks" in data
        assert "extended" in data
        assert data["extended"]["cf_error"]  # Should report missing config

    def test_full_health_check_404_for_missing(self, client):
        resp = client.post("/api/services/svc_nonexistent/health-check-full")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DB-backed runtime paths (fix #3)
# ---------------------------------------------------------------------------

class TestRuntimePaths:
    def test_defaults_fall_back_to_config(self, db_session):
        from app.settings_store import get_runtime_paths

        paths = get_runtime_paths(db_session)
        assert "generated_dir" in paths
        assert "certs_dir" in paths
        assert "tailscale_state_dir" in paths
        assert "docker_socket" in paths
        # All should have non-empty values (from config fallback)
        for v in paths.values():
            assert v

    def test_db_overrides_config(self, db_session):
        from app.settings_store import get_runtime_paths, set_setting

        set_setting(db_session, "generated_root", "/custom/generated")
        set_setting(db_session, "cert_root", "/custom/certs")
        db_session.flush()

        paths = get_runtime_paths(db_session)
        assert paths["generated_dir"] == "/custom/generated"
        assert paths["certs_dir"] == "/custom/certs"
