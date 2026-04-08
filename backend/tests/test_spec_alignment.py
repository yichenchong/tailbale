"""Tests for spec-alignment fixes:
- Upstream container validation on create (spec §17 step 2)
- Upstream port plausibility on create (spec §17 step 3)
- HTTPS probe in health checks (spec §18.1)
- Disable with DNS cleanup (spec §7.4)
- Unused import cleanup
"""

from unittest.mock import MagicMock, patch

import docker.errors
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_service(client, **overrides):
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


def _make_container(exposed_ports=None, port_bindings=None):
    c = MagicMock()
    c.name = "testcontainer"
    c.attrs = {
        "Config": {"ExposedPorts": exposed_ports or {}},
        "HostConfig": {"PortBindings": port_bindings or {}},
    }
    return c


# ---------------------------------------------------------------------------
# 1. Upstream container existence validation
# ---------------------------------------------------------------------------


class TestUpstreamContainerValidation:
    """create_service should reject requests when upstream container doesn't exist."""

    def test_missing_container_returns_422(self, client):
        """If Docker says NotFound, the API should 422."""
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=__import__("fastapi").HTTPException(
                status_code=422, detail="not found"
            ),
        ):
            resp = _create_service(client)
            assert resp.status_code == 422

    def test_docker_unreachable_returns_503(self, client):
        """If Docker daemon is unreachable, the API should 503."""
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=__import__("fastapi").HTTPException(
                status_code=503, detail="cannot connect"
            ),
        ):
            resp = _create_service(client)
            assert resp.status_code == 503

    def test_valid_container_succeeds(self, client):
        """When container exists and port is valid, creation succeeds."""
        # The autouse fixture already mocks _validate_upstream to a no-op
        resp = _create_service(client)
        assert resp.status_code == 201

    def test_create_service_calls_validate_upstream(self, client):
        """create_service should call _validate_upstream during creation."""
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = _create_service(client)
            assert resp.status_code == 201
            mock_val.assert_called_once()
            # Verify it was called with the right container_id and port
            args = mock_val.call_args
            assert args[0][1] == "abc123"  # container_id
            assert args[0][2] == 80  # port

    def test_validate_upstream_not_found_via_api(self, client):
        """API should 422 when _validate_upstream raises for missing container."""
        from fastapi import HTTPException

        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=422, detail="Upstream container 'x' not found"),
        ):
            resp = _create_service(client)
            assert resp.status_code == 422
            assert "not found" in resp.json()["detail"].lower()

    def test_validate_upstream_docker_unreachable_via_api(self, client):
        """API should 503 when _validate_upstream raises for Docker unreachable."""
        from fastapi import HTTPException

        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=503, detail="Cannot connect to Docker"),
        ):
            resp = _create_service(client)
            assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 2. Upstream port plausibility validation
# ---------------------------------------------------------------------------


class TestUpstreamPortValidation:
    """_validate_upstream_port should check exposed ports on the container."""

    def test_port_in_exposed_ports_passes(self):
        from app.routers.services import _validate_upstream_port

        container = _make_container(exposed_ports={"80/tcp": {}, "443/tcp": {}})
        # Should not raise
        _validate_upstream_port(container, 80)

    def test_port_not_in_exposed_ports_raises(self):
        from app.routers.services import _validate_upstream_port

        container = _make_container(exposed_ports={"80/tcp": {}, "443/tcp": {}})
        with pytest.raises(__import__("fastapi").HTTPException) as exc_info:
            _validate_upstream_port(container, 8080)
        assert exc_info.value.status_code == 422
        assert "8080" in exc_info.value.detail
        assert "80" in exc_info.value.detail  # lists available ports

    def test_port_in_host_bindings_passes(self):
        from app.routers.services import _validate_upstream_port

        container = _make_container(port_bindings={"3000/tcp": [{"HostPort": "3000"}]})
        _validate_upstream_port(container, 3000)

    def test_no_exposed_ports_allows_any(self):
        """When container has no exposed port metadata, any port is accepted."""
        from app.routers.services import _validate_upstream_port

        container = _make_container()  # empty ExposedPorts
        _validate_upstream_port(container, 9999)

    def test_merged_exposed_and_bindings(self):
        """Port found in either ExposedPorts or PortBindings should pass."""
        from app.routers.services import _validate_upstream_port

        container = _make_container(
            exposed_ports={"80/tcp": {}},
            port_bindings={"8080/tcp": [{"HostPort": "8080"}]},
        )
        _validate_upstream_port(container, 80)
        _validate_upstream_port(container, 8080)

    def test_rejects_port_when_others_exist(self):
        """When known ports exist, an unlisted port is rejected."""
        from app.routers.services import _validate_upstream_port

        container = _make_container(
            exposed_ports={"80/tcp": {}},
            port_bindings={"8080/tcp": [{"HostPort": "8080"}]},
        )
        with pytest.raises(__import__("fastapi").HTTPException) as exc_info:
            _validate_upstream_port(container, 3000)
        assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# 3. HTTPS probe in health checks
