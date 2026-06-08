"""Docker network management for edge containers."""

from __future__ import annotations

import logging

import docker

logger = logging.getLogger(__name__)


def _get_client(socket_path: str | None = None) -> docker.DockerClient:
    if socket_path:
        return docker.DockerClient(base_url=socket_path)
    return docker.DockerClient.from_env()


def create_network(network_name: str, socket_path: str | None = None) -> str:
    """Create a bridge network for an edge container. Returns the network ID."""
    client = _get_client(socket_path)
    try:
        existing = client.networks.get(network_name)
        logger.info("Network %s already exists (id=%s)", network_name, existing.id)
        return existing.id
    except docker.errors.NotFound:
        pass

    network = client.networks.create(network_name, driver="bridge")
    logger.info("Created network %s (id=%s)", network_name, network.id)
    return network.id


def remove_network(network_name: str, socket_path: str | None = None) -> None:
    """Remove a network if it exists."""
    client = _get_client(socket_path)
    try:
        network = client.networks.get(network_name)
        network.remove()
        logger.info("Removed network %s", network_name)
    except docker.errors.NotFound:
        logger.info("Network %s not found, nothing to remove", network_name)




def _resolve_container(
    client: docker.DockerClient,
    container_id: str,
    container_name: str | None = None,
):
    try:
        return client.containers.get(container_id)
    except docker.errors.NotFound:
        if not container_name:
            raise
        container = client.containers.get(container_name)
        logger.info(
            "Resolved upstream container %s to new ID %s after stale ID %s was missing",
            container_name,
            container.id,
            container_id,
        )
        return container


def connect_container(
    network_name: str,
    container_id: str,
    socket_path: str | None = None,
    container_name: str | None = None,
) -> str:
    """Connect a container to a network (idempotent). Returns the resolved container ID."""
    client = _get_client(socket_path)
    network = client.networks.get(network_name)
    container = _resolve_container(client, container_id, container_name)

    # Check if already connected
    container.reload()
    connected_networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    if network_name in connected_networks:
        logger.info("Container %s already on network %s", container.id, network_name)
        return container.id

    network.connect(container)
    logger.info("Connected container %s to network %s", container.id, network_name)
    return container.id


def ensure_network(
    network_name: str,
    app_container_id: str,
    socket_path: str | None = None,
    app_container_name: str | None = None,
) -> tuple[str, str]:
    """Idempotent: create network if absent, connect app container if not connected.

    Returns ``(network_id, resolved_container_id)``.
    """
    network_id = create_network(network_name, socket_path)
    resolved_container_id = connect_container(
        network_name,
        app_container_id,
        socket_path,
        app_container_name,
    )
    return network_id, resolved_container_id
