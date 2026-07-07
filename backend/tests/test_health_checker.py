"""Tests for health checker subchecks and aggregation."""

import json
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from app.health.health_checker import (
    CRITICAL_CHECKS,
    WARNING_CHECKS,
    aggregate_status,
    run_health_checks,
)
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
            "caddy_config_present": True, "https_probe_ok": True,
        }
        assert aggregate_status(checks) == "healthy"

    def test_critical_fail_gives_error(self):
        checks = {"edge_container_present": False, "edge_container_running": False}
        assert aggregate_status(checks) == "error"

    def test_warning_check_fails(self):
        checks = {
            "upstream_container_present": True, "upstream_network_connected": True,
            "edge_container_present": True, "edge_container_running": True,
            "tailscale_ready": True, "tailscale_ip_present": True,
            "cert_present": True, "caddy_config_present": True,
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

    @pytest.mark.parametrize(
        "failed_check",
        [
            "upstream_container_present",
            "upstream_network_connected",
            "tailscale_ready",
            "caddy_config_present",
        ],
    )
    def test_operational_failures_are_errors(self, failed_check):
        checks = {
            "upstream_container_present": True,
            "upstream_network_connected": True,
            "edge_container_present": True,
            "edge_container_running": True,
            "tailscale_ready": True,
            "tailscale_ip_present": True,
            "cert_present": True,
            "caddy_config_present": True,
            "cert_not_expiring": True,
            "dns_record_present": True,
            "dns_matches_ip": True,
            "https_probe_ok": True,
        }
        checks[failed_check] = False
        assert aggregate_status(checks) == "error"

    def test_dns_record_absent_is_warning(self):
        checks = {
            "upstream_container_present": True,
            "upstream_network_connected": True,
            "edge_container_present": True,
            "edge_container_running": True,
            "tailscale_ready": True,
            "tailscale_ip_present": True,
            "cert_present": True,
            "caddy_config_present": True,
            "cert_not_expiring": True,
            "dns_record_present": False,
            "dns_matches_ip": True,
            "https_probe_ok": True,
        }
        assert aggregate_status(checks) == "warning"

    def test_empty_checks_healthy(self):
        assert aggregate_status({}) == "healthy"


class TestRunHealthChecks:
    @patch("app.health.health_checker.connect")
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

        (certs_dir / svc.hostname / "current").mkdir(parents=True, exist_ok=True)
        (certs_dir / svc.hostname / "current" / "fullchain.pem").write_text("cert")
        (certs_dir / svc.hostname / "current" / "privkey.pem").write_text("key")

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

    @patch("app.health.health_checker.connect")
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

    @patch("app.health.health_checker.connect")
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

    @patch("app.health.health_checker.connect")
    def test_edge_name_conflict_with_other_service_is_not_healthy(
        self, mock_docker, db_session, tmp_data_dir
    ):
        svc = _create_service(db_session)
        client = mock_docker.return_value

        upstream = MagicMock()
        upstream.attrs = {"NetworkSettings": {"Networks": {}}}
        wrong_edge = MagicMock()
        wrong_edge.status = "running"
        wrong_edge.labels = {"tailbale.service_id": "other"}

        def get_container(name):
            if name == svc.upstream_container_id:
                return upstream
            if name == svc.edge_container_name:
                return wrong_edge
            raise Exception("not found")

        client.containers.get.side_effect = get_container
        client.containers.list.return_value = []

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )

        assert checks["edge_container_present"] is False
        assert checks["edge_container_running"] is False
        assert checks["tailscale_ready"] is False
        assert wrong_edge.exec_run.call_count == 0


    def test_docker_unavailable(self, db_session, tmp_data_dir):
        svc = _create_service(db_session)

        with patch("app.health.health_checker.connect", side_effect=Exception("Docker down")):
            checks = run_health_checks(
                db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
            )

        assert checks["upstream_container_present"] is False
        assert checks["edge_container_present"] is False
        assert checks["edge_container_running"] is False

    def test_docker_unavailable_still_reports_offline_checks(self, db_session, tmp_data_dir):
        # Regression: when Docker is unreachable, the filesystem- and DB-backed
        # subchecks must still be evaluated. A valid on-disk cert and a stored
        # DNS record were previously forced to False (cert_not_expiring /
        # dns_record_present), inconsistent with cert_present/caddy_config_present
        # which were already computed offline in the same fallback.
        from datetime import UTC, datetime, timedelta

        svc = _create_service(db_session)
        db_session.add(DnsRecord(
            service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1",
        ))
        db_session.commit()

        generated_dir = tmp_data_dir / "generated"
        certs_dir = tmp_data_dir / "certs"
        (generated_dir / svc.id).mkdir(parents=True, exist_ok=True)
        (generated_dir / svc.id / "Caddyfile").write_text("test")
        cur = certs_dir / svc.hostname / "current"
        cur.mkdir(parents=True, exist_ok=True)
        (cur / "fullchain.pem").write_text("cert")
        (cur / "privkey.pem").write_text("key")

        with (
            patch("app.health.health_checker.connect", side_effect=Exception("Docker down")),
            patch(
                "app.certs.cert_manager.get_cert_expiry",
                return_value=datetime.now(UTC) + timedelta(days=60),
            ),
        ):
            checks = run_health_checks(db_session, svc, generated_dir, certs_dir)

        # Docker-dependent checks remain False.
        assert checks["upstream_container_present"] is False
        assert checks["edge_container_running"] is False
        assert checks["tailscale_ready"] is False
        assert checks["https_probe_ok"] is False
        # Offline (disk/DB) checks are evaluated accurately, not forced False.
        assert checks["cert_present"] is True
        assert checks["cert_not_expiring"] is True
        assert checks["dns_record_present"] is True
        assert checks["caddy_config_present"] is True
        # DNS match needs the live Tailscale IP (Docker-only), so it stays False.
        assert checks["dns_matches_ip"] is False

    @patch("app.health.health_checker.connect")
    def test_no_dns_record(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("nf")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        assert checks["dns_record_present"] is False
        assert checks["dns_matches_ip"] is False

    @patch("app.health.health_checker.connect")
    def test_caddy_config_missing(self, mock_docker, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        mock_docker.return_value.containers.get.side_effect = docker.errors.NotFound("nf")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        assert checks["caddy_config_present"] is False

    @patch("app.health.health_checker._check_https_probe", return_value=True)
    @patch("app.health.health_checker._check_caddy_config", return_value=True)
    @patch("app.health.health_checker._check_cert_not_expiring", return_value=True)
    @patch("app.health.health_checker._check_cert_present", return_value=True)
    @patch("app.health.health_checker._check_tailscale", return_value=(True, True, "100.64.0.1"))
    @patch("app.health.health_checker._check_edge", return_value=(True, True))
    @patch("app.health.health_checker._check_upstream_network", return_value=True)
    @patch("app.health.health_checker._check_upstream_present", return_value=True)
    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.health.health_checker.connect")
    def test_live_cloudflare_drift_overrides_db_dns_match(
        self,
        mock_docker,
        mock_find_record,
        _mock_upstream_present,
        _mock_upstream_network,
        _mock_edge,
        _mock_tailscale,
        _mock_cert_present,
        _mock_cert_not_expiring,
        _mock_caddy,
        _mock_https_probe,
        db_session,
        tmp_data_dir,
    ):
        from app.secrets import CLOUDFLARE_TOKEN, write_secret
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.tailscale_ip = "100.64.0.1"
        db_session.add(DnsRecord(
            service_id=svc.id,
            hostname=svc.hostname,
            record_id="r1",
            value="100.64.0.1",
        ))
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()
        write_secret(CLOUDFLARE_TOKEN, "cf-token")
        mock_find_record.return_value = {"content": "100.64.0.99"}

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs", live_dns=True
        )

        assert checks["dns_record_present"] is True
        assert checks["dns_matches_ip"] is False
        mock_find_record.assert_called_once_with("cf-token", "zone123", svc.hostname, "A")

    @patch("app.health.health_checker._check_https_probe", return_value=True)
    @patch("app.health.health_checker._check_caddy_config", return_value=True)
    @patch("app.health.health_checker._check_cert_not_expiring", return_value=True)
    @patch("app.health.health_checker._check_cert_present", return_value=True)
    @patch("app.health.health_checker._check_tailscale", return_value=(True, True, "100.64.0.1"))
    @patch("app.health.health_checker._check_edge", return_value=(True, True))
    @patch("app.health.health_checker._check_upstream_network", return_value=True)
    @patch("app.health.health_checker._check_upstream_present", return_value=True)
    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.health.health_checker.connect")
    def test_live_cloudflare_missing_record_overrides_db_presence(
        self,
        mock_docker,
        mock_find_record,
        _mock_upstream_present,
        _mock_upstream_network,
        _mock_edge,
        _mock_tailscale,
        _mock_cert_present,
        _mock_cert_not_expiring,
        _mock_caddy,
        _mock_https_probe,
        db_session,
        tmp_data_dir,
    ):
        # A live Cloudflare lookup that finds NO record (the A record was deleted
        # out of band) must report dns_record_present=False even though a stale
        # local DnsRecord still exists, so the operator sees the record is truly
        # gone instead of trusting the DB mirror. Distinct from the drift case:
        # here presence flips, not just the IP match.
        from app.secrets import CLOUDFLARE_TOKEN, write_secret
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.tailscale_ip = "100.64.0.1"
        db_session.add(DnsRecord(
            service_id=svc.id,
            hostname=svc.hostname,
            record_id="r1",
            value="100.64.0.1",
        ))
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()
        write_secret(CLOUDFLARE_TOKEN, "cf-token")
        mock_find_record.return_value = None  # record absent in Cloudflare

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs", live_dns=True
        )

        assert checks["dns_record_present"] is False
        assert checks["dns_matches_ip"] is False
        mock_find_record.assert_called_once_with("cf-token", "zone123", svc.hostname, "A")

    @patch("app.health.health_checker._check_https_probe", return_value=True)
    @patch("app.health.health_checker._check_caddy_config", return_value=True)
    @patch("app.health.health_checker._check_cert_not_expiring", return_value=True)
    @patch("app.health.health_checker._check_cert_present", return_value=True)
    @patch("app.health.health_checker._check_tailscale", return_value=(True, True, "100.64.0.1"))
    @patch("app.health.health_checker._check_edge", return_value=(True, True))
    @patch("app.health.health_checker._check_upstream_network", return_value=True)
    @patch("app.health.health_checker._check_upstream_present", return_value=True)
    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.health.health_checker.connect")
    def test_automatic_health_does_not_call_cloudflare(
        self,
        mock_docker,
        mock_find_record,
        _mock_upstream_present,
        _mock_upstream_network,
        _mock_edge,
        _mock_tailscale,
        _mock_cert_present,
        _mock_cert_not_expiring,
        _mock_caddy,
        _mock_https_probe,
        db_session,
        tmp_data_dir,
    ):
        from app.secrets import CLOUDFLARE_TOKEN, write_secret
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.tailscale_ip = "100.64.0.1"
        db_session.add(DnsRecord(
            service_id=svc.id,
            hostname=svc.hostname,
            record_id="r1",
            value="100.64.0.1",
        ))
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()
        write_secret(CLOUDFLARE_TOKEN, "cf-token")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )

        assert checks["dns_matches_ip"] is True
        mock_find_record.assert_not_called()

    def test_live_tailscale_ip_overrides_stored_status_for_dns_and_probe(
        self, db_session, tmp_data_dir
    ):
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.tailscale_ip = "100.64.0.1"
        db_session.add(DnsRecord(
            service_id=svc.id,
            hostname=svc.hostname,
            record_id="r1",
            value="100.64.0.99",
        ))
        db_session.commit()

        with (
            patch("app.health.health_checker.connect", return_value=MagicMock()),
            patch("app.health.health_checker._check_upstream_present", return_value=True),
            patch("app.health.health_checker._check_upstream_network", return_value=True),
            patch("app.health.health_checker._check_edge", return_value=(True, True)),
            patch("app.health.health_checker._check_tailscale", return_value=(True, True, "100.64.0.99")),
            patch("app.health.health_checker._check_cert_present", return_value=True),
            patch("app.health.health_checker._check_cert_not_expiring", return_value=True),
            patch("app.health.health_checker._check_caddy_config", return_value=True),
            patch("app.health.health_checker._check_https_probe", return_value=True) as mock_probe,
        ):
            checks = run_health_checks(
                db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
            )

        assert checks["dns_matches_ip"] is True
        mock_probe.assert_called_once()
        assert mock_probe.call_args.args[1] == "100.64.0.99"

    def test_missing_live_tailscale_ip_ignores_stale_status_ip(
        self, db_session, tmp_data_dir
    ):
        svc = _create_service(db_session)
        status = db_session.get(ServiceStatus, svc.id)
        status.tailscale_ip = "100.64.0.1"
        db_session.add(DnsRecord(
            service_id=svc.id,
            hostname=svc.hostname,
            record_id="r1",
            value="100.64.0.1",
        ))
        db_session.commit()

        with (
            patch("app.health.health_checker.connect", return_value=MagicMock()),
            patch("app.health.health_checker._check_upstream_present", return_value=True),
            patch("app.health.health_checker._check_upstream_network", return_value=True),
            patch("app.health.health_checker._check_edge", return_value=(True, True)),
            patch("app.health.health_checker._check_tailscale", return_value=(False, False, None)),
            patch("app.health.health_checker._check_cert_present", return_value=True),
            patch("app.health.health_checker._check_cert_not_expiring", return_value=True),
            patch("app.health.health_checker._check_caddy_config", return_value=True),
            patch("app.health.health_checker._check_https_probe", return_value=False) as mock_probe,
        ):
            checks = run_health_checks(
                db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
            )

        assert checks["tailscale_ip_present"] is False
        assert checks["dns_matches_ip"] is False
        mock_probe.assert_called_once()
        assert mock_probe.call_args.args[1] is None

    @patch("app.health.health_checker.connect")
    def test_corrupt_renewal_window_does_not_crash_health_check(
        self, mock_docker, db_session, tmp_data_dir
    ):
        # Regression: a corrupt cert_renewal_window_days *global* setting must not
        # crash the whole 12-subcheck sweep. get_positive_int_setting fails loud
        # on a corrupt value (< 1); isolating that read keeps every other subcheck
        # accurate and degrades only cert_not_expiring (a warning check). Before
        # the fix, run_health_checks raised ValueError, silently staling health for
        # EVERY service in the sweep on a single bad global setting.
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        set_setting(db_session, "cert_renewal_window_days", "0")  # corrupt: < 1
        db_session.commit()

        client = mock_docker.return_value
        client.containers.get.side_effect = docker.errors.NotFound("nf")

        checks = run_health_checks(
            db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
        )
        # Returns a dict (no crash); the corrupt-window subcheck reports failing.
        assert checks["cert_not_expiring"] is False
        # Every other subcheck is still evaluated, not lost to the crash.
        assert set(checks) == set(CRITICAL_CHECKS) | set(WARNING_CHECKS)

    def test_corrupt_renewal_window_does_not_crash_offline_fallback(
        self, db_session, tmp_data_dir
    ):
        # Regression: the Docker-unreachable fallback reads the renewal window
        # before the offline disk/DB subchecks, so a corrupt value there would
        # crash cert_present/dns/caddy too. Isolating the read keeps those intact.
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        db_session.add(DnsRecord(
            service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1",
        ))
        set_setting(db_session, "cert_renewal_window_days", "0")
        db_session.commit()

        generated_dir = tmp_data_dir / "generated"
        certs_dir = tmp_data_dir / "certs"
        (generated_dir / svc.id).mkdir(parents=True, exist_ok=True)
        (generated_dir / svc.id / "Caddyfile").write_text("test")

        with patch("app.health.health_checker.connect", side_effect=Exception("Docker down")):
            checks = run_health_checks(db_session, svc, generated_dir, certs_dir)

        assert checks["cert_not_expiring"] is False
        # Offline disk/DB subchecks after the window read are still evaluated.
        assert checks["caddy_config_present"] is True
        assert checks["dns_record_present"] is True
        assert set(checks) == set(CRITICAL_CHECKS) | set(WARNING_CHECKS)

