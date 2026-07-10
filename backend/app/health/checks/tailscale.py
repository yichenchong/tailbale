"""Tailscale readiness / live-IP subcheck (AR18)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import docker

from app.edge.container_session import find_edge_container

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)


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
