"""Run-health-check orchestration tests."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import docker.errors

from app.health import health_checker, runner
from app.health.checks import certs as cert_checks
from app.health.checks import config as config_checks
from app.health.checks import docker as docker_checks
from app.health.checks import tailscale as tailscale_checks
from app.models.dns_record import DnsRecord
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.settings_store import set_setting

from ._services_helpers import _create_service_in_db as create_service_in_db


def _runtime_dirs(tmp_data_dir):
    return tmp_data_dir / "generated", tmp_data_dir / "certs"


def _write_caddy_config(generated_dir, service):
    service_dir = generated_dir / service.id
    service_dir.mkdir(parents=True, exist_ok=True)
    (service_dir / "Caddyfile").write_text("test")


def _write_cert_files(certs_dir, service):
    current = certs_dir / service.hostname / "current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "fullchain.pem").write_text("cert")
    (current / "privkey.pem").write_text("key")


class TestRunHealthChecks:
    @patch("app.health.runner.connect")
    def test_all_healthy(self, mock_connect, db_session, tmp_data_dir):
        service = create_service_in_db(db_session)
        status = db_session.get(ServiceStatus, service.id)
        status.tailscale_ip = "100.64.0.1"
        db_session.add(
            DnsRecord(
                service_id=service.id,
                hostname=service.hostname,
                record_id="r1",
                value="100.64.0.1",
            )
        )
        db_session.commit()

        generated_dir, certs_dir = _runtime_dirs(tmp_data_dir)
        _write_caddy_config(generated_dir, service)
        _write_cert_files(certs_dir, service)

        client = mock_connect.return_value
        upstream = MagicMock()
        upstream.attrs = {"NetworkSettings": {"Networks": {service.network_name: {}}}}
        edge = MagicMock()
        edge.status = "running"
        tailscale_result = MagicMock()
        tailscale_result.exit_code = 0
        tailscale_result.output = json.dumps(
            {"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.0.1"]}}
        ).encode()
        edge.exec_run.return_value = tailscale_result

        def get_container(name):
            if name == service.upstream_container_id:
                return upstream
            if name == service.edge_container_name:
                return edge
            raise docker.errors.NotFound("not found")

        client.containers.get.side_effect = get_container

        with (
            patch.object(cert_checks, "_check_cert_not_expiring", return_value=True),
            patch("app.health.probe.check_https_probe", return_value=True) as mock_probe,
        ):
            checks = health_checker.run_health_checks(db_session, service, generated_dir, certs_dir)

        assert checks["upstream_container_present"] is True
        assert checks["upstream_network_connected"] is True
        assert checks["edge_container_present"] is True
        assert checks["edge_container_running"] is True
        assert checks["tailscale_ready"] is True
        assert checks["tailscale_ip_present"] is True
        assert checks["cert_present"] is True
        assert checks["cert_not_expiring"] is True
        assert checks["dns_record_present"] is True
        assert checks["dns_matches_ip"] is True
        assert checks["caddy_config_present"] is True
        assert checks["https_probe_ok"] is True
        mock_probe.assert_called_once_with(service, "100.64.0.1", client)

    @patch("app.health.runner.connect")
    def test_missing_upstream(self, mock_connect, db_session, tmp_data_dir):
        service = create_service_in_db(db_session)
        client = mock_connect.return_value
        client.containers.get.side_effect = docker.errors.NotFound("not found")

        checks = health_checker.run_health_checks(
            db_session,
            service,
            tmp_data_dir / "generated",
            tmp_data_dir / "certs",
        )

        assert checks["upstream_container_present"] is False
        assert checks["upstream_network_connected"] is False
        assert checks["edge_container_present"] is False

    @patch("app.health.runner.connect")
    def test_edge_not_running_blocks_tailscale(self, mock_connect, db_session, tmp_data_dir):
        service = create_service_in_db(db_session)
        client = mock_connect.return_value
        upstream = MagicMock()
        upstream.attrs = {"NetworkSettings": {"Networks": {}}}
        edge = MagicMock()
        edge.status = "exited"

        def get_container(name):
            if name == service.upstream_container_id:
                return upstream
            if name == service.edge_container_name:
                return edge
            raise docker.errors.NotFound("not found")

        client.containers.get.side_effect = get_container

        checks = health_checker.run_health_checks(
            db_session,
            service,
            tmp_data_dir / "generated",
            tmp_data_dir / "certs",
        )

        assert checks["edge_container_present"] is True
        assert checks["edge_container_running"] is False
        assert checks["tailscale_ready"] is False
        assert checks["tailscale_ip_present"] is False

    @patch("app.health.runner.connect")
    def test_edge_name_conflict_with_other_service_is_not_healthy(
        self,
        mock_connect,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)
        client = mock_connect.return_value
        upstream = MagicMock()
        upstream.attrs = {"NetworkSettings": {"Networks": {}}}
        wrong_edge = MagicMock()
        wrong_edge.status = "running"
        wrong_edge.labels = {"tailbale.service_id": "other"}

        def get_container(name):
            if name == service.upstream_container_id:
                return upstream
            if name == service.edge_container_name:
                return wrong_edge
            raise docker.errors.NotFound("not found")

        client.containers.get.side_effect = get_container
        client.containers.list.return_value = []

        checks = health_checker.run_health_checks(
            db_session,
            service,
            tmp_data_dir / "generated",
            tmp_data_dir / "certs",
        )

        assert checks["edge_container_present"] is False
        assert checks["edge_container_running"] is False
        assert checks["tailscale_ready"] is False
        assert wrong_edge.exec_run.call_count == 0

    def test_docker_unavailable_marks_docker_dependent_checks_false(
        self,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)

        with patch.object(runner, "connect", side_effect=Exception("Docker down")):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
            )

        assert checks["upstream_container_present"] is False
        assert checks["edge_container_present"] is False
        assert checks["edge_container_running"] is False

    def test_docker_unavailable_still_reports_offline_checks(self, db_session, tmp_data_dir):
        service = create_service_in_db(db_session)
        db_session.add(
            DnsRecord(
                service_id=service.id,
                hostname=service.hostname,
                record_id="r1",
                value="100.64.0.1",
            )
        )
        db_session.commit()

        generated_dir, certs_dir = _runtime_dirs(tmp_data_dir)
        _write_caddy_config(generated_dir, service)
        _write_cert_files(certs_dir, service)

        with (
            patch.object(runner, "connect", side_effect=Exception("Docker down")),
            patch(
                "app.certs.cert_manager.get_cert_expiry",
                return_value=datetime.now(UTC) + timedelta(days=60),
            ),
        ):
            checks = health_checker.run_health_checks(db_session, service, generated_dir, certs_dir)

        assert checks["upstream_container_present"] is False
        assert checks["edge_container_running"] is False
        assert checks["tailscale_ready"] is False
        assert checks["https_probe_ok"] is False
        assert checks["cert_present"] is True
        assert checks["cert_not_expiring"] is True
        assert checks["dns_record_present"] is True
        assert checks["caddy_config_present"] is True
        assert checks["dns_matches_ip"] is False

    def test_live_tailscale_ip_overrides_stored_status_for_dns_and_probe(
        self,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)
        status = db_session.get(ServiceStatus, service.id)
        status.tailscale_ip = "100.64.0.1"
        db_session.add(
            DnsRecord(
                service_id=service.id,
                hostname=service.hostname,
                record_id="r1",
                value="100.64.0.99",
            )
        )
        db_session.commit()

        with (
            patch.object(runner, "connect", return_value=MagicMock()),
            patch.object(docker_checks, "_check_upstream_present", return_value=True),
            patch.object(docker_checks, "_check_upstream_network", return_value=True),
            patch.object(docker_checks, "_check_edge", return_value=(True, True)),
            patch.object(tailscale_checks, "_check_tailscale", return_value=(True, True, "100.64.0.99")),
            patch.object(cert_checks, "_check_cert_present", return_value=True),
            patch.object(cert_checks, "_check_cert_not_expiring", return_value=True),
            patch.object(config_checks, "_check_caddy_config", return_value=True),
            patch("app.health.probe.check_https_probe", return_value=True) as mock_probe,
        ):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
            )

        assert checks["dns_matches_ip"] is True
        mock_probe.assert_called_once()
        assert mock_probe.call_args.args[1] == "100.64.0.99"

    def test_missing_live_tailscale_ip_ignores_stale_status_ip(self, db_session, tmp_data_dir):
        service = create_service_in_db(db_session)
        status = db_session.get(ServiceStatus, service.id)
        status.tailscale_ip = "100.64.0.1"
        db_session.add(
            DnsRecord(
                service_id=service.id,
                hostname=service.hostname,
                record_id="r1",
                value="100.64.0.1",
            )
        )
        db_session.commit()

        with (
            patch.object(runner, "connect", return_value=MagicMock()),
            patch.object(docker_checks, "_check_upstream_present", return_value=True),
            patch.object(docker_checks, "_check_upstream_network", return_value=True),
            patch.object(docker_checks, "_check_edge", return_value=(True, True)),
            patch.object(tailscale_checks, "_check_tailscale", return_value=(False, False, None)),
            patch.object(cert_checks, "_check_cert_present", return_value=True),
            patch.object(cert_checks, "_check_cert_not_expiring", return_value=True),
            patch.object(config_checks, "_check_caddy_config", return_value=True),
            patch("app.health.probe.check_https_probe", return_value=False) as mock_probe,
        ):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
            )

        assert checks["tailscale_ip_present"] is False
        assert checks["dns_matches_ip"] is False
        mock_probe.assert_called_once()
        assert mock_probe.call_args.args[1] is None

    @patch("app.health.runner.connect")
    def test_corrupt_renewal_window_does_not_crash_health_check(
        self,
        mock_connect,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)

        set_setting(db_session, "cert_renewal_window_days", "0")
        db_session.commit()
        client = mock_connect.return_value
        client.containers.get.side_effect = docker.errors.NotFound("nf")

        checks = health_checker.run_health_checks(
            db_session,
            service,
            tmp_data_dir / "generated",
            tmp_data_dir / "certs",
        )

        assert checks["cert_not_expiring"] is False
        assert set(checks) == set(health_checker.CRITICAL_CHECKS) | set(
            health_checker.WARNING_CHECKS
        )

    def test_corrupt_renewal_window_does_not_crash_offline_fallback(
        self,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)

        db_session.add(
            DnsRecord(
                service_id=service.id,
                hostname=service.hostname,
                record_id="r1",
                value="100.64.0.1",
            )
        )
        set_setting(db_session, "cert_renewal_window_days", "0")
        db_session.commit()

        generated_dir, certs_dir = _runtime_dirs(tmp_data_dir)
        _write_caddy_config(generated_dir, service)

        with patch.object(runner, "connect", side_effect=Exception("Docker down")):
            checks = health_checker.run_health_checks(db_session, service, generated_dir, certs_dir)

        assert checks["cert_not_expiring"] is False
        assert checks["caddy_config_present"] is True
        assert checks["dns_record_present"] is True
        assert set(checks) == set(health_checker.CRITICAL_CHECKS) | set(
            health_checker.WARNING_CHECKS
        )

    def test_https_probe_in_check_keys(self, db_session):
        service = create_service_in_db(db_session)

        with patch.object(runner, "connect") as mock_connect:
            client = mock_connect.return_value
            client.containers.get.side_effect = docker.errors.NotFound("nope")
            checks = health_checker.run_health_checks(db_session, service, "/tmp/gen", "/tmp/certs")

        assert "https_probe_ok" in checks

    def test_docker_unavailable_includes_probe(self, db_session, tmp_path):
        service = create_service_in_db(db_session)

        with patch.object(runner, "connect", side_effect=Exception("no docker")):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_path / "gen",
                tmp_path / "certs",
            )

        assert "https_probe_ok" in checks
        assert checks["https_probe_ok"] is False


class TestEdgeLookupResilienceOnTransientDaemonFault:
    def _service(self):
        service = Service(
            name="T",
            upstream_container_id="c1",
            upstream_container_name="t",
            upstream_scheme="http",
            upstream_port=80,
            hostname="t.example.com",
            base_domain="example.com",
            edge_container_name="edge_t",
            network_name="n",
            ts_hostname="ts",
        )
        service.id = "svc-transient"
        return service

    def _client_named_faults_label_finds(self, container):
        client = MagicMock()
        client.containers.get.side_effect = docker.errors.APIError(
            "500 Server Error: daemon busy"
        )
        client.containers.list.return_value = [container]
        return client

    def test_check_edge_recovers_via_label_search(self):
        running = MagicMock()
        running.status = "running"
        client = self._client_named_faults_label_finds(running)

        assert health_checker._check_edge(client, self._service()) == (True, True)
        client.containers.list.assert_called_once()

    def test_check_tailscale_recovers_via_label_search(self):
        edge = MagicMock()
        edge.status = "running"
        result = MagicMock()
        result.exit_code = 0
        result.output = json.dumps(
            {"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.0.5"]}}
        ).encode()
        edge.exec_run.return_value = result
        client = self._client_named_faults_label_finds(edge)

        ready, ip_present, ip = health_checker._check_tailscale(
            client,
            self._service(),
            edge_running=True,
        )
        assert (ready, ip_present, ip) == (True, True, "100.64.0.5")

    def test_notfound_is_not_masked_by_tolerance(self):
        client = MagicMock()
        client.containers.get.side_effect = docker.errors.NotFound("no such container")
        client.containers.list.return_value = []

        assert health_checker._check_edge(client, self._service()) == (False, False)


class TestGetLiveTailscaleIp:
    """Standalone live-IP helper used by the manual full health check to verify
    live Cloudflare DNS against the current tailnet IP (not the persisted, and
    potentially stale, ServiceStatus.tailscale_ip). It reuses the exact live-IP
    path run_health_checks follows internally: connect -> _check_edge ->
    _check_tailscale."""

    def _edge_with_ts_ip(self, ip):
        edge = MagicMock()
        edge.status = "running"
        result = MagicMock()
        result.exit_code = 0
        result.output = json.dumps(
            {"BackendState": "Running", "Self": {"TailscaleIPs": [ip]}}
        ).encode()
        edge.exec_run.return_value = result
        return edge

    def test_returns_none_when_docker_unavailable(self, db_session):
        service = create_service_in_db(db_session)
        with patch.object(runner, "connect", side_effect=Exception("no docker")):
            assert health_checker.get_live_tailscale_ip(service) is None

    def test_returns_live_ip_via_real_edge_and_tailscale_helpers(self, db_session):
        service = create_service_in_db(db_session)
        client = MagicMock()
        client.containers.get.return_value = self._edge_with_ts_ip("100.64.0.5")

        with patch.object(runner, "connect", return_value=client):
            assert health_checker.get_live_tailscale_ip(service) == "100.64.0.5"

    def test_returns_none_when_edge_not_running(self, db_session):
        service = create_service_in_db(db_session)
        edge = MagicMock()
        edge.status = "exited"
        client = MagicMock()
        client.containers.get.return_value = edge

        with patch.object(runner, "connect", return_value=client):
            assert health_checker.get_live_tailscale_ip(service) is None

    def test_closes_client_even_on_success(self, db_session):
        # The helper opens its own Docker client; it MUST close it in a finally so
        # the manual full check never leaks a connection per invocation.
        service = create_service_in_db(db_session)
        client = MagicMock()
        client.containers.get.return_value = self._edge_with_ts_ip("100.64.0.9")

        with (
            patch.object(runner, "connect", return_value=client),
            patch.object(runner, "close_client") as mock_close,
        ):
            assert health_checker.get_live_tailscale_ip(service) == "100.64.0.9"

        mock_close.assert_called_once_with(client)
