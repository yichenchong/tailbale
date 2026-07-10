"""Service CRUD API endpoints.

CRUD handlers here parse requests, keep upstream validation at the historical
``app.routers.services._validate_upstream`` patch target, and delegate lifecycle
orchestration to :mod:`app.services`. Edge, certificate, reconcile, and other
service action endpoints live in :mod:`app.routers.service_actions`.
"""

import logging

import docker
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app import services as service_layer
from app.auth import get_current_user
from app.database import get_db
from app.models.certificate import Certificate
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.schemas.services import (
    ServiceCreate,
    ServiceListResponse,
    ServiceResponse,
    ServiceUpdate,
)
from app.services import docker_client, resolve_socket
from app.services.errors import DockerUnavailable, HostnameSuffixInvalid, ServiceNotFound
from app.settings_store import get_setting

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/services",
    tags=["services"],
    dependencies=[Depends(get_current_user)],
)


def _validate_upstream(db: Session, container_id: str, port: int) -> None:
    """Validate that the upstream container exists and the port is plausible.

    Raises HTTPException on invalid container/port input and DockerUnavailable
    when Docker is unreachable.
    """
    try:
        with docker_client(resolve_socket(db)) as client:
            upstream = client.containers.get(container_id)
            _validate_upstream_port(upstream, port)
    except docker.errors.NotFound as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Upstream container '{container_id}' not found",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Cannot connect to Docker to validate upstream container %s", container_id
        )
        raise DockerUnavailable(
            "Cannot connect to Docker to validate upstream container"
        ) from exc


def _validate_upstream_port(container, requested_port: int) -> None:
    """Check that *requested_port* is plausible for *container*.

    We inspect the container's exposed ports (from its image/config) and, if
    there are any explicit port definitions, verify the requested port is among
    them.  If the container has *no* exposed ports at all we let it through —
    the user may know better.
    """
    try:
        # container.attrs["Config"]["ExposedPorts"] → {"80/tcp": {}, "443/tcp": {}}
        exposed = container.attrs.get("Config", {}).get("ExposedPorts") or {}
        # Also check host-published ports under NetworkSettings
        port_bindings = (
            container.attrs.get("HostConfig", {}).get("PortBindings") or {}
        )
        # Merge both sets of known ports
        known_ports: set[int] = set()
        for spec in list(exposed.keys()) + list(port_bindings.keys()):
            try:
                known_ports.add(int(spec.split("/")[0]))
            except (ValueError, IndexError):
                continue

        if known_ports and requested_port not in known_ports:
            available = ", ".join(str(p) for p in sorted(known_ports))
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Port {requested_port} is not exposed by container "
                    f"'{container.name}'. Available ports: {available}"
                ),
            )
    except HTTPException:
        raise
    except Exception:
        pass  # If we can't inspect ports, allow the request through


@router.get("", response_model=ServiceListResponse)
def list_services(db: Session = Depends(get_db)):
    """List all service exposures."""
    services = db.query(Service).order_by(Service.created_at.desc(), Service.id.desc()).all()
    if not services:
        return ServiceListResponse(services=[], total=0)

    ids = [svc.id for svc in services]
    status_map = {
        s.service_id: s
        for s in db.query(ServiceStatus).filter(ServiceStatus.service_id.in_(ids)).all()
    }
    cert_map = {
        c.service_id: c
        for c in db.query(Certificate).filter(Certificate.service_id.in_(ids)).all()
    }
    result = [
        service_layer.to_response(svc, status_map.get(svc.id), cert_map.get(svc.id))
        for svc in services
    ]
    return ServiceListResponse(services=result, total=len(result))


@router.post("", response_model=ServiceResponse, status_code=201)
def create_service(body: ServiceCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create a new service exposure."""

    # The base domain is always the configured domain; the hostname must be a
    # subdomain of it. We derive base_domain server-side rather than trusting
    # the client so the persisted value always matches configuration.
    configured_domain = get_setting(db, "base_domain")
    if not configured_domain or not body.hostname.endswith(f".{configured_domain}"):
        raise HostnameSuffixInvalid(body.hostname, configured_domain)

    # Validate upstream container + port (spec §17 steps 2-3) — hard failure.
    # Kept in the router (not the service layer) so the test suite's
    # app.routers.services._validate_upstream patch still intercepts it.
    _validate_upstream(db, body.upstream_container_id, body.upstream_port)

    return service_layer.create_service(db, body, background_tasks, configured_domain)


@router.get("/{service_id}", response_model=ServiceResponse)
def get_service(service_id: str, db: Session = Depends(get_db)):
    """Get a single service by ID."""
    svc = db.get(Service, service_id)
    if not svc:
        raise ServiceNotFound()
    status = db.get(ServiceStatus, service_id)
    cert = db.get(Certificate, service_id)
    return service_layer.to_response(svc, status, cert)


@router.put("/{service_id}", response_model=ServiceResponse)
def update_service(service_id: str, body: ServiceUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Update a service exposure."""

    # Revalidate the upstream port BEFORE delegating. service_layer.update_service
    # takes the lifecycle/reconcile locks and performs the destructive hostname
    # teardown; _validate_upstream does a Docker round-trip, so holding those
    # locks across the network call would stall every other lifecycle op
    # (create_service deliberately validates BEFORE locking for the same reason).
    # Read the immutable upstream_container_id and the current port from a
    # pre-lock snapshot (mirroring create_service); the target-port-vs-container
    # check is independent of any concurrent row mutation because the container
    # id never changes. Validating here keeps the check ahead of the destructive
    # teardown AND keeps the app.routers.services._validate_upstream patch live.
    if "upstream_port" in body.model_fields_set:
        pre = db.get(Service, service_id)
        if pre is not None and body.upstream_port != pre.upstream_port:
            _validate_upstream(db, pre.upstream_container_id, body.upstream_port)

    return service_layer.update_service(db, service_id, body, background_tasks)


@router.post("/{service_id}/disable", response_model=ServiceResponse)
def disable_service(
    service_id: str,
    cleanup_dns: bool = False,
    db: Session = Depends(get_db),
):
    """Disable a service without deleting it.

    Query params:
        cleanup_dns: If true, also remove the Cloudflare DNS record so the
            hostname stops resolving to the (now-stopped) Tailscale IP.
    """
    return service_layer.disable_service(db, service_id, cleanup_dns=cleanup_dns)


@router.delete("/{service_id}", status_code=204)
def delete_service(
    service_id: str,
    cleanup_dns: bool = False,
    db: Session = Depends(get_db),
):
    """Delete a service exposure and associated records.

    Query params:
        cleanup_dns: If true, also delete the Cloudflare DNS record.
    """
    svc = db.get(Service, service_id)
    if not svc:
        raise ServiceNotFound()

    service_layer.delete_service_record(db, svc, cleanup_dns=cleanup_dns)
