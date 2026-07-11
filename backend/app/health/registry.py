"""Health-subcheck registry and aggregation (AR18).

The single source of truth for *which* subchecks exist, how they partition into
critical vs warning, and how a check dict collapses to an overall status. The
runner and the per-domain check modules both build their result dicts from
:data:`ALL_CHECK_NAMES`, and :func:`aggregate_status` is what the reconciler
consumes to derive a service phase.
"""

from __future__ import annotations

# Every subcheck name, in the order the result dict presents them. This is the
# single source of truth for *which* checks exist: both the happy-path result
# and the Docker-unreachable fallback are built from it (via ``dict.fromkeys``),
# so a new check can never be enumerated in one path and silently forgotten in
# the other.
ALL_CHECK_NAMES: tuple[str, ...] = (
    "upstream_container_present",
    "upstream_network_connected",
    "edge_container_present",
    "edge_container_running",
    "tailscale_ready",
    "tailscale_ip_present",
    "cert_present",
    "cert_not_expiring",
    "dns_record_present",
    "dns_matches_ip",
    "caddy_config_present",
    "https_probe_ok",
)

# Checks whose failure means "error" status.
CRITICAL_CHECKS = frozenset({
    "upstream_container_present",
    "upstream_network_connected",
    "edge_container_present",
    "edge_container_running",
    "tailscale_ready",
    "tailscale_ip_present",
    "cert_present",
    "caddy_config_present",
})

# Everything else is a "warning" (surfaced only when no critical already failed).
# Derived from the registry so a newly added check is classified automatically
# and the two sets can never disagree about the universe of checks.
WARNING_CHECKS = frozenset(ALL_CHECK_NAMES) - CRITICAL_CHECKS


def aggregate_status(checks: dict[str, bool]) -> str:
    """Determine overall status from health subchecks.

    Returns ``"healthy"``, ``"warning"``, or ``"error"``.
    """
    for name in CRITICAL_CHECKS:
        if name in checks and not checks[name]:
            return "error"
    for name in WARNING_CHECKS:
        if name in checks and not checks[name]:
            return "warning"
    return "healthy"
