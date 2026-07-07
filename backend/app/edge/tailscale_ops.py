"""Tailscale IP detection for edge containers (data-plane).

Split out of ``container_manager`` (AR-R3-15): detecting the Tailscale IPv4
assigned to a running edge container by exec-ing ``tailscale ip``/``status``
inside it is a distinct concern from container lifecycle. It needs the shared
client-lifecycle primitive :func:`~app.edge.container_manager.edge_container`
and the container-state helper
:func:`~app.edge.container_manager._wait_for_running`, imported one-way from
``container_manager`` (which does not import this module — the graph is acyclic).

The control-plane counterpart (Tailscale admin-API device delete) lives in the
leaf :mod:`app.edge.tailscale_device`, which ``container_manager.remove_edge``
imports directly.
"""

from __future__ import annotations

import json
import logging
import time

from app.edge.container_manager import _wait_for_running, edge_container

logger = logging.getLogger(__name__)


def detect_tailscale_ip(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
    max_retries: int = 10,
    retry_delay: float = 2.0,
) -> str | None:
    """Detect the Tailscale IPv4 address assigned to an edge container.

    Retries with backoff since Tailscale auth can take a few seconds.
    Returns the IP string or None if detection fails.
    """
    with edge_container(service_id, edge_container_name, socket_path) as (_client, container):
        if not container:
            return None

        # Wait for the container to be running before attempting exec.
        if not _wait_for_running(container):
            logger.warning(
                "Container %s not running (status=%s), skipping IP detection",
                edge_container_name, container.status,
            )
            return None

        for attempt in range(max_retries):
            try:
                # Re-check state on each retry — container may have restarted.
                container.reload()
                if container.status != "running" and not _wait_for_running(container, timeout=10.0):
                    logger.info("Container %s left running state on attempt %d", edge_container_name, attempt + 1)
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                    continue

                ts_env = {"TS_SOCKET": "/var/run/tailscale/tailscaled.sock"}

                # Try `tailscale ip -4` first
                exit_code, output = container.exec_run(
                    "tailscale ip -4", environment=ts_env,
                )
                if exit_code == 0:
                    ip = output.decode("utf-8", errors="replace").strip()
                    if ip and ip.startswith("100."):
                        logger.info("Detected Tailscale IP %s for %s", ip, edge_container_name)
                        return ip

                # Fallback: parse `tailscale status --json`
                exit_code, output = container.exec_run(
                    "tailscale status --json", environment=ts_env,
                )
                if exit_code == 0:
                    status = json.loads(output.decode("utf-8", errors="replace"))
                    ts_ips = status.get("Self", {}).get("TailscaleIPs", [])
                    for addr in ts_ips:
                        if addr.startswith("100."):
                            logger.info("Detected Tailscale IP %s for %s (via status)", addr, edge_container_name)
                            return addr
            except Exception:
                logger.info("Attempt %d failed for %s", attempt + 1, edge_container_name, exc_info=True)

            if attempt < max_retries - 1:
                time.sleep(retry_delay)

        logger.warning("Failed to detect Tailscale IP for %s after %d attempts", edge_container_name, max_retries)
        return None
