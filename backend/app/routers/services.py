"""Service CRUD API endpoints."""

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.schemas.services import (
    ServiceCreate,
    ServiceListResponse,
    ServiceResponse,
    ServiceStatusResponse,
    ServiceUpdate,
)

router = APIRouter(
    prefix="/api/services",
    tags=["services"],
    dependencies=[Depends(get_current_user)],
)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "service"


def _unique_slug(db: Session, name: str) -> str:
    """Return a slug derived from *name* that doesn't collide with existing edge names."""
    base = _slugify(name)
    slug = base
    suffix = 2
    while (
        db.query(Service)
        .filter(
            (Service.edge_container_name == f"edge_{slug}")
            | (Service.network_name == f"edge_net_{slug}")
            | (Service.ts_hostname == f"edge-{slug}")
        )
        .first()
    ):
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def _to_response(
    svc: Service,
    status: ServiceStatus | None,
    cert: Certificate | None = None,
) -> ServiceResponse:
    status_resp = None
    if status:
        health_checks = None
        if status.health_checks:
            try:
                health_checks = json.loads(status.health_checks)
            except (json.JSONDecodeError, TypeError):
                pass
        status_resp = ServiceStatusResponse(
            phase=status.phase,
            message=status.message,
            tailscale_ip=status.tailscale_ip,
            edge_container_id=status.edge_container_id,
            last_reconciled_at=status.last_reconciled_at.isoformat() if status.last_reconciled_at else None,
            health_checks=health_checks,
            cert_expires_at=cert.expires_at.isoformat() if cert and cert.expires_at else None,
        )
    return ServiceResponse(
        id=svc.id,
        name=svc.name,
        enabled=svc.enabled,
        upstream_container_id=svc.upstream_container_id,
        upstream_container_name=svc.upstream_container_name,
        upstream_scheme=svc.upstream_scheme,
        upstream_port=svc.upstream_port,
        healthcheck_path=svc.healthcheck_path,
        hostname=svc.hostname,
        base_domain=svc.base_domain,
        edge_container_name=svc.edge_container_name,
        network_name=svc.network_name,
        ts_hostname=svc.ts_hostname,
        preserve_host_header=svc.preserve_host_header,
        custom_caddy_snippet=svc.custom_caddy_snippet,
        app_profile=svc.app_profile,
        status=status_resp,
        created_at=svc.created_at.isoformat(),
        updated_at=svc.updated_at.isoformat(),
    )


def _emit_event(db: Session, service_id: str | None, kind: str, message: str, details: dict | None = None):
    event = Event(
        service_id=service_id,
        kind=kind,
        level="info",
        message=message,
        details=json.dumps(details) if details else None,
    )
    db.add(event)


@router.get("", response_model=ServiceListResponse)
async def list_services(db: Session = Depends(get_db)):
    """List all service exposures."""
    services = db.query(Service).order_by(Service.created_at.desc()).all()
    result = []
    for svc in services:
        status = db.get(ServiceStatus, svc.id)
        cert = db.get(Certificate, svc.id)
        result.append(_to_response(svc, status, cert))
    return ServiceListResponse(services=result, total=len(result))


@router.post("", response_model=ServiceResponse, status_code=201)
async def create_service(body: ServiceCreate, db: Session = Depends(get_db)):
    """Create a new service exposure."""
    # Check hostname uniqueness
    existing = db.query(Service).filter(Service.hostname == body.hostname).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Hostname '{body.hostname}' is already in use")

    slug = _unique_slug(db, body.name)
    svc = Service(
        name=body.name,
        enabled=body.enabled,
        upstream_container_id=body.upstream_container_id,
        upstream_container_name=body.upstream_container_name,
        upstream_scheme=body.upstream_scheme,
        upstream_port=body.upstream_port,
        healthcheck_path=body.healthcheck_path,
        hostname=body.hostname,
        base_domain=body.base_domain,
        edge_container_name=f"edge_{slug}",
        network_name=f"edge_net_{slug}",
        ts_hostname=f"edge-{slug}",
        preserve_host_header=body.preserve_host_header,
        custom_caddy_snippet=body.custom_caddy_snippet,
        app_profile=body.app_profile,
    )
    db.add(svc)
    db.flush()  # Generate ID

    # Create initial status
    status = ServiceStatus(service_id=svc.id, phase="pending", message="Awaiting first reconciliation")
    db.add(status)

    _emit_event(db, svc.id, "service_created", f"Service '{svc.name}' created for {svc.hostname}")

    db.commit()
    db.refresh(svc)
    db.refresh(status)

    return _to_response(svc, status)


