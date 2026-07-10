"""Public facade for per-service health checks (AR18 decomposition).

Historically a single god-module, this is now split into a registry, a runner,
and per-domain check modules under ``app.health``:

* :mod:`app.health.registry` — ``ALL_CHECK_NAMES`` / ``CRITICAL_CHECKS`` /
  ``WARNING_CHECKS`` and :func:`aggregate_status`.
* :mod:`app.health.runner` — :func:`run_health_checks` orchestration (client
  lifecycle, Docker-unreachable fallback) and :func:`get_live_tailscale_ip`.
* :mod:`app.health.checks.docker` / ``.tailscale`` / ``.dns`` / ``.certs`` /
  ``.config`` — the individual subchecks.

This module preserves the public import surface every consumer (reconciler
steps / reconcile_loop / probe_retry, services/edge_ops) and test relies on by
re-exporting those symbols from their new defining modules. Patch a subcheck or
the Docker client lifecycle at its *defining* module, not here.
"""

from __future__ import annotations

from app.health.checks.certs import (
    _cert_not_expiring_subcheck,
    _check_cert_not_expiring,
    _check_cert_present,
)
from app.health.checks.config import _check_caddy_config
from app.health.checks.dns import _check_dns, _check_stored_dns, check_live_dns
from app.health.checks.docker import (
    _check_edge,
    _check_upstream_network,
    _check_upstream_present,
)
from app.health.checks.tailscale import _check_tailscale
from app.health.registry import (
    ALL_CHECK_NAMES,
    CRITICAL_CHECKS,
    WARNING_CHECKS,
    aggregate_status,
)
from app.health.runner import get_live_tailscale_ip, run_health_checks

__all__ = [
    "ALL_CHECK_NAMES",
    "CRITICAL_CHECKS",
    "WARNING_CHECKS",
    "_cert_not_expiring_subcheck",
    "_check_caddy_config",
    "_check_cert_not_expiring",
    "_check_cert_present",
    "_check_dns",
    "_check_edge",
    "_check_stored_dns",
    "_check_tailscale",
    "_check_upstream_network",
    "_check_upstream_present",
    "aggregate_status",
    "check_live_dns",
    "get_live_tailscale_ip",
    "run_health_checks",
]
