"""Docker container discovery API."""

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
    for container_port, bindings in port_data.items():
        # container_port looks like "80/tcp"
        parts = container_port.split("/")
        port_num = parts[0]
        proto = parts[1] if len(parts) > 1 else "tcp"
        host_port = None
        if bindings:
            host_port = bindings[0].get("HostPort")
        ports.append(ContainerPortInfo(
            container_port=port_num,
            host_port=host_port,
            protocol=proto,
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
    try:
        client = docker.DockerClient(base_url=socket_path)
        containers = client.containers.list(all=not running_only)
    except Exception:
        return DiscoveryResponse(containers=[], total=0)

    result: list[DiscoveredContainer] = []
    for c in containers:
        if hide_managed and _is_managed(c):
            continue

        name = c.name or ""
        image = (c.image.tags[0] if c.image and c.image.tags else
                 c.attrs.get("Config", {}).get("Image", "unknown"))

        if search:
            search_lower = search.lower()
            if search_lower not in name.lower() and search_lower not in image.lower():
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
