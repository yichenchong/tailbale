"""Docker container discovery API."""

import contextlib

import docker
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.schemas.services import (
    ContainerPortInfo,
    DiscoveredContainer,
    DiscoveryResponse,
)
from app.settings_store import get_setting

router = APIRouter(
    prefix="/api/discovery",
    tags=["discovery"],
    dependencies=[Depends(get_current_user)],
)

# Labels used to identify orchestrator-managed containers
MANAGED_LABELS = {"tailbale.managed": "true"}


def _parse_ports(container) -> list[ContainerPortInfo]:
    """Extract port mappings from a Docker container."""
    ports: list[ContainerPortInfo] = []
    port_data = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
    exposed_ports = container.attrs.get("Config", {}).get("ExposedPorts") or {}
    port_specs = dict.fromkeys(exposed_ports, None)
    port_specs.update(port_data)
    for container_port, bindings in port_specs.items():
        # container_port looks like "80/tcp"
        port_num, _, proto = container_port.partition("/")
        host_port = None
        for binding in bindings or ():
            host_port = binding.get("HostPort")
            if host_port:
                break
        ports.append(ContainerPortInfo(
            container_port=port_num,
            host_port=host_port,
            protocol=proto or "tcp",
        ))
    return ports


def _parse_networks(container) -> list[str]:
    """Extract network names from a Docker container."""
    networks = container.attrs.get("NetworkSettings", {}).get("Networks") or {}
    return list(networks.keys())


def _is_managed(container) -> bool:
    """Check if a container is managed by the orchestrator."""
    labels = container.labels or {}
    return labels.get("tailbale.managed") == "true"


@router.get("/containers", response_model=DiscoveryResponse)
async def list_containers(
    running_only: bool = Query(default=True),
    hide_managed: bool = Query(default=True),
    search: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """List Docker containers available for exposure."""
    socket_path = get_setting(db, "docker_socket_path")
    search_lower = search.strip().lower()
    client = None
    try:
        client = docker.DockerClient(base_url=socket_path)
        containers = client.containers.list(all=not running_only)
        result: list[DiscoveredContainer] = []
        for c in containers:
            if hide_managed and _is_managed(c):
                continue

            name = c.name or ""
            image = c.attrs.get("Config", {}).get("Image") or "unknown"

            if search_lower and search_lower not in name.lower() and search_lower not in image.lower():
                continue

            result.append(DiscoveredContainer(
                id=c.id,
                name=name,
                image=image,
                status=c.status,
                state=c.attrs.get("State", {}).get("Status", c.status),
                ports=_parse_ports(c),
                networks=_parse_networks(c),
                labels=c.labels or {},
            ))
        return DiscoveryResponse(containers=result, total=len(result))
    except docker.errors.DockerException:
        return DiscoveryResponse(containers=[], total=0)
    finally:
        if client is not None:
            close = getattr(client, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()