class TestEdgeLookupResilienceOnTransientDaemonFault:
    """HE-R2-1 regression: the health path must stay resilient to a *transient*
    non-``NotFound`` Docker fault on the named ``containers.get`` lookup.

    Pre-refactor, health's own ``_find_edge_container_for_health`` caught a broad
    ``Exception`` on the named lookup, then fell through to the label search, so a
    momentary daemon hiccup (APIError / connection reset) that still leaves the
    container discoverable by its ``tailbale.service_id`` label reported the edge
    accurately. AR16/AR7 routed health through the shared ``find_edge_container``,
    which (correctly, for the edge *lifecycle* callers) only swallows ``NotFound``
    and re-raises everything else. That silently narrowed the health path: a
    transient APIError now degrades a perfectly-running service to ``error``.

    The fix opts the health callsites into ``tolerate_lookup_errors=True`` so the
    broad-then-label-fallback behavior is restored for health only, while the
    lifecycle callers keep propagating (no duplicate-container footgun). A
    ``NotFound`` still means "absent"; only the label search can recover it.
    """

    def _svc(self):
        svc = Service(
            name="T", upstream_container_id="c1", upstream_container_name="t",
            upstream_scheme="http", upstream_port=80, hostname="t.example.com",
            base_domain="example.com", edge_container_name="edge_t",
            network_name="n", ts_hostname="ts",
        )
        svc.id = "svc-transient"
        return svc

    def _client_named_faults_label_finds(self, container):
        """Docker client whose named ``get`` raises a transient APIError but whose
        label ``list`` still finds *container*."""
        client = MagicMock()
        client.containers.get.side_effect = docker.errors.APIError(
            "500 Server Error: daemon busy"
        )
        client.containers.list.return_value = [container]
        return client

    def test_check_edge_recovers_via_label_search(self):
        from app.health.health_checker import _check_edge

        running = MagicMock()
        running.status = "running"
        client = self._client_named_faults_label_finds(running)

        # Fail-before: the shared lookup re-raised the APIError, _check_edge's own
        # except returned (False, False) — a running edge misreported as absent.
        assert _check_edge(client, self._svc()) == (True, True)
        client.containers.list.assert_called_once()

    def test_check_tailscale_recovers_via_label_search(self):
        from app.health.health_checker import _check_tailscale

        edge = MagicMock()
        edge.status = "running"
        exec_result = MagicMock()
        exec_result.exit_code = 0
        exec_result.output = json.dumps(
            {"BackendState": "Running", "Self": {"TailscaleIPs": ["100.64.0.5"]}}
        ).encode()
        edge.exec_run.return_value = exec_result
        client = self._client_named_faults_label_finds(edge)

        ready, ip_present, ip = _check_tailscale(client, self._svc(), edge_running=True)
        assert (ready, ip_present, ip) == (True, True, "100.64.0.5")

    def test_check_https_probe_recovers_via_label_search(self):
        from app.health.health_checker import _check_https_probe

        edge = MagicMock()
        edge.status = "running"
        # _check_https_probe unpacks ``exit_code, output = container.exec_run(...)``,
        # so exec_run must return a 2-tuple (unlike _check_tailscale, which reads
        # ``.exit_code``/``.output`` attributes off the result object).
        edge.exec_run.return_value = (0, b"200")
        client = self._client_named_faults_label_finds(edge)

        assert _check_https_probe(self._svc(), "100.64.0.5", client) is True

    def test_notfound_is_not_masked_by_tolerance(self):
        # Tolerance only broadens the *named* lookup; a genuine NotFound with no
        # label match still means the edge is absent (not a false positive).
        from app.health.health_checker import _check_edge

        client = MagicMock()
        client.containers.get.side_effect = docker.errors.NotFound("no such container")
        client.containers.list.return_value = []
        assert _check_edge(client, self._svc()) == (False, False)


