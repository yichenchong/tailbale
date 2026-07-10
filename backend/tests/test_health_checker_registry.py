"""Health check-name registry tests."""

from unittest.mock import patch

from app.health import health_checker, runner

from ._services_helpers import _create_service_in_db as create_service_in_db


class TestHealthCheckRegistry:
    def test_registry_partitions_into_critical_and_warning(self):
        assert set(health_checker.ALL_CHECK_NAMES) == set(health_checker.CRITICAL_CHECKS) | set(
            health_checker.WARNING_CHECKS
        )
        assert set(health_checker.CRITICAL_CHECKS).isdisjoint(health_checker.WARNING_CHECKS)
        assert len(health_checker.ALL_CHECK_NAMES) == len(set(health_checker.ALL_CHECK_NAMES))

    def test_https_probe_is_warning_check(self):
        assert "https_probe_ok" in health_checker.WARNING_CHECKS
        assert "https_probe_ok" not in health_checker.CRITICAL_CHECKS

    def test_offline_fallback_returns_exactly_the_registry(self, db_session, tmp_data_dir):
        service = create_service_in_db(db_session)

        with patch.object(runner, "connect", side_effect=Exception("Docker down")):
            checks = health_checker.run_health_checks(
                db_session,
                service,
                tmp_data_dir / "generated",
                tmp_data_dir / "certs",
            )

        assert set(checks) == set(health_checker.ALL_CHECK_NAMES)
