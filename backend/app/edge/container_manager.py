"""Edge container lifecycle management."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.types import Mount

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)

from app.edge.image_builder import EDGE_IMAGE, ensure_edge_image
from app.version import __version__


def _get_client(socket_path: str | None = None) -> docker.DockerClient:
    if socket_path:
        return docker.DockerClient(base_url=socket_path)
    return docker.DockerClient.from_env()


def _find_edge_container(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> docker.models.containers.Container | None:
    """Find existing edge container by name or service_id label."""
    client = _get_client(socket_path)
    try:
        return client.containers.get(edge_container_name)
    except docker.errors.NotFound:
        pass

    # Fallback: search by label
    containers = client.containers.list(
        all=True, filters={"label": f"tailbale.service_id={service_id}"}
    )
    return containers[0] if containers else None


def create_edge_container(
    service: Service,
    ts_authkey: str,
    generated_dir: str | Path,
    certs_dir: str | Path,
    tailscale_state_dir: str | Path,
    socket_path: str | None = None,
    edge_image: str = EDGE_IMAGE,
) -> str:
    """Create an edge container for a service. Returns the container ID."""
    generated_dir = Path(generated_dir)
    certs_dir = Path(certs_dir)
    tailscale_state_dir = Path(tailscale_state_dir)

    ensure_edge_image(socket_path)
    client = _get_client(socket_path)

    # Prepare mount paths
    caddyfile_path = str(generated_dir / service.id / "Caddyfile")
    cert_dir = str(certs_dir / service.hostname)
    ts_state_dir = str(tailscale_state_dir / service.edge_container_name)

    # Ensure host directories exist
    Path(cert_dir).mkdir(parents=True, exist_ok=True)
    Path(ts_state_dir).mkdir(parents=True, exist_ok=True)

    # Ensure Caddyfile exists; Docker bind mounts fail if the source is missing.
    caddyfile = Path(caddyfile_path)
    if not caddyfile.exists():
        caddyfile.parent.mkdir(parents=True, exist_ok=True)
        placeholder = (
            "# Placeholder — will be replaced by reconciler\n"
            ":443 {\n"
            '    respond "Service starting..." 503\n'
            "}\n"
        )
        caddyfile.write_text(placeholder)
        logger.warning("Caddyfile did not exist; created placeholder at %s", caddyfile_path)

    mounts = [
        Mount(target="/etc/caddy/Caddyfile", source=caddyfile_path, type="bind", read_only=True),
        Mount(target="/certs", source=cert_dir, type="bind", read_only=True),
        Mount(target="/var/lib/tailscale", source=ts_state_dir, type="bind"),
    ]

    environment = {
        "TS_AUTHKEY": ts_authkey,
        "TS_HOSTNAME": service.ts_hostname,
    }

    labels = {
        "tailbale.managed": "true",
        "tailbale.service_id": service.id,
        "tailbale.version": __version__,
    }

    container = client.containers.create(
        image=edge_image,
        name=service.edge_container_name,
        mounts=mounts,
        environment=environment,
        labels=labels,
        network=service.network_name,
        detach=True,
        restart_policy={"Name": "unless-stopped"},
    )

    logger.info(
        "Created edge container %s (id=%s) for service %s",
        service.edge_container_name,
        container.id,
        service.id,
    )
    return container.id


def start_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Start an existing edge container."""
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        raise RuntimeError(f"Edge container not found for service {service_id}")
    container.start()
    logger.info("Started edge container %s", edge_container_name)


def stop_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Stop an edge container."""
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        logger.info("Edge container not found for service %s, nothing to stop", service_id)
        return
    container.stop(timeout=10)
    logger.info("Stopped edge container %s", edge_container_name)


def restart_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Restart an edge container."""
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        raise RuntimeError(f"Edge container not found for service {service_id}")
    container.restart(timeout=10)
    logger.info("Restarted edge container %s", edge_container_name)


def remove_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Remove an edge container (stop first if running)."""
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        logger.info("Edge container not found for service %s, nothing to remove", service_id)
        return
    container.remove(force=True)
    logger.info("Removed edge container %s", edge_container_name)


def recreate_edge(
    service: Service,
    ts_authkey: str,
    generated_dir: str | Path,
    certs_dir: str | Path,
    tailscale_state_dir: str | Path,
    socket_path: str | None = None,
    edge_image: str = EDGE_IMAGE,
) -> str:
    """Remove existing edge and create + start a new one. Returns new container ID."""
    remove_edge(service.id, service.edge_container_name, socket_path)
    container_id = create_edge_container(
        service, ts_authkey, generated_dir, certs_dir, tailscale_state_dir,
        socket_path, edge_image,
    )
    start_edge(service.id, service.edge_container_name, socket_path)
    return container_id


def get_edge_version(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
) -> str | None:
    """Read the tailbale.version label from an edge container.

    Returns the version string, or None if the container doesn't exist
    or has no version label.
    """
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        return None
    return container.labels.get("tailbale.version")


def get_edge_logs(
    service_id: str,
    edge_container_name: str,
    tail: int = 100,
    socket_path: str | None = None,
) -> str:
    """Fetch recent logs from an edge container."""
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        return ""
    return container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")


def reload_caddy(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> str:
    """Execute `caddy reload` inside the edge container. Returns exec output."""
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        raise RuntimeError(f"Edge container not found for service {service_id}")

    exit_code, output = container.exec_run(
        "caddy reload --config /etc/caddy/Caddyfile"
    )
    result = output.decode("utf-8", errors="replace")
    if exit_code != 0:
        raise RuntimeError(f"Caddy reload failed (exit {exit_code}): {result}")
    logger.info("Reloaded Caddy in edge container %s", edge_container_name)
    return result


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
    container = _find_edge_container(service_id, edge_container_name, socket_path)
    if not container:
        return None

    for attempt in range(max_retries):
        try:
            # Try `tailscale ip -4` first
            exit_code, output = container.exec_run(
                "tailscale ip -4 --socket=/var/run/tailscale/tailscaled.sock"
            )
            if exit_code == 0:
                ip = output.decode("utf-8", errors="replace").strip()
                if ip and ip.startswith("100."):
                    logger.info("Detected Tailscale IP %s for %s", ip, edge_container_name)
                    return ip

            # Fallback: parse `tailscale status --json`
            exit_code, output = container.exec_run(
                "tailscale status --json --socket=/var/run/tailscale/tailscaled.sock"
            )
            if exit_code == 0:
                status = json.loads(output.decode("utf-8", errors="replace"))
                ts_ips = status.get("Self", {}).get("TailscaleIPs", [])
                for addr in ts_ips:
                    if addr.startswith("100."):
                        logger.info("Detected Tailscale IP %s for %s (via status)", addr, edge_container_name)
                        return addr
        except Exception:
            logger.debug("Attempt %d failed for %s", attempt + 1, edge_container_name, exc_info=True)

        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    logger.warning("Failed to detect Tailscale IP for %s after %d attempts", edge_container_name, max_retries)
    return None