class TestCertNotExpiringSubcheck:
    """Direct coverage of the HEV4 ``_cert_not_expiring_subcheck`` contract: the
    fail-loud renewal-window read is isolated so a corrupt *global* setting
    degrades only this subcheck, the setting is read exactly once and the value
    propagated verbatim, and ONLY ``ValueError`` is swallowed — any other error
    still propagates."""

    def test_corrupt_window_returns_false_and_warns(self, db_session, tmp_path, caplog):
        import logging

        from app.health.health_checker import _cert_not_expiring_subcheck
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        set_setting(db_session, "cert_renewal_window_days", "0")  # corrupt: < 1
        db_session.commit()

        with caplog.at_level(logging.WARNING, logger="app.health.health_checker"):
            result = _cert_not_expiring_subcheck(db_session, svc, tmp_path)

        assert result is False
        assert any(
            "cert_renewal_window_days is corrupt" in r.getMessage() for r in caplog.records
        )

    def test_valid_window_read_once_and_passed_through(self, db_session, tmp_path):
        from app.health import health_checker
        from app.health.health_checker import _cert_not_expiring_subcheck
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        set_setting(db_session, "cert_renewal_window_days", "45")
        db_session.commit()

        with (
            patch.object(
                health_checker,
                "get_positive_int_setting",
                wraps=health_checker.get_positive_int_setting,
            ) as spy,
            patch.object(health_checker, "_check_cert_not_expiring", return_value=True) as inner,
        ):
            result = _cert_not_expiring_subcheck(db_session, svc, tmp_path)

        assert result is True
        # Read exactly once (no double-read of the setting) ...
        spy.assert_called_once_with(db_session, "cert_renewal_window_days")
        # ... and the configured window is propagated verbatim to the real check.
        inner.assert_called_once_with(svc, tmp_path, 45)

    def test_non_value_error_from_setting_propagates(self, db_session, tmp_path):
        # The isolation catches ONLY the fail-loud ValueError. A genuinely
        # unexpected error (e.g. a DB failure) must still propagate rather than
        # being silently masked as cert_not_expiring=False; broadening the except
        # would hide real faults and reintroduce silent staling.
        from app.health import health_checker
        from app.health.health_checker import _cert_not_expiring_subcheck

        svc = _create_service(db_session)
        with (
            patch.object(
                health_checker, "get_positive_int_setting", side_effect=RuntimeError("db down")
            ),
            pytest.raises(RuntimeError),
        ):
            _cert_not_expiring_subcheck(db_session, svc, tmp_path)