@router.get("/{service_id}", response_model=ServiceResponse)
async def get_service(service_id: str, db: Session = Depends(get_db)):
    """Get a single service by ID."""
    svc = db.get(Service, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    status = db.get(ServiceStatus, service_id)
    cert = db.get(Certificate, service_id)
    return _to_response(svc, status, cert)


@router.put("/{service_id}", response_model=ServiceResponse)
async def update_service(service_id: str, body: ServiceUpdate, db: Session = Depends(get_db)):
    """Update a service exposure."""
    svc = db.get(Service, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    changes: dict = {}

    sent = body.model_fields_set

    if "hostname" in sent and body.hostname != svc.hostname:
        existing = db.query(Service).filter(
            Service.hostname == body.hostname, Service.id != service_id
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Hostname '{body.hostname}' is already in use")
        changes["hostname"] = body.hostname
        svc.hostname = body.hostname

    for field in (
        "name", "upstream_scheme", "upstream_port", "healthcheck_path",
        "enabled", "preserve_host_header", "custom_caddy_snippet", "app_profile",
    ):
        if field in sent:
            val = getattr(body, field)
            changes[field] = val
            setattr(svc, field, val)

    if changes:
        _emit_event(db, svc.id, "service_updated", f"Service '{svc.name}' updated", details=changes)

    db.commit()
    db.refresh(svc)
    status = db.get(ServiceStatus, service_id)
    return _to_response(svc, status)


@router.post("/{service_id}/disable", response_model=ServiceResponse)
async def disable_service(service_id: str, db: Session = Depends(get_db)):
    """Disable a service without deleting it."""
    svc = db.get(Service, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    svc.enabled = False
    _emit_event(db, svc.id, "service_disabled", f"Service '{svc.name}' disabled")
    db.commit()
    db.refresh(svc)
    status = db.get(ServiceStatus, service_id)
    return _to_response(svc, status)


@router.delete("/{service_id}", status_code=204)
async def delete_service(
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
        raise HTTPException(status_code=404, detail="Service not found")

    # Optionally clean up DNS record in Cloudflare before deleting
    if cleanup_dns:
        from app.adapters.dns_reconciler import cleanup_dns_record
        from app.secrets import CLOUDFLARE_TOKEN, read_secret
        from app.settings_store import get_setting

        cf_token = read_secret(CLOUDFLARE_TOKEN)
        zone_id = get_setting(db, "cf_zone_id")
        if cf_token and zone_id:
            try:
                cleanup_dns_record(db, svc, cf_token, zone_id)
            except Exception:
                pass  # Best-effort; service deletion proceeds regardless

    name = svc.name
    _emit_event(db, None, "service_deleted", f"Service '{name}' ({service_id}) deleted")
    db.delete(svc)  # CASCADE deletes status, certs, dns records
    db.commit()


# --- Action endpoints ---


def _get_service_or_404(service_id: str, db: Session) -> Service:
    svc = db.get(Service, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    return svc


@router.post("/{service_id}/reload")
async def reload_service(service_id: str, db: Session = Depends(get_db)):
    """Reload Caddy config inside edge container."""
    from app.edge.container_manager import reload_caddy

    svc = _get_service_or_404(service_id, db)
    try:
        output = reload_caddy(svc.id, svc.edge_container_name)
        _emit_event(db, svc.id, "caddy_reloaded", f"Caddy reloaded for '{svc.name}'")
        db.commit()
        return {"success": True, "message": "Caddy config reloaded", "output": output}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from None


@router.post("/{service_id}/restart-edge")
async def restart_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Restart edge container."""
    from app.edge.container_manager import restart_edge

    svc = _get_service_or_404(service_id, db)
    try:
        restart_edge(svc.id, svc.edge_container_name)
        _emit_event(db, svc.id, "edge_restarted", f"Edge container restarted for '{svc.name}'")
        db.commit()
        return {"success": True, "message": "Edge container restarted"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from None


@router.post("/{service_id}/recreate-edge")
async def recreate_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Destroy and recreate edge container."""
    from app.edge.container_manager import recreate_edge as do_recreate
    from app.config import settings
    from app.secrets import read_secret, TAILSCALE_AUTH_KEY

    svc = _get_service_or_404(service_id, db)
    try:
        ts_authkey = read_secret(TAILSCALE_AUTH_KEY)
        if not ts_authkey:
            raise HTTPException(status_code=400, detail="Tailscale auth key not configured")

        container_id = do_recreate(
            svc, ts_authkey,
            settings.generated_dir, settings.certs_dir, settings.tailscale_state_dir,
        )

        # Update status with new container ID
        status = db.get(ServiceStatus, svc.id)
        if status:
            status.edge_container_id = container_id

        _emit_event(db, svc.id, "edge_recreated", f"Edge container recreated for '{svc.name}'")
        db.commit()
        return {"success": True, "message": "Edge container recreated", "container_id": container_id}
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from None


@router.post("/{service_id}/renew-cert")
async def renew_cert(service_id: str, db: Session = Depends(get_db)):
    """Force certificate renewal for a service."""
    from app.certs.renewal_task import process_service_cert

    svc = _get_service_or_404(service_id, db)
    try:
        process_service_cert(db, svc)
        cert = db.get(Certificate, svc.id)
        return {
            "success": True,
            "message": f"Certificate processed for {svc.hostname}",
            "expires_at": cert.expires_at.isoformat() if cert and cert.expires_at else None,
            "last_failure": cert.last_failure if cert else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from None


@router.get("/{service_id}/logs/cert")
async def get_cert_logs(service_id: str, limit: int = 50, db: Session = Depends(get_db)):
    """Get cert-related events for a service."""
    from app.models.event import Event

    _get_service_or_404(service_id, db)
    events = (
        db.query(Event)
        .filter(
            Event.service_id == service_id,
            Event.kind.in_(["cert_issued", "cert_renewed", "cert_failed"]),
        )
        .order_by(Event.created_at.desc())
        .limit(limit)
        .all()
    )
    return {
        "events": [
            {
                "id": evt.id,
                "kind": evt.kind,
                "level": evt.level,
                "message": evt.message,
                "created_at": evt.created_at.isoformat() if evt.created_at else None,
                "details": json.loads(evt.details) if evt.details else None,
            }
            for evt in events
        ]
    }


@router.post("/{service_id}/reconcile")
async def reconcile_service_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Trigger manual reconciliation for a single service."""
    import asyncio

    from app.reconciler.reconcile_loop import reconcile_one

    _get_service_or_404(service_id, db)
    try:
        result = await asyncio.to_thread(reconcile_one, db, service_id)
        return {"success": True, "phase": result["phase"], "error": result.get("error")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from None


@router.get("/{service_id}/logs/edge")
async def get_edge_logs(service_id: str, tail: int = 100, db: Session = Depends(get_db)):
    """Get edge container logs."""
    from app.edge.container_manager import get_edge_logs as fetch_logs

    svc = _get_service_or_404(service_id, db)
    logs = fetch_logs(svc.id, svc.edge_container_name, tail=tail)
    return {"logs": logs}
