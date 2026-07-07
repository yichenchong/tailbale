"""Tailscale control-plane (admin-API) device operations.

A leaf module: reading a node's Tailscale ID out of a running container and
deleting a device from the tailnet depend only on ``docker``/``httpx2`` — never
on ``container_manager`` or ``tailscale_ops``. Keeping them here lets
``container_manager.remove_edge`` import them at module top (one-way,
``container_manager -> tailscale_device``) instead of via a call-time deferred
import, so the edge module graph is fully acyclic with no cycle-breaker imports.

The data-plane counterpart — detecting the assigned Tailscale IP by exec-ing into
the running container — lives in ``tailscale_ops`` (which needs the container
lifecycle primitives); this module is the control-plane (api.tailscale.com) half.
"""

from __future__ import annotations

import json
import logging

import docker
import httpx2

logger = logging.getLogger(__name__)


def _get_tailscale_node_id(
    container: docker.models.containers.Container,
) -> str | None:
    """Extract the Tailscale node ID from inside a running edge container."""
    try:
        exit_code, output = container.exec_run(
            "tailscale status --json",
            environment={"TS_SOCKET": "/var/run/tailscale/tailscaled.sock"},
        )
        if exit_code != 0:
            return None
        status = json.loads(output.decode("utf-8", errors="replace"))
        return status.get("Self", {}).get("ID")
    except Exception:
        return None


def _delete_tailscale_device(node_id: str, api_key: str) -> bool:
    """Delete a device from the tailnet via the Tailscale API.

    Uses ``DELETE /api/v2/device/{nodeId}`` with Basic auth.
    Returns True on success (200/2xx), False on any failure.
    """
    try:
        resp = httpx2.delete(
            f"https://api.tailscale.com/api/v2/device/{node_id}",
            auth=(api_key, ""),
            timeout=10.0,
        )
        if resp.is_success:
            logger.info("Deleted Tailscale device %s via API", node_id)
            return True
        logger.warning(
            "Tailscale API device deletion returned %d for node %s: %s",
            resp.status_code, node_id, resp.text[:200],
        )
        return False
    except Exception:
        logger.info("Tailscale API device deletion failed for node %s", node_id, exc_info=True)
        return False