class TestCheckDnsNonLive:
    """Direct coverage of the offline (DB-only) DNS subcheck semantics."""

    def test_match_mismatch_and_missing_ip(self, db_session):
        from app.health.health_checker import _check_dns

        svc = _create_service(db_session)
        db_session.add(DnsRecord(
            service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1",
        ))
        db_session.commit()

        # Present, and the stored value matches the current Tailscale IP.
        assert _check_dns(db_session, svc, "100.64.0.1") == (True, True)
        # Present, but the stored value no longer matches.
        assert _check_dns(db_session, svc, "100.64.0.99") == (True, False)
        # Present, but no current IP to compare against -> cannot claim a match.
        assert _check_dns(db_session, svc, None) == (True, False)

    def test_absent_record(self, db_session):
        from app.health.health_checker import _check_dns

        svc = _create_service(db_session)
        assert _check_dns(db_session, svc, "100.64.0.1") == (False, False)

    def test_record_without_record_id_is_not_present(self, db_session):
        from app.health.health_checker import _check_dns

        svc = _create_service(db_session)
        db_session.add(DnsRecord(
            service_id=svc.id, hostname=svc.hostname, record_id=None, value="100.64.0.1",
        ))
        db_session.commit()
        # No Cloudflare record_id yet -> not considered present, but the stored
        # value still matches the live IP.
        assert _check_dns(db_session, svc, "100.64.0.1") == (False, True)

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

        with patch("app.health.health_checker.connect") as mock_dc:
            mock_client = MagicMock()
            mock_dc.return_value = mock_client
            mock_client.containers.get.side_effect = docker.errors.NotFound("nope")
            checks = run_health_checks(db_session, svc, "/tmp/gen", "/tmp/certs")

        assert "https_probe_ok" in checks

    def test_https_probe_false_when_no_ip(self, db_session, caplog):
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="t.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )
        assert _check_https_probe(svc, None) is False
        assert "missing Tailscale IP" in caplog.text

    def test_https_probe_false_when_no_client(self, caplog):
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="t.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )
        assert _check_https_probe(svc, "100.64.0.1", client=None) is False
        assert "Docker client unavailable" in caplog.text

    def test_https_probe_success(self):
        """Probe succeeds when curl exits 0 and returns a 2xx status code."""
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="probe.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        # curl exits 0 and writes HTTP status code via -w "%{http_code}"
        mock_container.exec_run.return_value = (0, b"200")
        mock_client.containers.get.return_value = mock_container

        result = _check_https_probe(svc, "100.64.0.1", client=mock_client)
        assert result is True
        mock_client.containers.get.assert_called_once_with("e")

    def test_https_probe_runs_curl_in_edge_container(self):
        """Probe execs curl inside the edge container with the correct Host header."""
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="tls.example.com",
            base_domain="example.com", edge_container_name="edge_tls",
            network_name="n", ts_hostname="ts",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"200")
        mock_client.containers.get.return_value = mock_container

        _check_https_probe(svc, "100.64.0.1", client=mock_client)

        mock_container.exec_run.assert_called_once()
        cmd = mock_container.exec_run.call_args[0][0]
        assert "curl" in cmd
        assert "https://localhost:443/" in cmd
        assert "tls.example.com" in " ".join(cmd)

    def test_https_probe_uses_configured_healthcheck_path(self):
        """Probe targets the service healthcheck path when one is configured."""
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="tls.example.com",
            base_domain="example.com", edge_container_name="edge_tls",
            network_name="n", ts_hostname="ts",
            healthcheck_path="readyz",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"200")
        mock_client.containers.get.return_value = mock_container

        _check_https_probe(svc, "100.64.0.1", client=mock_client)

        cmd = mock_container.exec_run.call_args[0][0]
        assert "https://localhost:443/readyz" in cmd

    def test_https_probe_5xx_is_failure(self, caplog):
        """curl returning a 5xx status code means the upstream is broken."""
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="fail.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"502")
        mock_client.containers.get.return_value = mock_container

        assert _check_https_probe(svc, "100.64.0.1", client=mock_client) is False
        assert "upstream returned 5xx" in caplog.text
        assert "http_code=502" in caplog.text

    def test_https_probe_4xx_is_success(self):
        """4xx from upstream (e.g. auth required) still means Caddy is serving."""
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="auth.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        # curl exits 0 and returns 401 — Caddy is serving, upstream requires auth
        mock_container.exec_run.return_value = (0, b"401")
        mock_client.containers.get.return_value = mock_container

        assert _check_https_probe(svc, "100.64.0.1", client=mock_client) is True

    def test_https_probe_connection_error_is_failure(self, caplog):
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="err.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (7, b"curl: (7) Failed to connect")
        mock_client.containers.get.return_value = mock_container

        assert _check_https_probe(svc, "100.64.0.1", client=mock_client) is False
        assert "curl returned non-zero" in caplog.text
        assert "exit_code=7" in caplog.text

    def test_https_probe_container_not_running(self, caplog):
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="t.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "restarting"
        mock_client.containers.get.return_value = mock_container

        assert _check_https_probe(svc, "100.64.0.1", client=mock_client) is False
        assert "edge container not running" in caplog.text
        assert "container_status=restarting" in caplog.text

    def test_https_probe_no_response_is_failure(self, caplog):
        """curl returning '000' (no HTTP response received) fails the probe."""
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="t.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"000")
        mock_client.containers.get.return_value = mock_container

        assert _check_https_probe(svc, "100.64.0.1", client=mock_client) is False
        assert "no HTTP response received" in caplog.text
        assert "http_code=000" in caplog.text

    def test_https_probe_rejects_malformed_status(self, caplog):
        """A truncated/non-3-digit status (e.g. "00") must not pass the probe.

        Regression: ``raw[-3:]`` previously left a 2-char numeric string that
        slipped past ``isdigit()`` and was treated as a healthy response."""
        from app.health.health_checker import _check_https_probe

        svc = Service(
            name="Test", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="bad.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"00")
        mock_client.containers.get.return_value = mock_container

        assert _check_https_probe(svc, "100.64.0.1", client=mock_client) is False
        assert "did not return a valid HTTP status" in caplog.text

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
            "app.health.health_checker.connect",
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

# ---------------------------------------------------------------------------
# Cert-on-disk reads via the <hostname>/current/ symlink
# ---------------------------------------------------------------------------


class TestCertPresentCurrentSymlink:
    """_check_cert_present/_check_cert_not_expiring must read the published
    ``<hostname>/current/{fullchain,privkey}.pem`` path, following the atomic
    ``current`` symlink to the real generation directory."""

    def _svc(self):
        return Service(
            name="T", upstream_container_id="c1",
            upstream_container_name="t", upstream_scheme="http",
            upstream_port=80, hostname="cert.example.com",
            base_domain="example.com", edge_container_name="e",
            network_name="n", ts_hostname="ts",
        )

    def test_present_through_real_symlink(self, tmp_path):
        from app.health.health_checker import _check_cert_present

        svc = self._svc()
        host_dir = tmp_path / svc.hostname
        gen = host_dir / "gen-123-abc"
        gen.mkdir(parents=True)
        (gen / "fullchain.pem").write_text("cert")
        (gen / "privkey.pem").write_text("key")
        # Atomic relative symlink, exactly like the cert writer publishes.
        (host_dir / "current").symlink_to("gen-123-abc")

        assert _check_cert_present(svc, tmp_path) is True

    def test_absent_when_no_current(self, tmp_path):
        from app.health.health_checker import _check_cert_present

        svc = self._svc()
        (tmp_path / svc.hostname).mkdir(parents=True)
        assert _check_cert_present(svc, tmp_path) is False

    def test_absent_when_privkey_missing(self, tmp_path):
        from app.health.health_checker import _check_cert_present

        svc = self._svc()
        cur = tmp_path / svc.hostname / "current"
        cur.mkdir(parents=True)
        (cur / "fullchain.pem").write_text("cert")  # privkey absent
        assert _check_cert_present(svc, tmp_path) is False

    def test_absent_when_current_symlink_dangling(self, tmp_path):
        from app.health.health_checker import _check_cert_present

        svc = self._svc()
        host_dir = tmp_path / svc.hostname
        host_dir.mkdir(parents=True)
        # current points at a generation dir that no longer exists.
        (host_dir / "current").symlink_to("gen-deleted")
        assert _check_cert_present(svc, tmp_path) is False

    def test_not_expiring_reads_current_path(self, tmp_path):
        from app.health.health_checker import _check_cert_not_expiring

        svc = self._svc()
        host_dir = tmp_path / svc.hostname
        gen = host_dir / "gen-1"
        gen.mkdir(parents=True)
        (gen / "fullchain.pem").write_text("cert")
        (host_dir / "current").symlink_to("gen-1")

        with patch(
            "app.certs.cert_manager.get_cert_expiry",
        ) as mock_expiry:
            from datetime import UTC, datetime, timedelta
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=60)
            assert _check_cert_not_expiring(svc, tmp_path, 30) is True
            # Confirms it parsed the symlinked current/fullchain.pem.
            called_path = mock_expiry.call_args.args[0]
            assert called_path.name == "fullchain.pem"
            assert "current" in str(called_path)

    def _publish_cert(self, tmp_path, svc):
        host_dir = tmp_path / svc.hostname
        gen = host_dir / "gen-1"
        gen.mkdir(parents=True)
        (gen / "fullchain.pem").write_text("cert")
        (host_dir / "current").symlink_to("gen-1")

    def test_expiring_inside_configured_window(self, tmp_path):
        """With renewal_window_days=30, a cert ~20 days out is inside the
        renewal window and counts as expiring (False)."""
        from app.health.health_checker import _check_cert_not_expiring

        svc = self._svc()
        self._publish_cert(tmp_path, svc)
        with patch("app.certs.cert_manager.get_cert_expiry") as mock_expiry:
            from datetime import UTC, datetime, timedelta
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=20)
            assert _check_cert_not_expiring(svc, tmp_path, 30) is False

    def test_not_expiring_outside_configured_window(self, tmp_path):
        """With renewal_window_days=30, a cert ~40 days out is beyond the
        renewal window and counts as not expiring (True)."""
        from app.health.health_checker import _check_cert_not_expiring

        svc = self._svc()
        self._publish_cert(tmp_path, svc)
        with patch("app.certs.cert_manager.get_cert_expiry") as mock_expiry:
            from datetime import UTC, datetime, timedelta
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=40)
            assert _check_cert_not_expiring(svc, tmp_path, 30) is True

    def test_huge_window_overflows_to_expiring(self, tmp_path):
        """CHR / AR-R3-8: a renewal window so large the threshold overflows the
        representable datetime range reads as expiring (False) — no expiry can
        exceed an unrepresentable threshold. Behavior-preserving after the
        days_from_now migration (returns None instead of raising OverflowError,
        which was previously caught as False)."""
        from app.health.health_checker import _check_cert_not_expiring

        svc = self._svc()
        self._publish_cert(tmp_path, svc)
        with patch("app.certs.cert_manager.get_cert_expiry") as mock_expiry:
            from datetime import UTC, datetime, timedelta
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=3650)
            assert _check_cert_not_expiring(svc, tmp_path, 10**9) is False

    def test_default_window_when_no_db_value(self, tmp_path, db_session):
        """With no ``cert_renewal_window_days`` stored, the setting-driven
        subcheck resolves the DEFAULTS 30-day window: ~20 days out is expiring
        (False), ~40 is not (True)."""
        from app.health.health_checker import _cert_not_expiring_subcheck

        svc = self._svc()
        self._publish_cert(tmp_path, svc)
        with patch("app.certs.cert_manager.get_cert_expiry") as mock_expiry:
            from datetime import UTC, datetime, timedelta
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=20)
            assert _cert_not_expiring_subcheck(db_session, svc, tmp_path) is False
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=40)
            assert _cert_not_expiring_subcheck(db_session, svc, tmp_path) is True


