"""Certificate health-check tests."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app import settings_store
from app.health import health_checker
from app.models.service import Service
from app.settings_store import set_setting

from ._services_helpers import _create_service_in_db as create_service_in_db


def _service():
    return Service(
        name="T",
        upstream_container_id="c1",
        upstream_container_name="t",
        upstream_scheme="http",
        upstream_port=80,
        hostname="cert.example.com",
        base_domain="example.com",
        edge_container_name="e",
        network_name="n",
        ts_hostname="ts",
    )


def _publish_cert(tmp_path, service):
    host_dir = tmp_path / service.hostname
    generation = host_dir / "gen-1"
    generation.mkdir(parents=True)
    (generation / "fullchain.pem").write_text("cert")
    (host_dir / "current").symlink_to("gen-1")


class TestCertPresentCurrentSymlink:
    def test_present_through_real_symlink(self, tmp_path):
        service = _service()
        host_dir = tmp_path / service.hostname
        generation = host_dir / "gen-123-abc"
        generation.mkdir(parents=True)
        (generation / "fullchain.pem").write_text("cert")
        (generation / "privkey.pem").write_text("key")
        (host_dir / "current").symlink_to("gen-123-abc")

        assert health_checker._check_cert_present(service, tmp_path) is True

    def test_absent_when_no_current(self, tmp_path):
        service = _service()
        (tmp_path / service.hostname).mkdir(parents=True)

        assert health_checker._check_cert_present(service, tmp_path) is False

    def test_absent_when_privkey_missing(self, tmp_path):
        service = _service()
        current = tmp_path / service.hostname / "current"
        current.mkdir(parents=True)
        (current / "fullchain.pem").write_text("cert")

        assert health_checker._check_cert_present(service, tmp_path) is False

    def test_absent_when_current_symlink_dangling(self, tmp_path):
        service = _service()
        host_dir = tmp_path / service.hostname
        host_dir.mkdir(parents=True)
        (host_dir / "current").symlink_to("gen-deleted")

        assert health_checker._check_cert_present(service, tmp_path) is False

    def test_not_expiring_reads_current_path(self, tmp_path):
        service = _service()
        _publish_cert(tmp_path, service)

        with patch("app.certs.cert_manager.get_cert_expiry") as mock_expiry:
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=60)
            assert health_checker._check_cert_not_expiring(service, tmp_path, 30) is True

        called_path = mock_expiry.call_args.args[0]
        assert called_path.name == "fullchain.pem"
        assert "current" in str(called_path)

    def test_expiring_inside_configured_window(self, tmp_path):
        service = _service()
        _publish_cert(tmp_path, service)

        with patch("app.certs.cert_manager.get_cert_expiry") as mock_expiry:
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=20)
            assert health_checker._check_cert_not_expiring(service, tmp_path, 30) is False

    def test_not_expiring_outside_configured_window(self, tmp_path):
        service = _service()
        _publish_cert(tmp_path, service)

        with patch("app.certs.cert_manager.get_cert_expiry") as mock_expiry:
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=40)
            assert health_checker._check_cert_not_expiring(service, tmp_path, 30) is True

    def test_huge_window_overflows_to_expiring(self, tmp_path):
        service = _service()
        _publish_cert(tmp_path, service)

        with patch("app.certs.cert_manager.get_cert_expiry") as mock_expiry:
            mock_expiry.return_value = datetime.now(UTC) + timedelta(days=3650)
            assert health_checker._check_cert_not_expiring(service, tmp_path, 10**9) is False


class TestCertNotExpiringSubcheck:
    def test_corrupt_window_returns_false_and_warns(self, db_session, tmp_path, caplog):
        service = create_service_in_db(db_session)
        set_setting(db_session, "cert_renewal_window_days", "0")
        db_session.commit()

        with caplog.at_level(logging.WARNING, logger="app.health.health_checker"):
            result = health_checker._cert_not_expiring_subcheck(db_session, service, tmp_path)

        assert result is False
        assert any(
            "cert_renewal_window_days is corrupt" in record.getMessage()
            for record in caplog.records
        )

    def test_valid_window_read_once_and_passed_through(self, db_session, tmp_path):
        service = create_service_in_db(db_session)
        set_setting(db_session, "cert_renewal_window_days", "45")
        db_session.commit()

        with (
            patch.object(
                settings_store,
                "get_positive_int_setting",
                wraps=settings_store.get_positive_int_setting,
            ) as spy,
            patch.object(health_checker, "_check_cert_not_expiring", return_value=True) as inner,
        ):
            result = health_checker._cert_not_expiring_subcheck(db_session, service, tmp_path)

        assert result is True
        spy.assert_called_once_with(db_session, "cert_renewal_window_days")
        inner.assert_called_once_with(service, tmp_path, 45)

    def test_non_value_error_from_setting_propagates(self, db_session, tmp_path):
        service = create_service_in_db(db_session)

        with (
            patch.object(
                settings_store,
                "get_positive_int_setting",
                side_effect=RuntimeError("db down"),
            ),
            pytest.raises(RuntimeError),
        ):
            health_checker._cert_not_expiring_subcheck(db_session, service, tmp_path)
