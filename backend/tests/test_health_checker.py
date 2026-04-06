"""Tests for health checker subchecks and aggregation."""

import json
from unittest.mock import MagicMock, patch

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
            "cert_not_expiring": False,  # warning
        }
        assert aggregate_status(checks) == "warning"

    def test_critical_overrides_warning(self):
        checks = {
            "edge_container_present": True, "edge_container_running": True,
            "tailscale_ip_present": False,  # critical
            "cert_present": True, "cert_not_expiring": False,  # warning
        }
        assert aggregate_status(checks) == "error"

    def test_empty_checks_healthy(self):
        assert aggregate_status({}) == "healthy"


class TestRunHealthChecks:
    @patch("app.health.health_checker._get_docker_client")
    def test_all_healthy(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)

        # Set up tailscale IP in status
        status = db_session.get(ServiceStatus, svc.id)
        status.tailscale_ip = "100.64.0.1"

        # Create DNS record
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1")
        db_session.add(dns)
        db_session.commit()

        generated_dir = tmp_data_dir / "generated"
        certs_dir = tmp_data_dir / "certs"

        # Create Caddyfile
        (generated_dir / svc.id).mkdir(parents=True, exist_ok=True)
        (generated_dir / svc.id / "Caddyfile").write_text("test")

        # Create cert files (keyed by hostname, matching renewal_task + health checker)
        (certs_dir / svc.hostname).mkdir(parents=True, exist_ok=True)
        (certs_dir / svc.hostname / "fullchain.pem").write_text("cert")
        (certs_dir / svc.hostname / "privkey.pem").write_text("key")

        # Mock Docker client
        client = mock_docker.return_value

        # Upstream container exists and is on network
        upstream_container = MagicMock()
        upstream_container.attrs = {"NetworkSettings": {"Networks": {"edge_net_testapp": {}}}}

        # Edge container exists and running
        edge_container = MagicMock()
        edge_container.status = "running"

        # Tailscale is ready — exec_run("tailscale status --json") returns ExecResult-like object
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

        # Mock cert expiry
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

        import docker.errors
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
        import docker.errors
        mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("nf")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        assert checks["dns_record_present"] is False
        assert checks["dns_matches_ip"] is False

    @patch("app.health.health_checker._get_docker_client")
    def test_caddy_config_missing(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        import docker.errors
        mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("nf")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        assert checks["caddy_config_present"] is False
