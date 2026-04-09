"""Tests for health checker subchecks and aggregation."""

import json
from unittest.mock import MagicMock, patch

import docker.errors

from app.health.health_checker import aggregate_status, run_health_checks
from app.models.dns_record import DnsRecord
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service(db, **overrides):
    defaults = {
        "name": "TestApp", "upstream_container_id": "abc123",
        "upstream_container_name": "testapp", "upstream_scheme": "http",
        "upstream_port": 80, "hostname": "testapp.example.com",
        "base_domain": "example.com", "edge_container_name": "edge_testapp",
        "network_name": "edge_net_testapp", "ts_hostname": "edge-testapp",
    }
    defaults.update(overrides)
    svc = Service(**defaults)
    db.add(svc)
    db.flush()
    db.add(ServiceStatus(service_id=svc.id, phase="pending"))
    db.commit()
    return svc


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


class TestAggregateStatus:
    def test_all_pass(self):
        checks = {
            "upstream_container_present": True, "upstream_network_connected": True,
            "edge_container_present": True, "edge_container_running": True,
            "tailscale_ready": True, "tailscale_ip_present": True,
            "cert_present": True, "cert_not_expiring": True,
            "dns_record_present": True, "dns_matches_ip": True,
            "caddy_config_present": True,
        }
        assert aggregate_status(checks) == "healthy"

    def test_critical_fail_gives_error(self):
        checks = {"edge_container_present": False, "edge_container_running": False}
        assert aggregate_status(checks) == "error"

    def test_warning_check_fails(self):
        checks = {
            "edge_container_present": True, "edge_container_running": True,
            "tailscale_ip_present": True, "cert_present": True,
            "cert_not_expiring": False,
        }
        assert aggregate_status(checks) == "warning"

    def test_critical_overrides_warning(self):
        checks = {
            "edge_container_present": True, "edge_container_running": True,
            "tailscale_ip_present": False,
            "cert_present": True, "cert_not_expiring": False,
        }
        assert aggregate_status(checks) == "error"

    def test_empty_checks_healthy(self):
        assert aggregate_status({}) == "healthy"