# ---------------------------------------------------------------------------
# CHR / AR-R3-16a: single-source check-name registry
# ---------------------------------------------------------------------------


class TestHealthCheckRegistry:
    """The check-name registry is the single source of truth for both result
    dicts, so the Docker-unreachable fallback cannot omit a check the happy path
    returns (the drift the two hand-listed dicts used to risk)."""

    def test_registry_partitions_into_critical_and_warning(self):
        from app.health.health_checker import ALL_CHECK_NAMES

        # Every registered check is classified exactly once — critical/warning
        # together cover the registry and never overlap.
        assert set(ALL_CHECK_NAMES) == set(CRITICAL_CHECKS) | set(WARNING_CHECKS)
        assert set(CRITICAL_CHECKS).isdisjoint(WARNING_CHECKS)
        assert len(ALL_CHECK_NAMES) == len(set(ALL_CHECK_NAMES))  # no duplicates

    def test_offline_fallback_returns_exactly_the_registry(self, db_session, tmp_data_dir):
        from app.health.health_checker import ALL_CHECK_NAMES

        svc = _create_service(db_session)
        with patch("app.health.health_checker.connect", side_effect=Exception("Docker down")):
            checks = run_health_checks(
                db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
            )
        # The fallback is built from the same registry as the happy path via
        # dict.fromkeys, so its keys are exactly the registry.
        assert set(checks) == set(ALL_CHECK_NAMES)


