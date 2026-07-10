"""Health-check orchestration: client lifecycle, subcheck fan-out, fallback (AR18).

Runs the battery of per-domain subchecks against the real system state and
returns a dict of ``check_name -> bool``. The reconciler calls
:func:`run_health_checks` at the end of each cycle; it can also be invoked
standalone. :func:`get_live_tailscale_ip` exposes the same live-IP path the
runner follows internally so the manual full check can verify live Cloudflare
DNS against the current tailnet IP.

``connect`` / ``close_client`` are imported here (the module owning the Docker
client lifecycle) so tests patch them at ``app.health.runner``; the per-domain
subchecks are called through their defining modules so a test patching, say,
``app.health.checks.docker._check_edge`` is honored at call time.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.edge.docker_client import close_client, connect
from app.health import probe
from app.health.checks import certs as cert_checks
from app.health.checks import config as config_checks
from app.health.checks import dns as dns_checks
from app.health.checks import docker as docker_checks
from app.health.checks import tailscale as tailscale_checks
from app.health.registry import ALL_CHECK_NAMES

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

    from app.models.service import Service

logger = logging.getLogger(__name__)


def run_health_checks(
    db: Session,
    service: Service,
    generated_dir: str | Path,
    certs_dir: str | Path,
    socket_path: str | None = None,
    *,
    live_dns: bool = False,
) -> dict[str, bool]:
    """Run all health subchecks for *service*.  Returns {name: bool}."""
    try:
        client = connect(socket_path)
    except Exception:
        logger.warning("Cannot connect to Docker for health checks", exc_info=True)
        # Docker is unreachable, but the filesystem- and DB-backed subchecks do
        # not need it: report them accurately instead of forcing False, so a
        # transient daemon outage does not misreport an on-disk cert or a stored
        # DNS record as failing. Every check defaults to False from the registry
        # (so the fallback can never omit a check the happy path returns); only
        # the offline (disk/DB) subchecks are overridden here. ``dns_matches_ip``
        # still needs the live Tailscale IP (Docker-only) so it stays False, but
        # ``live_dns`` is honored — a manual full check gets the same live
        # Cloudflare presence accuracy it would with Docker up.
        checks: dict[str, bool] = dict.fromkeys(ALL_CHECK_NAMES, False)
        checks["cert_present"] = cert_checks._check_cert_present(service, certs_dir)
        checks["cert_not_expiring"] = cert_checks._cert_not_expiring_subcheck(
            db, service, certs_dir
        )
        dns_record_present, dns_matches_ip = dns_checks._check_dns(
            db, service, None, live=live_dns
        )
        checks["dns_record_present"] = dns_record_present
        checks["dns_matches_ip"] = dns_matches_ip
        checks["caddy_config_present"] = config_checks._check_caddy_config(service, generated_dir)
        return checks

    checks = dict.fromkeys(ALL_CHECK_NAMES, False)
    try:
        # --- Upstream container ---
        checks["upstream_container_present"] = docker_checks._check_upstream_present(
            client, service
        )
        checks["upstream_network_connected"] = docker_checks._check_upstream_network(
            client, service
        )

        # --- Edge container ---
        edge_present, edge_running = docker_checks._check_edge(client, service)
        checks["edge_container_present"] = edge_present
        checks["edge_container_running"] = edge_running

        # --- Tailscale ---
        ts_ready, ts_ip_present, live_tailscale_ip = tailscale_checks._check_tailscale(
            client, service, edge_running
        )

        current_ip = live_tailscale_ip
        checks["tailscale_ready"] = ts_ready
        checks["tailscale_ip_present"] = ts_ip_present

        # --- Certs ---
        checks["cert_present"] = cert_checks._check_cert_present(service, certs_dir)
        checks["cert_not_expiring"] = cert_checks._cert_not_expiring_subcheck(
            db, service, certs_dir
        )

        # --- DNS ---
        checks["dns_record_present"], checks["dns_matches_ip"] = dns_checks._check_dns(
            db, service, current_ip, live=live_dns
        )

        # --- Caddy config ---
        checks["caddy_config_present"] = config_checks._check_caddy_config(service, generated_dir)

        # --- HTTPS probe (spec §18.1) ---
        checks["https_probe_ok"] = probe.check_https_probe(service, current_ip, client)
        return checks
    finally:
        close_client(client)


def get_live_tailscale_ip(service: Service, socket_path: str | None = None) -> str | None:
    """Return *service*'s current live Tailscale IP, or ``None`` if unavailable.

    One-shot (no retry loop, unlike ``edge.tailscale_ops.detect_tailscale_ip``):
    connect to Docker, resolve the edge container, read ``tailscale status`` once.
    This is the SAME live-IP path ``run_health_checks`` follows internally
    (``connect`` -> :func:`_check_edge` -> :func:`_check_tailscale`) but exposed
    standalone so the manual full health check can verify live Cloudflare DNS
    against the *live* IP rather than the persisted ``ServiceStatus.tailscale_ip``,
    which lags a tailnet IP change until the next reconcile. Returns ``None`` on
    any Docker/edge/Tailscale failure so callers degrade gracefully.
    """
    try:
        client = connect(socket_path)
    except Exception:
        logger.info("Cannot connect to Docker for live Tailscale IP", exc_info=True)
        return None
    try:
        _edge_present, edge_running = docker_checks._check_edge(client, service)
        _ready, _ip_present, live_ip = tailscale_checks._check_tailscale(
            client, service, edge_running
        )
        return live_ip
    finally:
        close_client(client)
