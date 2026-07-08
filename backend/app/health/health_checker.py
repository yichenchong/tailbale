"""Per-service health subchecks.

Runs a battery of checks against the real system state and returns
a dict of check_name -> bool.  The reconciler calls this at the end
of each cycle; it can also be invoked standalone.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import docker

# ``cloudflare_adapter`` and ``cert_manager`` are imported as modules (not their
# symbols) so tests — here and in the reconciler suite, which drives the health
# path through ``reconcile_service`` — can patch ``find_record`` / ``get_cert_expiry``
# at the source module and have the patch take effect: the attribute is resolved
# at call time rather than bound once at import.
from app import secrets
from app.adapters import cloudflare_adapter
from app.certs import cert_manager
from app.edge.container_manager import find_edge_container
from app.edge.docker_client import close_client, connect
from app.health import probe
from app.models.dns_record import DnsRecord
from app.settings_store import get_positive_int_setting, get_setting
from app.timeutil import days_from_now

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.service import Service

logger = logging.getLogger(__name__)

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
        checks["cert_present"] = _check_cert_present(service, certs_dir)
        checks["cert_not_expiring"] = _cert_not_expiring_subcheck(db, service, certs_dir)
        dns_record_present, dns_matches_ip = _check_dns(db, service, None, live=live_dns)
        checks["dns_record_present"] = dns_record_present
        checks["dns_matches_ip"] = dns_matches_ip
        checks["caddy_config_present"] = _check_caddy_config(service, generated_dir)
        return checks

    checks = dict.fromkeys(ALL_CHECK_NAMES, False)
    try:
        # --- Upstream container ---
        checks["upstream_container_present"] = _check_upstream_present(client, service)
        checks["upstream_network_connected"] = _check_upstream_network(client, service)

        # --- Edge container ---
        edge_present, edge_running = _check_edge(client, service)
        checks["edge_container_present"] = edge_present
        checks["edge_container_running"] = edge_running

        # --- Tailscale ---
        ts_ready, ts_ip_present, live_tailscale_ip = _check_tailscale(client, service, edge_running)

        current_ip = live_tailscale_ip
        checks["tailscale_ready"] = ts_ready
        checks["tailscale_ip_present"] = ts_ip_present

        # --- Certs ---
        checks["cert_present"] = _check_cert_present(service, certs_dir)
        checks["cert_not_expiring"] = _cert_not_expiring_subcheck(db, service, certs_dir)

        # --- DNS ---
        checks["dns_record_present"], checks["dns_matches_ip"] = _check_dns(
            db, service, current_ip, live=live_dns
        )

        # --- Caddy config ---
        checks["caddy_config_present"] = _check_caddy_config(service, generated_dir)

        # --- HTTPS probe (spec §18.1) ---
        checks["https_probe_ok"] = probe.check_https_probe(service, current_ip, client)
        return checks
    finally:
        close_client(client)


# ---- Individual check helpers ----


def _check_upstream_present(client: docker.DockerClient, service: Service) -> bool:
    try:
        client.containers.get(service.upstream_container_id)
        return True
    except Exception:
        return False

def _check_stored_dns(db: Session, service: Service, current_ip: str | None) -> tuple[bool, bool]:
    dns_record = db.get(DnsRecord, service.id)
    db_record_present = dns_record is not None and dns_record.record_id is not None
    db_matches_ip = bool(
        dns_record
        and dns_record.value
        and current_ip
        and dns_record.value == current_ip
    )
    return db_record_present, db_matches_ip


def check_live_dns(
    db: Session, service: Service, current_ip: str | None,
) -> tuple[bool, bool, dict[str, object]]:
    """Return live Cloudflare DNS booleans plus manual-check extended fields."""
    db_record_present, db_matches_ip = _check_stored_dns(db, service, current_ip)

    try:
        cf_token = secrets.read_secret(secrets.CLOUDFLARE_TOKEN)
        zone_id = get_setting(db, "cf_zone_id")
    except Exception:
        logger.info("Could not load Cloudflare settings for DNS health", exc_info=True)
        return db_record_present, db_matches_ip, {}

    if not cf_token or not zone_id:
        return (
            db_record_present,
            db_matches_ip,
            {"cf_error": "Cloudflare token or zone ID not configured"},
        )

    try:
        live_record = cloudflare_adapter.find_record(cf_token, zone_id, service.hostname, "A")
    except Exception as e:
        logger.exception("Live Cloudflare DNS verification failed for service %s", service.id)
        return (
            db_record_present,
            False,
            {"cf_error": f"Cloudflare verification failed ({type(e).__name__})"},
        )

    record_ip = live_record.get("content") if live_record else None
    matches_ip = bool(current_ip and record_ip == current_ip)
    return (
        live_record is not None,
        matches_ip,
        {
            "cf_record_exists": live_record is not None,
            "cf_record_ip": record_ip,
            "cf_ip_matches_tailscale": matches_ip,
        },
    )


def _check_dns(db: Session, service: Service, current_ip: str | None, *, live: bool = False) -> tuple[bool, bool]:
    if not live:
        return _check_stored_dns(db, service, current_ip)

    present, matches_ip, _extended = check_live_dns(db, service, current_ip)
    return present, matches_ip



def _check_upstream_network(client: docker.DockerClient, service: Service) -> bool:
    try:
        container = client.containers.get(service.upstream_container_id)
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        return service.network_name in networks
    except Exception:
        return False


def _check_edge(client: docker.DockerClient, service: Service) -> tuple[bool, bool]:
    try:
        container = find_edge_container(
            client, service.id, service.edge_container_name, tolerate_lookup_errors=True
        )
        if container is None:
            return False, False
        return True, container.status == "running"
    except Exception:
        logger.info("Edge container health lookup failed for %s", service.id, exc_info=True)
        return False, False


def _check_tailscale(
    client: docker.DockerClient, service: Service, edge_running: bool
) -> tuple[bool, bool, str | None]:
    if not edge_running:
        return False, False, None
    try:
        container = find_edge_container(
            client, service.id, service.edge_container_name, tolerate_lookup_errors=True
        )
        if container is None:
            return False, False, None
        result = container.exec_run(
            "tailscale status --json",
            environment={"TS_SOCKET": "/var/run/tailscale/tailscaled.sock"},
        )
        if result.exit_code != 0:
            return False, False, None
        data = json.loads(result.output)
        ready = data.get("BackendState") == "Running"
        ts_ips = data.get("Self", {}).get("TailscaleIPs", [])
        tailscale_ip = next((str(ip) for ip in ts_ips if str(ip).startswith("100.")), None)
        return ready, tailscale_ip is not None, tailscale_ip
    except Exception:
        logger.info("Tailscale health lookup failed for %s", service.id, exc_info=True)
        return False, False, None


def _check_cert_present(service: Service, certs_dir: str | Path) -> bool:
    d = Path(certs_dir) / service.hostname / "current"
    return (d / "fullchain.pem").exists() and (d / "privkey.pem").exists()


def _check_cert_not_expiring(
    service: Service, certs_dir: str | Path, renewal_window_days: int
) -> bool:
    cert_path = Path(certs_dir) / service.hostname / "current" / "fullchain.pem"
    if not cert_path.exists():
        return False
    try:
        expiry = cert_manager.get_cert_expiry(cert_path)
        if expiry is None:
            return False
        threshold = days_from_now(renewal_window_days)
        if threshold is None:
            # Window so large the threshold overflows the representable range;
            # no expiry can exceed it, so the cert reads as expiring — matching
            # the prior behavior where the OverflowError was caught as False.
            return False
        return expiry > threshold
    except Exception:
        return False


def _cert_not_expiring_subcheck(db: Session, service: Service, certs_dir: str | Path) -> bool:
    """``cert_not_expiring`` subcheck, resilient to a corrupt renewal-window setting.

    The renewal window comes from ``get_positive_int_setting``, which fails loud
    (raises ``ValueError``) on a corrupt stored value. That fail-loud must stay
    isolated to this one subcheck: a single corrupt *global* setting otherwise
    crashes ``run_health_checks`` outright, staling health for every service in
    the sweep. On a corrupt window we report the subcheck as failing — consistent
    with ``_check_cert_not_expiring`` returning ``False`` on any internal error.
    """
    try:
        window = get_positive_int_setting(db, "cert_renewal_window_days")
    except ValueError:
        logger.warning(
            "cert_renewal_window_days is corrupt; reporting cert_not_expiring as "
            "failing until it is fixed",
            exc_info=True,
        )
        return False
    return _check_cert_not_expiring(service, certs_dir, window)


def _check_caddy_config(service: Service, generated_dir: str | Path) -> bool:
    return (Path(generated_dir) / service.id / "Caddyfile").exists()

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
