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

def _custom_aliases(container, network_name: str) -> set[str]:
    """Return non-automatic aliases Docker reports for a container endpoint."""
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    endpoint = networks.get(network_name) or {}
    # Union the deprecated per-endpoint ``Aliases`` with ``DNSNames`` (Docker
    # Engine 25+). Relying on ``Aliases`` alone would make the steady-state
    # comparison always fail on an engine that only populates ``DNSNames``,
    # forcing a disconnect/reconnect churn on every reconcile. The automatic
    # names (container name/id/hostname) are subtracted below either way.
    aliases = set(endpoint.get("Aliases") or []) | set(endpoint.get("DNSNames") or [])
    automatic = {
        getattr(container, "name", ""),
        getattr(container, "id", ""),
        str(getattr(container, "id", ""))[:12],
        str(container.attrs.get("Name", "")).lstrip("/"),
    }
    return {alias for alias in aliases if alias and alias not in automatic}


def _already_connected(exc: docker.errors.APIError) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "already connected" in message


def _connect_with_aliases(network, container, aliases: list[str]) -> None:
    try:
        network.connect(container, aliases=aliases)
    except docker.errors.APIError as exc:
        if not _already_connected(exc):
            raise
        container.reload()
        connected_networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        if network.name not in connected_networks:
            raise
        if _custom_aliases(container, network.name) == set(aliases):
            return
        network.disconnect(container, force=True)
        container.reload()
        network.connect(container, aliases=aliases)


def reconcile_additional_edge_networks(
    edge_container_name: str,
    primary_network_name: str,
    additional_networks: list[dict] | None,
    socket_path: str | None = None,
) -> None:
    """Converge operator-owned edge network attachments and aliases.

    Tailbale owns the edge container, so every non-primary Docker network
    attachment is treated as managed desired state: configured networks are
    connected with exact aliases, and any other non-primary networks are
    disconnected. The primary per-service network is never disconnected or
    re-aliased here.
    """
    desired: dict[str, list[str]] = {}
    for item in additional_networks or []:
        name = str(item.get("name", ""))
        if name == primary_network_name:
            logger.warning(
                "Ignoring additional edge network %s because it is the primary network",
                name,
            )
            continue
        desired[name] = list(item.get("aliases") or [])

    with docker_client(socket_path) as client:
        container = client.containers.get(edge_container_name)

        # External networks are operator-owned. Require them to already exist so
        # a typo fails reconciliation instead of silently creating a wrong bridge.
        desired_networks = {
            name: client.networks.get(name)
            for name in desired
        }

        container.reload()
        connected = container.attrs.get("NetworkSettings", {}).get("Networks", {})

        for name, aliases in desired.items():
            network = desired_networks[name]
            if name in connected:
                if _custom_aliases(container, name) == set(aliases):
                    logger.info(
                        "Edge container %s already connected to %s with desired aliases",
                        edge_container_name,
                        name,
                    )
                    continue
                network.disconnect(container, force=True)
                container.reload()

            _connect_with_aliases(network, container, aliases)
            logger.info(
                "Connected edge container %s to %s with aliases %s",
                edge_container_name,
                name,
                aliases,
            )
            container.reload()
            connected = container.attrs.get("NetworkSettings", {}).get("Networks", {})

        for name in list(connected):
            if name == primary_network_name or name in desired:
                continue
            try:
                network = client.networks.get(name)
            except docker.errors.NotFound:
                continue
            try:
                network.disconnect(container, force=True)
            except docker.errors.APIError:
                # Best-effort prune: a transient daemon error detaching a network
                # the operator never asked us to manage must not fail the whole
                # service reconcile. Log and move on; the next pass retries.
                logger.warning(
                    "Failed to disconnect edge container %s from unmanaged network %s",
                    edge_container_name,
                    name,
                    exc_info=True,
                )
                continue
            logger.info(
                "Disconnected edge container %s from unmanaged additional network %s",
                edge_container_name,
                name,
            )


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
