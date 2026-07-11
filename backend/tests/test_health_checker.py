"""Aggregate health status tests."""

import pytest

from app.health.health_checker import aggregate_status
from app.health.status_policy import phase_level, phase_rank, transition_verb

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


class TestStatusPolicy:
    def test_phase_rank_orders_health_phases(self):
        assert phase_rank("healthy") < phase_rank("warning") < phase_rank("error")

    def test_phase_rank_unknown_ranks_worst(self):
        assert phase_rank("pending") == 3
        assert phase_rank("error") < phase_rank("pending")

    @pytest.mark.parametrize(
        ("phase", "expected"),
        [("healthy", "info"), ("warning", "warning"), ("error", "error")],
    )
    def test_phase_level_maps_health_phases(self, phase, expected):
        assert phase_level(phase) == expected

    def test_phase_level_unknown_defaults_to_error(self):
        assert phase_level("pending") == "error"

    def test_phase_level_unknown_override(self):
        assert phase_level("pending", unknown="warning") == "warning"

    def test_phase_level_error_ignores_unknown_override(self):
        # error is a KNOWN phase and must map to "error" regardless of the
        # unknown fallback; probe_retry passes unknown="warning". A regression
        # that returned the fallback before the explicit error branch would break.
        assert phase_level("error", unknown="warning") == "error"

    def test_transition_verb_improved_degraded_changed(self):
        assert transition_verb("error", "healthy") == "improved"
        assert transition_verb("healthy", "error") == "degraded"
        assert transition_verb("warning", "warning") == "changed"

    def test_transition_across_unknown_phase_uses_worst_rank(self):
        # non-health phases rank worst, so LEAVING one reads as an improvement and
        # ENTERING one as a degradation (historical _UNKNOWN_PHASE_RANK behavior).
        assert transition_verb("pending", "error") == "improved"
        assert transition_verb("error", "pending") == "degraded"
