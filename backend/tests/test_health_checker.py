"""Aggregate health status tests."""

import pytest

from app.health.health_checker import aggregate_status

PASSING_CHECKS = {
    "upstream_container_present": True,
    "upstream_network_connected": True,
    "edge_container_present": True,
    "edge_container_running": True,
    "tailscale_ready": True,
    "tailscale_ip_present": True,
    "cert_present": True,
    "cert_not_expiring": True,
    "dns_record_present": True,
    "dns_matches_ip": True,
    "caddy_config_present": True,
    "https_probe_ok": True,
}


class TestAggregateStatus:
    def test_all_pass(self):
        assert aggregate_status(PASSING_CHECKS) == "healthy"

    def test_critical_fail_gives_error(self):
        checks = {"edge_container_present": False, "edge_container_running": False}
        assert aggregate_status(checks) == "error"

    def test_warning_check_fails(self):
        checks = PASSING_CHECKS | {"cert_not_expiring": False}
        assert aggregate_status(checks) == "warning"

    def test_critical_overrides_warning(self):
        checks = {
            "edge_container_present": True,
            "edge_container_running": True,
            "tailscale_ip_present": False,
            "cert_present": True,
            "cert_not_expiring": False,
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
        checks = PASSING_CHECKS | {failed_check: False}
        assert aggregate_status(checks) == "error"

    def test_dns_record_absent_is_warning(self):
        checks = PASSING_CHECKS | {"dns_record_present": False}
        assert aggregate_status(checks) == "warning"

    def test_empty_checks_healthy(self):
        assert aggregate_status({}) == "healthy"
