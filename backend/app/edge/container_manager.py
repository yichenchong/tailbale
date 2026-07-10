"""Edge container lifecycle management.

The container identity/lookup/session/wait primitives (:func:`container_service_id`,
:func:`is_container_for_service`, :func:`find_edge_container`,
:func:`_find_edge_container`, :func:`_find_edge_container_for_use`,
:func:`edge_container`, :func:`_wait_for_running`) live in the leaf
:mod:`app.edge.container_session` (AR5). This module imports them for its own
lifecycle mutations and re-exports them so existing importers
(``app.edge.container_manager.find_edge_container`` / ``_find_edge_container`` /
``edge_container`` / ``_wait_for_running``, used by the health checker, HTTPS
probe, and reconciler steps) keep resolving unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import docker
from docker.types import Mount

from app import secrets
from app.edge.container_session import (
    _find_edge_container,
    _find_edge_container_for_use,
    _wait_for_running,
    container_service_id,
    edge_container,
    find_edge_container,
    is_container_for_service,
)
from app.edge.docker_client import docker_client
from app.edge.image_builder import EDGE_IMAGE, ensure_edge_image
from app.edge.tailscale_device import _delete_tailscale_device, _get_tailscale_node_id
from app.version import __version__

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)

# Re-exported from the container_session leaf so the historical import paths keep
# resolving (the health checker, HTTPS probe, and reconciler steps import these
# via ``app.edge.container_manager``).
__all__ = [
    "_find_edge_container",
    "_find_edge_container_for_use",
    "_wait_for_running",
    "container_service_id",
    "create_edge_container",
    "edge_container",
    "find_edge_container",
    "get_edge_logs",
    "get_edge_version",
    "is_container_for_service",
    "recreate_edge",
    "remove_edge",
    "restart_edge",
    "start_edge",
    "stop_edge",
]


def create_edge_container(
    service: Service,
    ts_authkey: str,
    generated_dir: str | Path,
    certs_dir: str | Path,
    tailscale_state_dir: str | Path,
    socket_path: str | None = None,
    edge_image: str = EDGE_IMAGE,
    ts_control_url: str | None = None,
) -> str:
    """Create an edge container for a service. Returns the container ID.

    The *_dir paths must be resolvable by the **Docker host**.  When tailBale
    runs inside a container that shares the host's Docker socket, these must
    be the host-side paths (see ``HOST_DATA_DIR``).  The caller (reconciler)
    is responsible for creating directories and writing files at the internal
    (container-local) equivalents before calling this function.
    """
    generated_dir = Path(generated_dir)
    certs_dir = Path(certs_dir)
    tailscale_state_dir = Path(tailscale_state_dir)

    ensure_edge_image(socket_path)
    with docker_client(socket_path) as client:
        # Prepare mount paths (these are host-side paths for Docker bind mounts).
        # The Caddyfile directory is mounted (not the file itself) to avoid
        # stale-inode issues when the file is atomically replaced on the host.
        caddy_config_dir = str(generated_dir / service.id)
        cert_dir = str(certs_dir / service.hostname)
        ts_state_dir = str(tailscale_state_dir / service.edge_container_name)

        mounts = [
            Mount(target="/etc/caddy", source=caddy_config_dir, type="bind", read_only=True),
            Mount(target="/certs", source=cert_dir, type="bind", read_only=True),
            Mount(target="/var/lib/tailscale", source=ts_state_dir, type="bind"),
        ]

        environment = {
            "TS_AUTHKEY": ts_authkey,
            "TS_HOSTNAME": service.ts_hostname,
        }
        if ts_control_url:
            environment["TS_LOGIN_SERVER"] = ts_control_url

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
    with edge_container(service_id, edge_container_name, socket_path) as (_client, container):
        if not container:
            raise RuntimeError(f"Edge container not found for service {service_id}")
        container.start()
        logger.info("Started edge container %s", edge_container_name)


def stop_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Stop an edge container."""
    with edge_container(service_id, edge_container_name, socket_path) as (_client, container):
        if not container:
            logger.info("Edge container not found for service %s, nothing to stop", service_id)
            return
        container.stop(timeout=10)
        logger.info("Stopped edge container %s", edge_container_name)


def restart_edge(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> None:
    """Restart an edge container."""
    with edge_container(service_id, edge_container_name, socket_path) as (_client, container):
        if not container:
            raise RuntimeError(f"Edge container not found for service {service_id}")
        container.restart(timeout=10)
        logger.info("Restarted edge container %s", edge_container_name)


def remove_edge(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
    *,
    delete_device: bool = True,
    raise_on_error: bool = False,
) -> None:
    """Force-remove an edge container.

    The container is force-removed (``remove(force=True)``) — there is no
    graceful stop first.

    When ``delete_device`` is true and a running container exposes a Tailscale
    node ID, a best-effort call to the Tailscale API deletes the device from the
    tailnet so it doesn't linger as an offline machine in the admin console.
    Device deletion happens only when a Tailscale API key is configured;
    otherwise it is skipped. Pass ``delete_device=False`` to preserve the node's
    tailnet identity/IP across a container swap (the TS state dir is kept).

    When ``raise_on_error`` is true a docker ``APIError`` from the removal is
    re-raised, so callers like ``recreate_edge`` fail cleanly instead of hitting
    a later opaque 409 name conflict; by default it is logged and swallowed
    (best-effort).
    """
    with edge_container(service_id, edge_container_name, socket_path) as (_client, container):
        if not container:
            logger.info("Edge container not found for service %s, nothing to remove", service_id)
            return

        # Best-effort: remove the Tailscale device via API before destroying the
        # container. Skipped when delete_device is False to preserve the node's
        # tailnet identity/IP across a container swap.
        if delete_device and container.status == "running":
            node_id = _get_tailscale_node_id(container)
            if node_id:
                api_key = secrets.read_secret(secrets.TAILSCALE_API_KEY)
                if api_key:
                    _delete_tailscale_device(node_id, api_key)
                else:
                    logger.info(
                        "Tailscale API key not configured — skipping device removal for %s",
                        edge_container_name,
                    )
            else:
                logger.info("Could not get Tailscale node ID for %s", edge_container_name)

        try:
            container.remove(force=True)
            logger.info("Removed edge container %s", edge_container_name)
        except docker.errors.NotFound:
            logger.info("Edge container %s already removed", edge_container_name)
            return
        except docker.errors.APIError:
            logger.warning("Failed to remove edge container %s", edge_container_name, exc_info=True)
            if raise_on_error:
                raise
            return


def recreate_edge(
    service: Service,
    ts_authkey: str,
    generated_dir: str | Path,
    certs_dir: str | Path,
    tailscale_state_dir: str | Path,
    socket_path: str | None = None,
    edge_image: str = EDGE_IMAGE,
    ts_control_url: str | None = None,
) -> str:
    """Remove existing edge and create + start a new one. Returns new container ID."""
    remove_edge(
        service.id, service.edge_container_name, socket_path,
        delete_device=False, raise_on_error=True,
    )
    container_id = create_edge_container(
        service, ts_authkey, generated_dir, certs_dir, tailscale_state_dir,
        socket_path, edge_image, ts_control_url,
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
    labels = getattr(container, "labels", None)
    if not isinstance(labels, dict):
        return None
    return labels.get("tailbale.version")


def get_edge_logs(
    service_id: str,
    edge_container_name: str,
    tail: int = 100,
    socket_path: str | None = None,
) -> str:
    """Fetch recent logs from an edge container."""
    with edge_container(service_id, edge_container_name, socket_path) as (_client, container):
        if not container:
            return ""
        return container.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