# ---------------------------------------------------------------------------
# CHR / AR-R3-16b: probe-result classifier extracted from _check_https_probe
# ---------------------------------------------------------------------------


class TestClassifyProbeResult:
    """Direct coverage of the 5-branch classifier, testable without a container."""

    def test_2xx_is_healthy(self):
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, b"200") is True

    def test_3xx_is_healthy(self):
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, b"301") is True

    def test_4xx_is_healthy(self):
        # 4xx (e.g. auth required) still means Caddy is serving the route.
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, b"401") is True

    def test_nonzero_curl_exit_is_unhealthy(self):
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(7, b"curl: (7) Failed to connect") is False

    def test_5xx_is_unhealthy(self):
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, b"502") is False

    def test_no_response_000_is_unhealthy(self):
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, b"000") is False

    def test_non_three_digit_status_is_unhealthy(self):
        # Regression: a truncated 2-char numeric status must not slip past isdigit().
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, b"00") is False

    def test_empty_output_is_unhealthy(self):
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, b"") is False

    def test_none_output_is_unhealthy(self):
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, None) is False

    def test_status_is_parsed_from_last_three_chars(self):
        # The parser takes the trailing 3 chars, so a trailing numeric code
        # classifies on that suffix and a trailing non-digit run does not.
        from app.health.health_checker import _classify_probe_result

        assert _classify_probe_result(0, b"xx200") is True
        assert _classify_probe_result(0, b"200xx") is False