class TestRunHealthChecks:
    @patch("app.health.health_checker._get_docker_client")
    def test_all_healthy(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)

        status = db_session.get(ServiceStatus, svc.id)
        status.tailscale_ip = "100.64.0.1"

        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1")
        db_session.add(dns)
        db_session.commit()

        generated_dir = tmp_data_dir / "generated"
        certs_dir = tmp_data_dir / "certs"

        (generated_dir / svc.id).mkdir(parents=True, exist_ok=True)
        (generated_dir / svc.id / "Caddyfile").write_text("test")

        (certs_dir / svc.hostname).mkdir(parents=True, exist_ok=True)
        (certs_dir / svc.hostname / "fullchain.pem").write_text("cert")
        (certs_dir / svc.hostname / "privkey.pem").write_text("key")

        client = mock_docker.return_value

        upstream_container = MagicMock()
        upstream_container.attrs = {"NetworkSettings": {"Networks": {"edge_net_testapp": {}}}}

        edge_container = MagicMock()
        edge_container.status = "running"

        ts_output = json.dumps({"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.0.1"]}})
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.output = ts_output.encode()
        edge_container.exec_run.return_value = exec_result

        def get_container(name):
            if name == svc.upstream_container_id:
                return upstream_container
            if name == svc.edge_container_name:
                return edge_container
            raise Exception(f"Not found: {name}")

        client.containers.get.side_effect = get_container

        with patch("app.health.health_checker._check_cert_not_expiring", return_value=True):
            checks = run_health_checks(db_session, svc, generated_dir, certs_dir)

        assert checks["upstream_container_present"] is True
        assert checks["upstream_network_connected"] is True
        assert checks["edge_container_present"] is True
        assert checks["edge_container_running"] is True
        assert checks["tailscale_ready"] is True
        assert checks["tailscale_ip_present"] is True
        assert checks["cert_present"] is True
        assert checks["dns_record_present"] is True
        assert checks["dns_matches_ip"] is True
        assert checks["caddy_config_present"] is True

    @patch("app.health.health_checker._get_docker_client")
    def test_missing_upstream(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        client = mock_docker.return_value
        client.containers.get.side_effect = docker.errors.NotFound("not found")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        assert checks["upstream_container_present"] is False
        assert checks["upstream_network_connected"] is False
        assert checks["edge_container_present"] is False

    @patch("app.health.health_checker._get_docker_client")
    def test_edge_not_running(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        client = mock_docker.return_value

        upstream = MagicMock()
        upstream.attrs = {"NetworkSettings": {"Networks": {}}}

        edge = MagicMock()
        edge.status = "exited"

        def get_container(name):
            if name == svc.upstream_container_id:
                return upstream
            if name == svc.edge_container_name:
                return edge
            raise Exception("not found")

        client.containers.get.side_effect = get_container

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        assert checks["edge_container_present"] is True
        assert checks["edge_container_running"] is False
        assert checks["tailscale_ready"] is False
        assert checks["tailscale_ip_present"] is False

    def test_docker_unavailable(self, db_session, tmp_data_dir):
        svc = _create_service(db_session)

        with patch("app.health.health_checker._get_docker_client", side_effect=Exception("Docker down")):
            checks = run_health_checks(
                db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
            )

        assert checks["upstream_container_present"] is False
        assert checks["edge_container_present"] is False
        assert checks["edge_container_running"] is False

    @patch("app.health.health_checker._get_docker_client")
    def test_no_dns_record(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("nf")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        assert checks["dns_record_present"] is False
        assert checks["dns_matches_ip"] is False

    @patch("app.health.health_checker._get_docker_client")
    def test_caddy_config_missing(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("nf")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        assert checks["caddy_config_present"] is False


# ---------------------------------------------------------------------------
# Cloudflare import fix
# ---------------------------------------------------------------------------


class TestCloudflareImportFix:
    """Verify the health-check-full endpoint imports from cloudflare_adapter."""

    def test_import_path_is_correct(self):
        from app.adapters.cloudflare_adapter import find_record
        assert callable(find_record)

    @patch("app.health.health_checker.run_health_checks")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    @patch("app.adapters.cloudflare_adapter.find_record")
    def test_health_check_full_calls_cloudflare_adapter(
        self, mock_find, mock_secret, mock_checks, client, db_session
    ):
        from app.settings_store import set_setting

        mock_checks.return_value = {"edge_container_present": True}
        mock_find.return_value = {"content": "100.64.0.1"}

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/health-check-full")
        assert resp.status_code == 200
        data = resp.json()
        assert "cf_record_exists" in data["extended"]
        assert data["extended"]["cf_record_exists"] is True


# ---------------------------------------------------------------------------
# HTTPS probe in health checks
# ---------------------------------------------------------------------------


class TestHttpsProbeHealthCheck:
    """Health checker should include an https_probe_ok subcheck."""

    def test_https_probe_in_check_keys(self, db_session):
        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="test", upstream_scheme="http",
            upstream_port=80, hostname="test.example.com",
            base_domain="example.com", edge_container_name="edge_test",
            network_name="edge_net_test", ts_hostname="edge-test",
        )
        db_session.add(svc)
        db_session.flush()
        db_session.add(ServiceStatus(service_id=svc.id, phase="healthy"))
        db_session.commit()

        with patch("app.health.health_checker._get_docker_client") as mock_dc:
            mock_client = MagicMock()
            mock_dc.return_value = mock_client
            mock_client.containers.get.side_effect = docker.errors.NotFound("nope")
            checks = run_health_checks(db_session, svc, "/tmp/gen", "/tmp/certs")

        assert "https_probe_ok" in checks

    def test_https_probe_false_when_no_ip(self, db_session):
        from app.health.health_checker import _check_https_probe

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
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="probe.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

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
        from app.health.health_checker import _check_https_probe

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
        from app.health.health_checker import _check_https_probe

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
        from app.health.health_checker import CRITICAL_CHECKS, WARNING_CHECKS
        assert "https_probe_ok" in WARNING_CHECKS
        assert "https_probe_ok" not in CRITICAL_CHECKS

    def test_docker_unavailable_includes_probe(self, db_session, tmp_path):
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
# Full health-check endpoint
# ---------------------------------------------------------------------------


class TestFullHealthCheck:
    """Test the health-check-full API endpoint."""

    def _create(self, client):
        body = {
            "name": "App", "upstream_container_id": "abc123",
            "upstream_container_name": "app", "upstream_scheme": "http",
            "upstream_port": 80, "hostname": "app.example.com",
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
        assert data["extended"]["cf_error"]

    def test_full_health_check_404_for_missing(self, client):
        resp = client.post("/api/services/svc_nonexistent/health-check-full")
        assert resp.status_code == 404
