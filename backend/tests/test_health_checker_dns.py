"""DNS health-check tests."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from app.health import health_checker, runner
from app.health.checks import certs as cert_checks
from app.health.checks import config as config_checks
from app.health.checks import dns as dns_checks
from app.health.checks import docker as docker_checks
from app.health.checks import tailscale as tailscale_checks
from app.models.dns_record import DnsRecord
from app.models.service_status import ServiceStatus
from app.secrets import CLOUDFLARE_TOKEN, write_secret
from app.settings_store import set_setting

from ._services_helpers import _create_service_in_db as create_service_in_db


@contextmanager
def _healthy_non_dns_checks(current_ip="100.64.0.1"):
    with (
        patch.object(runner, "connect", return_value=MagicMock()),
        patch.object(docker_checks, "_check_upstream_present", return_value=True),
        patch.object(docker_checks, "_check_upstream_network", return_value=True),
        patch.object(docker_checks, "_check_edge", return_value=(True, True)),
        patch.object(tailscale_checks, "_check_tailscale", return_value=(True, True, current_ip)),
        patch.object(cert_checks, "_check_cert_present", return_value=True),
        patch.object(cert_checks, "_check_cert_not_expiring", return_value=True),
        patch.object(config_checks, "_check_caddy_config", return_value=True),
        patch("app.health.probe.check_https_probe", return_value=True),
    ):
        yield


def _configure_cloudflare(db_session):
    set_setting(db_session, "cf_zone_id", "zone123")
    db_session.commit()
    write_secret(CLOUDFLARE_TOKEN, "cf-token")


def _add_dns_record(db_session, service, *, value="100.64.0.1", record_id="r1"):
    db_session.add(
        DnsRecord(
            service_id=service.id,
            hostname=service.hostname,
            record_id=record_id,
            value=value,
        )
    )
    db_session.commit()


class TestCheckDnsNonLive:
    def test_match_mismatch_and_missing_ip(self, db_session):
        service = create_service_in_db(db_session)
        _add_dns_record(db_session, service, value="100.64.0.1")

        assert health_checker._check_dns(db_session, service, "100.64.0.1") == (True, True)
        assert health_checker._check_dns(db_session, service, "100.64.0.99") == (True, False)
        assert health_checker._check_dns(db_session, service, None) == (True, False)

    def test_absent_record(self, db_session):
        service = create_service_in_db(db_session)

        assert health_checker._check_dns(db_session, service, "100.64.0.1") == (False, False)

    def test_record_without_record_id_is_not_present(self, db_session):
        service = create_service_in_db(db_session)
        _add_dns_record(db_session, service, record_id=None, value="100.64.0.1")

        assert health_checker._check_dns(db_session, service, "100.64.0.1") == (False, True)


class TestCheckLiveDns:
    def test_none_ips_do_not_false_match(self, db_session):
        service = create_service_in_db(db_session)
        _configure_cloudflare(db_session)

        with patch("app.adapters.cloudflare_adapter.find_record", return_value={"content": None}):
            present, matches, extended = health_checker.check_live_dns(db_session, service, None)

        assert present is True
        assert matches is False
        assert extended["cf_record_exists"] is True
        assert extended["cf_record_ip"] is None
        assert extended["cf_ip_matches_tailscale"] is False

    def test_record_ip_without_current_ip_is_not_a_match(self, db_session):
        service = create_service_in_db(db_session)
        _configure_cloudflare(db_session)

        with patch(
            "app.adapters.cloudflare_adapter.find_record",
            return_value={"content": "100.64.0.1"},
        ):
            _present, matches, extended = health_checker.check_live_dns(db_session, service, None)

        assert matches is False
        assert extended["cf_record_ip"] == "100.64.0.1"
        assert extended["cf_ip_matches_tailscale"] is False

    def test_matching_ips_report_a_match(self, db_session):
        service = create_service_in_db(db_session)
        _configure_cloudflare(db_session)

        with patch(
            "app.adapters.cloudflare_adapter.find_record",
            return_value={"content": "100.64.0.1"},
        ):
            present, matches, extended = health_checker.check_live_dns(
                db_session,
                service,
                "100.64.0.1",
            )

        assert present is True
        assert matches is True
        assert extended["cf_ip_matches_tailscale"] is True

    def test_find_record_error_surfaces_cf_error_and_forces_no_match(self, db_session):
        # Live-verification exception branch (previously covered only via
        # return_value): when find_record raises, DB presence is still reported
        # (the row exists) but the match is forced False — we could not verify
        # live — and a cf_error names the exception type. A regression that
        # reported a phantom match or dropped the cf_error would slip past every
        # other test.
        service = create_service_in_db(db_session)
        _add_dns_record(db_session, service, value="100.64.0.1")
        _configure_cloudflare(db_session)

        with patch(
            "app.adapters.cloudflare_adapter.find_record",
            side_effect=RuntimeError("cloudflare exploded"),
        ):
            present, matches, extended = health_checker.check_live_dns(
                db_session, service, "100.64.0.1",
            )

        assert present is True
        assert matches is False
        assert extended == {"cf_error": "Cloudflare verification failed (RuntimeError)"}

    def test_credentials_load_error_falls_back_to_db_without_cf_error(self, db_session):
        # If loading Cloudflare credentials raises (a transient settings/secret
        # read failure, distinct from missing config), check_live_dns degrades to
        # the stored-DB booleans with an empty extended dict — it must never crash
        # the health sweep, and unlike the not-configured path it surfaces no
        # cf_error.
        service = create_service_in_db(db_session)
        _add_dns_record(db_session, service, value="100.64.0.1")

        with patch.object(
            dns_checks,
            "cloudflare_credentials",
            side_effect=RuntimeError("secret read failed"),
        ):
            present, matches, extended = health_checker.check_live_dns(
                db_session, service, "100.64.0.1",
            )

        assert present is True
        assert matches is True
        assert extended == {}


class TestRunHealthChecksDnsFlow:
    @patch("app.adapters.cloudflare_adapter.find_record")
    def test_live_cloudflare_drift_overrides_db_dns_match(
        self,
        mock_find_record,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)
        status = db_session.get(ServiceStatus, service.id)
        status.tailscale_ip = "100.64.0.1"
        _add_dns_record(db_session, service, value="100.64.0.1")
        _configure_cloudflare(db_session)
        mock_find_record.return_value = {"content": "100.64.0.99"}

        with _healthy_non_dns_checks(current_ip="100.64.0.1"):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
                live_dns=True,
            )

        assert checks["dns_record_present"] is True
        assert checks["dns_matches_ip"] is False
        mock_find_record.assert_called_once_with("cf-token", "zone123", service.hostname, "A")

    @patch("app.adapters.cloudflare_adapter.find_record")
    def test_live_cloudflare_missing_record_overrides_db_presence(
        self,
        mock_find_record,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)
        status = db_session.get(ServiceStatus, service.id)
        status.tailscale_ip = "100.64.0.1"
        _add_dns_record(db_session, service, value="100.64.0.1")
        _configure_cloudflare(db_session)
        mock_find_record.return_value = None

        with _healthy_non_dns_checks(current_ip="100.64.0.1"):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
                live_dns=True,
            )

        assert checks["dns_record_present"] is False
        assert checks["dns_matches_ip"] is False
        mock_find_record.assert_called_once_with("cf-token", "zone123", service.hostname, "A")

    @patch("app.adapters.cloudflare_adapter.find_record")
    def test_automatic_health_does_not_call_cloudflare(
        self,
        mock_find_record,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)
        status = db_session.get(ServiceStatus, service.id)
        status.tailscale_ip = "100.64.0.1"
        _add_dns_record(db_session, service, value="100.64.0.1")
        _configure_cloudflare(db_session)

        with _healthy_non_dns_checks(current_ip="100.64.0.1"):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
            )

        assert checks["dns_matches_ip"] is True
        mock_find_record.assert_not_called()

    @patch("app.adapters.cloudflare_adapter.find_record")
    def test_offline_fallback_consults_live_cloudflare_when_requested(
        self,
        mock_find_record,
        db_session,
        tmp_data_dir,
    ):
        service = create_service_in_db(db_session)
        _add_dns_record(db_session, service, value="100.64.0.1")
        _configure_cloudflare(db_session)
        mock_find_record.return_value = None

        with patch.object(runner, "connect", side_effect=Exception("Docker down")):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
                live_dns=True,
            )

        mock_find_record.assert_called_once_with("cf-token", "zone123", service.hostname, "A")
        assert checks["dns_record_present"] is False

    def test_offline_fallback_stays_db_only_for_automatic_sweep(self, db_session, tmp_data_dir):
        service = create_service_in_db(db_session)
        _add_dns_record(db_session, service, value="100.64.0.1")
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        with (
            patch.object(runner, "connect", side_effect=Exception("Docker down")),
            patch("app.adapters.cloudflare_adapter.find_record") as mock_find_record,
        ):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
            )

        mock_find_record.assert_not_called()
        assert checks["dns_record_present"] is True
