"""Edge container identity, lookup, session, and wait primitives (leaf).

Extracted from ``container_manager`` (AR5): the container identity/lookup helpers
(:func:`container_service_id`, :func:`is_container_for_service`,
:func:`find_edge_container`, :func:`_find_edge_container`,
:func:`_find_edge_container_for_use`), the client-lifecycle context manager
:func:`edge_container`, and the container-state gate :func:`_wait_for_running`
are a distinct concern from container lifecycle mutations. This leaf owns them
and is imported one-way by ``container_manager`` (which re-exports them for its
own lifecycle functions and existing importers), the Caddy-admin helper, and the
Tailscale IP-detection helper. It does not import any of those modules, so the
dependency graph stays acyclic.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

import docker

from app.edge.docker_client import close_client, connect, docker_client

logger = logging.getLogger(__name__)


def container_service_id(container: docker.models.containers.Container) -> str | None:
    """Return the tailBale service id label from a Docker container."""
    labels = getattr(container, "labels", None)
    if not isinstance(labels, dict):
        return None
    return labels.get("tailbale.service_id")


def is_container_for_service(
    container: docker.models.containers.Container,
    service_id: str,
) -> bool:
    label = container_service_id(container)
    return label in (None, service_id)


def find_edge_container(
    client: docker.DockerClient,
    service_id: str,
    edge_container_name: str,
    *,
    tolerate_lookup_errors: bool = False,
) -> docker.models.containers.Container | None:
    """Locate an edge container on an already-open *client*.

    Tries the named container first (ignoring any whose service-id label points
    at a different service), then falls back to a label search so Docker
    ID/name changes still resolve. This is the single lookup implementation
    shared by the edge lifecycle helpers and the health checker.

    The named-lookup step normally swallows only ``NotFound`` and lets any other
    error (APIError / connection) propagate, so a lifecycle caller never mistakes
    a transient daemon fault for "container absent" and creates a duplicate. The
    health checker, however, must stay resilient: a transient non-``NotFound``
    fault on ``containers.get`` should still fall through to the label search
    rather than degrade the service to unhealthy. Pass
    ``tolerate_lookup_errors=True`` (health path only) to restore that broader
    tolerance; an error raised by the label ``list`` itself still propagates in
    both modes (that path was always broad-then-propagate).
    """
    try:
        container = client.containers.get(edge_container_name)
        if is_container_for_service(container, service_id):
            return container
        logger.warning(
            "Ignoring container named %s because it belongs to service %s, not %s",
            edge_container_name,
            container_service_id(container),
            service_id,
        )
    except docker.errors.NotFound:
        pass
    except Exception:
        if not tolerate_lookup_errors:
            raise
        logger.debug(
            "Named lookup of %s failed transiently; falling back to label search",
            edge_container_name,
            exc_info=True,
        )

    # Fallback: search by label. This also handles Docker ID/name changes.
    containers = client.containers.list(
        all=True, filters={"label": f"tailbale.service_id={service_id}"}
    )
    return containers[0] if isinstance(containers, list) and containers else None


def _find_edge_container(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
    client: docker.DockerClient | None = None,
) -> docker.models.containers.Container | None:
    """Find existing edge container by name or service_id label."""
    if client is not None:
        return find_edge_container(client, service_id, edge_container_name)
    with docker_client(socket_path) as client:
        return find_edge_container(client, service_id, edge_container_name)


def _find_edge_container_for_use(
    service_id: str,
    edge_container_name: str,
    socket_path: str | None = None,
) -> tuple[docker.DockerClient | None, docker.models.containers.Container | None]:
    """Find an edge container and keep its Docker client open for follow-up calls."""
    client = connect(socket_path)
    try:
        return client, _find_edge_container(service_id, edge_container_name, socket_path, client)
    except Exception:
        close_client(client)
        raise


@contextmanager
def edge_container(
    service_id: str, edge_container_name: str, socket_path: str | None = None
) -> Iterator[
    tuple[docker.DockerClient | None, docker.models.containers.Container | None]
]:
    """Yield ``(client, container)`` for an edge container, closing the client on exit.

    Mirrors :func:`app.edge.docker_client.docker_client`: it opens a client via
    :func:`_find_edge_container_for_use`, yields it alongside the located
    container (which may be ``None`` when absent), and guarantees
    :func:`close_client` even when the body raises. This is the single
    client-lifecycle primitive shared by the edge lifecycle, Tailscale, and
    Caddy-admin helpers.
    """
    client, container = _find_edge_container_for_use(
        service_id, edge_container_name, socket_path
    )
    try:
        yield client, container
    finally:
        close_client(client)


def _wait_for_running(
    container: docker.models.containers.Container,
    timeout: float = 30.0,
    poll_interval: float = 1.0,
) -> bool:
    """Wait until a container reaches the 'running' state.

    Docker rejects ``exec`` calls when a container is restarting or paused.
    Returns True once the container reaches 'running', or False if it enters a
    terminal state (exited/dead/removing) or the timeout elapses first.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        container.reload()  # refresh attrs from daemon
        if container.status == "running":
            return True
        if container.status in ("exited", "dead", "removing"):
            logger.warning("Container %s is %s — not waiting further", container.name, container.status)
            return False
        time.sleep(poll_interval)
    logger.warning("Timed out waiting for container %s to be running (status=%s)", container.name, container.status)
    return False
