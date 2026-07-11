"""Service CRUD API endpoints.

CRUD handlers here parse requests, keep upstream validation at the historical
``app.routers.services._validate_upstream`` patch target, and delegate lifecycle
orchestration to :mod:`app.services`. Edge, certificate, reconcile, and other
service action endpoints live in :mod:`app.routers.service_actions`.
"""

from fastapi import APIRouter, BackgroundTasks, Depends
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
from app.services import diagnostics
from app.services.errors import HostnameSuffixInvalid, ServiceNotFound
from app.settings_store import get_setting

router = APIRouter(
    prefix="/api/services",
    tags=["services"],
    dependencies=[Depends(get_current_user)],
)


def _validate_upstream(db: Session, container_id: str, port: int) -> None:
    """Validate the upstream container/port before the lifecycle lock.

    Thin seam kept at the historical ``app.routers.services._validate_upstream``
    patch target (the autouse test fixture and CRUD tests patch it here); the
    Docker round-trip, port inspection, and error mapping live in the intentful
    op :func:`app.services.diagnostics.validate_upstream_container_port` (AR15).
    """
    diagnostics.validate_upstream_container_port(db, container_id, port)


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
    # Compare case-insensitively: the hostname is lowercased by the schema, but a
    # legacy/direct-DB base_domain can hold a mixed-case value (the API validator
    # only lowercases on write). A raw endswith would then reject a valid
    # subdomain — settings._reject_base_domain_change_with_services guards the
    # same legacy case the same way.
    if not configured_domain or not body.hostname.endswith(f".{configured_domain.lower()}"):
        raise HostnameSuffixInvalid(body.hostname, configured_domain)

    # Validate upstream container + port (spec §17 steps 2-3) — hard failure,
    # BEFORE service_layer.create_service takes the lifecycle lock. The router
    # keeps the _validate_upstream seam (delegating to diagnostics) so the
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
