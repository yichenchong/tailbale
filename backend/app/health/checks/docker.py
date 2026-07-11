"""Docker-backed subchecks: upstream container/network and edge container (AR18).

``find_edge_container`` is imported here (the module that now defines the edge
lookup) so the transient-daemon-fault tolerance and label-search recovery stay
with the checks that use it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import docker

from app.edge.container_session import find_edge_container

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)


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
        container = find_edge_container(
            client, service.id, service.edge_container_name, tolerate_lookup_errors=True
        )
        if container is None:
            return False, False
        return True, container.status == "running"
    except Exception:
        logger.info("Edge container health lookup failed for %s", service.id, exc_info=True)
        return False, False