# ---------------------------------------------------------------------------
# CHR / CI-OBS3: Docker-unreachable fallback honors the requested live_dns flag
# ---------------------------------------------------------------------------


class TestOfflineFallbackHonorsLiveDns:
    """When Docker is unreachable, a manual full check with live_dns=True must
    still consult Cloudflare for DNS presence (the DNS subcheck needs no Docker).
    The fallback previously dropped the live flag, silently falling back to the DB
    mirror and reporting an out-of-band-deleted record as still present."""

    @patch("app.adapters.cloudflare_adapter.find_record")
    def test_fallback_consults_live_cloudflare_when_requested(
        self, mock_find_record, db_session, tmp_data_dir
    ):
        from app.secrets import CLOUDFLARE_TOKEN, write_secret
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        # A stale local mirror still records the A record...
        db_session.add(DnsRecord(
            service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1",
        ))
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()
        write_secret(CLOUDFLARE_TOKEN, "cf-token")
        # ...but the record was deleted out of band in Cloudflare.
        mock_find_record.return_value = None

        with patch("app.health.health_checker.connect", side_effect=Exception("Docker down")):
            checks = run_health_checks(
                db_session,
                svc,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
                live_dns=True,
            )

        # Before the fix the live flag was dropped: find_record was never called
        # and dns_record_present trusted the stale DB mirror (True).
        mock_find_record.assert_called_once_with("cf-token", "zone123", svc.hostname, "A")
        assert checks["dns_record_present"] is False

    def test_fallback_stays_db_only_for_automatic_sweep(self, db_session, tmp_data_dir):
        # The automatic 60s sweep does not request live_dns, so the fallback must
        # NOT reach out to Cloudflare — honoring the flag must not become always-live.
        from app.settings_store import set_setting

        svc = _create_service(db_session)
        db_session.add(DnsRecord(
            service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1",
        ))
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        with (
            patch("app.health.health_checker.connect", side_effect=Exception("Docker down")),
            patch("app.adapters.cloudflare_adapter.find_record") as mock_find_record,
        ):
            checks = run_health_checks(
                db_session, svc, tmp_data_dir / "generated", tmp_data_dir / "certs"
            )

        mock_find_record.assert_not_called()
        assert checks["dns_record_present"] is True  # DB mirror
