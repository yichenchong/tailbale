"""Per-service health subchecks.

Runs a battery of checks against the real system state and returns
a dict of check_name -> bool.  The reconciler calls this at the end
of each cycle; it can also be invoked standalone.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import docker

from app.models.dns_record import DnsRecord
from app.models.service_status import ServiceStatus

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.service import Service

logger = logging.getLogger(__name__)

# Checks whose failure means "error" status
CRITICAL_CHECKS = frozenset({
    "edge_container_present",
    "edge_container_running",
    "tailscale_ip_present",
    "cert_present",
})

# Checks whose failure means "warning" (unless a critical already failed)
WARNING_CHECKS = frozenset({
    "cert_not_expiring",
    "dns_matches_ip",
    "https_probe_ok",
})


def _get_docker_client(socket_path: str | None = None) -> docker.DockerClient:
    if socket_path:
        return docker.DockerClient(base_url=socket_path)
    return docker.DockerClient.from_env()


def run_health_checks(
    db: Session,
    service: Service,
    generated_dir: str | Path,
    certs_dir: str | Path,
    socket_path: str | None = None,
) -> dict[str, bool]:
    """Run all health subchecks for *service*.  Returns {name: bool}."""
    checks: dict[str, bool] = {}

    try:
        client = _get_docker_client(socket_path)
    except Exception:
        logger.warning("Cannot connect to Docker for health checks", exc_info=True)
        return {
            "upstream_container_present": False,
            "upstream_network_connected": False,
            "edge_container_present": False,
            "edge_container_running": False,
            "tailscale_ready": False,
            "tailscale_ip_present": False,
            "cert_present": _check_cert_present(service, certs_dir),
            "cert_not_expiring": False,
            "dns_record_present": False,
            "dns_matches_ip": False,
            "caddy_config_present": _check_caddy_config(service, generated_dir),
            "https_probe_ok": False,
        }

    # --- Upstream container ---
    checks["upstream_container_present"] = _check_upstream_present(client, service)
    checks["upstream_network_connected"] = _check_upstream_network(client, service)

    # --- Edge container ---
    edge_present, edge_running = _check_edge(client, service)
    checks["edge_container_present"] = edge_present
    checks["edge_container_running"] = edge_running

    # --- Tailscale ---
    ts_ready, ts_ip_present = _check_tailscale(client, service, edge_running)
    checks["tailscale_ready"] = ts_ready
    checks["tailscale_ip_present"] = ts_ip_present

    # --- Certs ---
    checks["cert_present"] = _check_cert_present(service, certs_dir)
    checks["cert_not_expiring"] = _check_cert_not_expiring(service, certs_dir)

    # --- DNS ---
    status = db.get(ServiceStatus, service.id)
    current_ip = status.tailscale_ip if status else None
    dns_record = db.get(DnsRecord, service.id)

    checks["dns_record_present"] = (
        dns_record is not None and dns_record.record_id is not None
    )
    checks["dns_matches_ip"] = bool(
        dns_record
        and dns_record.value
        and current_ip
        and dns_record.value == current_ip
    )

    # --- Caddy config ---
    checks["caddy_config_present"] = _check_caddy_config(service, generated_dir)

    # --- HTTPS probe (spec §18.1) ---
    checks["https_probe_ok"] = _check_https_probe(service, current_ip, certs_dir)

    return checks


# ---- Individual check helpers ----


def _check_upstream_present(client: docker.DockerClient, service: Service) -> bool:
    try:
        client.containers.get(service.upstream_container_id)
        return True
    except Exception:
        return False


def _check_upstream_network(client: docker.DockerClient, service: Service) -> bool:
    try:
        container = client.containers.get(service.upstream_container_id)
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        return service.network_name in networks
    except Exception:
        return False


def _check_edge(client: docker.DockerClient, service: Service) -> tuple[bool, bool]:
    try:
        container = client.containers.get(service.edge_container_name)
        return True, container.status == "running"
    except Exception:
        return False, False


def _check_tailscale(
    client: docker.DockerClient, service: Service, edge_running: bool
) -> tuple[bool, bool]:
    if not edge_running:
        return False, False
    try:
        container = client.containers.get(service.edge_container_name)
        result = container.exec_run(
            "tailscale status --json",
            environment={"TS_SOCKET": "/var/run/tailscale/tailscaled.sock"},
        )
        if result.exit_code != 0:
            return False, False
        data = json.loads(result.output)
        ready = data.get("BackendState") == "Running"
        ts_ips = data.get("Self", {}).get("TailscaleIPs", [])
        ip_present = any(str(ip).startswith("100.") for ip in ts_ips)
        return ready, ip_present
    except Exception:
        return False, False


def _check_cert_present(service: Service, certs_dir: str | Path) -> bool:
    d = Path(certs_dir) / service.hostname
    return (d / "fullchain.pem").exists() and (d / "privkey.pem").exists()


def _check_cert_not_expiring(service: Service, certs_dir: str | Path) -> bool:
    cert_path = Path(certs_dir) / service.hostname / "fullchain.pem"
    if not cert_path.exists():
        return False
    try:
        from app.certs.cert_manager import get_cert_expiry

        expiry = get_cert_expiry(cert_path)
        if expiry is None:
            return False
        return expiry > datetime.now(timezone.utc) + timedelta(days=14)
    except Exception:
        return False


def _check_caddy_config(service: Service, generated_dir: str | Path) -> bool:
    return (Path(generated_dir) / service.id / "Caddyfile").exists()


def _check_https_probe(
    service: "Service", tailscale_ip: str | None, certs_dir: str | Path,
) -> bool:
    """Attempt an actual HTTPS request to the edge container via its Tailscale IP.

    We verify the TLS handshake succeeds by building an SSL context that
    trusts the service's own fullchain.pem as the CA bundle.  Hostname
    verification is disabled because the cert is issued for the public
    hostname, not the Tailscale IP we connect to.  A short timeout is used.
    """
    if not tailscale_ip:
        return False

    cert_path = Path(certs_dir) / service.hostname
    fullchain = cert_path / "fullchain.pem"
    if not fullchain.exists():
        return False

    try:
        import ssl

        import httpx

        # Build an SSL context that actually verifies the TLS certificate
        # chain against the service's own fullchain.pem.  We disable
        # hostname checking because we connect by IP, not by hostname.
        ctx = ssl.create_default_context(cafile=str(fullchain))
        ctx.check_hostname = False

        # Probe via Tailscale IP, setting Host header so Caddy routes correctly
        url = f"https://{tailscale_ip}:443/"
        resp = httpx.get(
            url,
            headers={"Host": service.hostname},
            verify=ctx,
            timeout=5.0,
            follow_redirects=True,
        )
        # Any response that isn't a 5xx means the edge is serving
        return resp.status_code < 500
    except Exception:
        return False


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
