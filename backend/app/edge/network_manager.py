"""Docker network management for edge containers."""

from __future__ import annotations

import logging

import docker

from app.edge.docker_client import docker_client

logger = logging.getLogger(__name__)


def create_network(network_name: str, socket_path: str | None = None) -> str:
    """Create a bridge network for an edge container. Returns the network ID."""
    with docker_client(socket_path) as client:
        try:
            existing = client.networks.get(network_name)
            logger.info("Network %s already exists (id=%s)", network_name, existing.id)
            return existing.id
        except docker.errors.NotFound:
            pass

        try:
            network = client.networks.create(network_name, driver="bridge")
        except docker.errors.APIError as create_exc:
            # Lost a create race: another caller created the network between our
            # get above and this create (modern daemons reject duplicate names
            # with a 409). Recover by returning the existing one; re-raise the
            # original error if the network genuinely was not created.
            try:
                existing = client.networks.get(network_name)
            except docker.errors.NotFound:
                raise create_exc from None
            logger.info(
                "Network %s was created concurrently (id=%s)", network_name, existing.id
            )
            return existing.id
        logger.info("Created network %s (id=%s)", network_name, network.id)
        return network.id


def _disconnect_all_endpoints(network: docker.models.networks.Network) -> None:
    """Force-disconnect every container still attached to a network.

    The per-service network keeps the upstream container attached (connected
    via ``ensure_network``). After the edge container is removed the upstream
    endpoint lingers, so Docker refuses ``network.remove()`` with
    "has active endpoints". Disconnect them so the network can be torn down.
    """
    try:
        network.reload()
    except docker.errors.APIError:
        logger.debug("Failed to reload network %s before disconnect", network.name, exc_info=True)
    endpoints = network.attrs.get("Containers") or {}
    for container_id in list(endpoints):
        try:
            network.disconnect(container_id, force=True)
            logger.info("Disconnected container %s from network %s", container_id, network.name)
        except docker.errors.NotFound:
            continue
        except docker.errors.APIError:
            logger.warning(
                "Failed to disconnect container %s from network %s",
                container_id, network.name, exc_info=True,
            )


def remove_network(network_name: str, socket_path: str | None = None) -> None:
    """Remove a network if it exists.

    Any still-attached endpoints (notably the upstream container connected via
    ``ensure_network``) are disconnected first so Docker does not refuse the
    removal with "has active endpoints" and leak the per-service network.
    """
    with docker_client(socket_path) as client:
        try:
            network = client.networks.get(network_name)
        except docker.errors.NotFound:
            logger.info("Network %s not found, nothing to remove", network_name)
            return

        try:
            network.remove()
            logger.info("Removed network %s", network_name)
            return
        except docker.errors.NotFound:
            return
        except docker.errors.APIError:
            # Most likely "has active endpoints" — disconnect attached
            # containers and retry once.
            logger.info(
                "Network %s could not be removed directly, disconnecting endpoints first",
                network_name,
            )

        _disconnect_all_endpoints(network)
        try:
            network.remove()
            logger.info("Removed network %s after disconnecting endpoints", network_name)
        except docker.errors.NotFound:
            logger.info("Network %s already removed", network_name)
        except docker.errors.APIError:
            logger.warning(
                "Network %s could not be removed even after disconnecting endpoints; "
                "it may leak. Manual cleanup may be required.",
                network_name,
                exc_info=True,
            )


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
    with docker_client(socket_path) as client:
        network = client.networks.get(network_name)
        container = _resolve_container(client, container_id, container_name)

        # Check if already connected
        container.reload()
        connected_networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        if network_name in connected_networks:
            logger.info("Container %s already on network %s", container.id, network_name)
            return container.id

        try:
            network.connect(container)
        except docker.errors.APIError as exc:
            message = str(exc).lower()
            if "already exists" not in message and "already connected" not in message:
                raise
            container.reload()
            connected_networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
            if network_name not in connected_networks:
                raise
            logger.info(
                "Container %s became connected to network %s during connect",
                container.id, network_name,
            )
            return container.id
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