# ---------------------------------------------------------------------------


class TestHttpsProbeHealthCheck:
    """Health checker should include an https_probe_ok subcheck."""

    def test_https_probe_in_check_keys(self, db_session):
        """run_health_checks should return https_probe_ok in the result dict."""
        from app.health.health_checker import run_health_checks
        from app.models.service import Service
        from app.models.service_status import ServiceStatus

        svc = Service(
            name="Test",
            upstream_container_id="c1",
            upstream_container_name="test",
            upstream_scheme="http",
            upstream_port=80,
            hostname="test.example.com",
            base_domain="example.com",
            edge_container_name="edge_test",
            network_name="edge_net_test",
            ts_hostname="edge-test",
        )
        db_session.add(svc)
        db_session.flush()
        db_session.add(ServiceStatus(service_id=svc.id, phase="healthy"))
        db_session.commit()

        # Mock Docker client to avoid real connections
        with patch("app.health.health_checker._get_docker_client") as mock_dc:
            mock_client = MagicMock()
            mock_dc.return_value = mock_client
            # Make all Docker checks return something reasonable
            mock_client.containers.get.side_effect = docker.errors.NotFound("nope")

            checks = run_health_checks(db_session, svc, "/tmp/gen", "/tmp/certs")

        assert "https_probe_ok" in checks

    def test_https_probe_false_when_no_ip(self, db_session):
        """Without a Tailscale IP, the probe should be False."""
        from app.health.health_checker import _check_https_probe
        from app.models.service import Service

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="t.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )
        assert _check_https_probe(svc, None, "/tmp/certs") is False

    @patch("httpx.get")
    def test_https_probe_success(self, mock_get, db_session, tmp_path):
        """When HTTPS probe returns 200, the check should be True."""
        from app.health.health_checker import _check_https_probe
        from app.models.service import Service

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="probe.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        # Create fake cert files
        cert_dir = tmp_path / "probe.example.com"
        cert_dir.mkdir()
        (cert_dir / "fullchain.pem").write_text("fake")
        (cert_dir / "privkey.pem").write_text("fake")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        result = _check_https_probe(svc, "100.64.0.1", str(tmp_path))
        assert result is True
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "100.64.0.1" in call_args[0][0]

    @patch("httpx.get")
    def test_https_probe_5xx_is_failure(self, mock_get, tmp_path):
        """A 5xx response means the probe failed."""
        from app.health.health_checker import _check_https_probe
        from app.models.service import Service

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="fail.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        cert_dir = tmp_path / "fail.example.com"
        cert_dir.mkdir()
        (cert_dir / "fullchain.pem").write_text("fake")
        (cert_dir / "privkey.pem").write_text("fake")

        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_get.return_value = mock_resp

        assert _check_https_probe(svc, "100.64.0.1", str(tmp_path)) is False

    @patch("httpx.get")
    def test_https_probe_connection_error_is_failure(self, mock_get, tmp_path):
        """A connection error means the probe failed gracefully."""
        from app.health.health_checker import _check_https_probe
        from app.models.service import Service

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="err.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        cert_dir = tmp_path / "err.example.com"
        cert_dir.mkdir()
        (cert_dir / "fullchain.pem").write_text("fake")
        (cert_dir / "privkey.pem").write_text("fake")

        mock_get.side_effect = Exception("connection refused")

        assert _check_https_probe(svc, "100.64.0.1", str(tmp_path)) is False

    def test_https_probe_is_warning_check(self):
        """https_probe_ok should be in WARNING_CHECKS, not CRITICAL."""
        from app.health.health_checker import CRITICAL_CHECKS, WARNING_CHECKS

        assert "https_probe_ok" in WARNING_CHECKS
        assert "https_probe_ok" not in CRITICAL_CHECKS

    def test_docker_unavailable_includes_probe(self, db_session, tmp_path):
        """When Docker is unavailable, the fallback dict should include https_probe_ok."""
        from app.health.health_checker import run_health_checks
        from app.models.service import Service
        from app.models.service_status import ServiceStatus

        svc = Service(
            name="T", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="t.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )
        db_session.add(svc)
        db_session.flush()
        db_session.add(ServiceStatus(service_id=svc.id, phase="pending"))
        db_session.commit()

        gen_dir = str(tmp_path / "gen")
        certs_dir = str(tmp_path / "certs")

        with patch(
            "app.health.health_checker._get_docker_client",
            side_effect=Exception("no docker"),
        ):
            checks = run_health_checks(db_session, svc, gen_dir, certs_dir)

        assert "https_probe_ok" in checks
        assert checks["https_probe_ok"] is False


# ---------------------------------------------------------------------------
# 4. Disable with DNS cleanup
# ---------------------------------------------------------------------------


class TestDisableDnsCleanup:
    """spec §7.4 — disable may optionally remove DNS records."""

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_without_cleanup_dns(self, mock_stop, client):
        """Default disable should NOT touch DNS."""
        svc_id = _create_service(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.edge.container_manager.stop_edge")
    def test_disable_with_cleanup_dns(self, mock_stop, mock_secret, mock_cleanup, client, db_session):
        """disable?cleanup_dns=true should attempt to remove the DNS record."""
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable?cleanup_dns=true")
        assert resp.status_code == 200
        mock_cleanup.assert_called_once()

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value=None)
    @patch("app.edge.container_manager.stop_edge")
    def test_disable_cleanup_dns_no_token_is_noop(self, mock_stop, mock_secret, mock_cleanup, client):
        """If no CF token is configured, cleanup_dns=true is a harmless no-op."""
        svc_id = _create_service(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable?cleanup_dns=true")
        assert resp.status_code == 200
        mock_cleanup.assert_not_called()

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_still_stops_edge(self, mock_stop, client):
        """Disable should always attempt to stop the edge container."""
        svc_id = _create_service(client).json()["id"]
        client.post(f"/api/services/{svc_id}/disable")
        mock_stop.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Unused import cleanup verification
# ---------------------------------------------------------------------------


class TestUnusedImportCleanup:
    """Verify stale imports have been removed from endpoints."""

    def test_health_check_full_no_unused_imports(self):
        """health-check-full should not import app_settings or DnsRecord."""
        import ast
        from pathlib import Path

        source = Path(__file__).parent.parent / "app" / "routers" / "services.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))

        # Find the full_health_check function
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "full_health_check":
                body_source = ast.dump(node)
                # Should not contain import of app_settings
                assert "app_settings" not in body_source or "app.config" not in body_source
                # Should not contain import of DnsRecord
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom):
                        names = [alias.name for alias in child.names]
                        if child.module and "config" in child.module:
                            assert "settings" not in names, \
                                "app_settings is imported but unused in full_health_check"

    def test_update_edge_no_unused_imports(self):
        """update-edge should not import app_settings."""
        import ast
        from pathlib import Path

        source = Path(__file__).parent.parent / "app" / "routers" / "services.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_edge_endpoint":
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom):
                        if child.module and "config" in child.module:
                            names = [alias.name for alias in child.names]
                            assert "settings" not in names, \
                                "app_settings is imported but unused in update_edge_endpoint"
