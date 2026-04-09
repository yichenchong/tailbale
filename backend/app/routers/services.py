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


def _validate_upstream(db: Session, container_id: str, port: int) -> None:
    """Validate that the upstream container exists and the port is plausible.

    Raises HTTPException on failure (422 if container/port invalid, 503 if
    Docker is unreachable).
    """
    import docker as docker_lib

    socket = _get_docker_socket(db)
    try:
        client = (
            docker_lib.DockerClient(base_url=socket)
            if socket
            else docker_lib.DockerClient.from_env()
        )
        upstream = client.containers.get(container_id)
    except docker_lib.errors.NotFound:
        raise HTTPException(
            status_code=422,
            detail=f"Upstream container '{container_id}' not found",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot connect to Docker to validate upstream container: {exc}",
        )

    _validate_upstream_port(upstream, port)


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

    # Validate hostname belongs to configured base domain
    from app.settings_store import get_setting
    configured_domain = get_setting(db, "base_domain")
    if configured_domain and not body.hostname.endswith(f".{configured_domain}"):
        raise HTTPException(
            status_code=422,
            detail=f"Hostname '{body.hostname}' must end with '.{configured_domain}'",
        )

    # Validate upstream container + port (spec §17 steps 2-3) — hard failure
    _validate_upstream(db, body.upstream_container_id, body.upstream_port)

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
        # Validate hostname belongs to configured base domain
        from app.settings_store import get_setting
        configured_domain = get_setting(db, "base_domain")
        if configured_domain and not body.hostname.endswith(f".{configured_domain}"):
            raise HTTPException(
                status_code=422,
                detail=f"Hostname '{body.hostname}' must end with '.{configured_domain}'",
            )

        old_hostname = svc.hostname

        # Clean up old Cloudflare DNS record (best-effort) before losing the handle
        from app.adapters.dns_reconciler import cleanup_dns_record
        from app.secrets import CLOUDFLARE_TOKEN, read_secret
        cf_token = read_secret(CLOUDFLARE_TOKEN)
        zone_id = get_setting(db, "cf_zone_id")
        if cf_token and zone_id:
            try:
                cleanup_dns_record(db, svc, cf_token, zone_id)
            except Exception:
                pass  # Best-effort; proceed with hostname change

        # Update Certificate row hostname (if exists) so cert renewal targets new hostname
        cert = db.get(Certificate, svc.id)
        if cert:
            cert.hostname = body.hostname

        # Update DnsRecord hostname if the row survived cleanup (e.g. CF was unreachable)
        from app.models.dns_record import DnsRecord
        dns_record = db.get(DnsRecord, svc.id)
        if dns_record:
            dns_record.hostname = body.hostname

        # Remove old cert files from disk
        import shutil
        from pathlib import Path
        from app.settings_store import get_runtime_paths
        runtime = get_runtime_paths(db)
        old_cert_dir = Path(runtime["certs_dir"]) / old_hostname
        if old_cert_dir.exists():
            shutil.rmtree(old_cert_dir, ignore_errors=True)

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
async def disable_service(
    service_id: str,
    cleanup_dns: bool = False,
    db: Session = Depends(get_db),
):
    """Disable a service without deleting it.

    Query params:
        cleanup_dns: If true, also remove the Cloudflare DNS record so the
            hostname stops resolving to the (now-stopped) Tailscale IP.
    """
    svc = db.get(Service, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    svc.enabled = False

    # Update status to "disabled" so the UI doesn't show stale "healthy"
    status = db.get(ServiceStatus, svc.id)
    if status:
        status.phase = "disabled"
        status.message = "Service disabled by user"
        status.health_checks = None  # Stale checks are misleading

    _emit_event(db, svc.id, "service_disabled", f"Service '{svc.name}' disabled")

    # Best-effort: stop the edge container so it stops serving traffic
    from app.edge.container_manager import stop_edge
    try:
        stop_edge(svc.id, svc.edge_container_name, _get_docker_socket(db))
    except Exception:
        pass  # Edge may not exist yet

    # Optionally clean up the DNS record (spec §7.4)
    if cleanup_dns:
        from app.adapters.dns_reconciler import cleanup_dns_record
        from app.secrets import CLOUDFLARE_TOKEN, read_secret
        from app.settings_store import get_setting

        cf_token = read_secret(CLOUDFLARE_TOKEN)
        zone_id = get_setting(db, "cf_zone_id")
        if cf_token and zone_id:
            try:
                cleanup_dns_record(db, svc, cf_token, zone_id)
                _emit_event(db, svc.id, "dns_removed", f"DNS record removed on disable for '{svc.name}'")
            except Exception:
                pass  # Best-effort

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

    # Best-effort: remove edge container and Docker network
    import shutil
    from pathlib import Path

    from app.edge.container_manager import remove_edge
    from app.edge.network_manager import remove_network
    from app.settings_store import get_runtime_paths

    socket = _get_docker_socket(db)
    try:
        remove_edge(svc.id, svc.edge_container_name, socket)
    except Exception:
        pass
    try:
        remove_network(svc.network_name, socket)
    except Exception:
        pass

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

    # Clean up generated configs, certs, and Tailscale state on disk
    runtime = get_runtime_paths(db)
    for subdir in [
        Path(runtime["generated_dir"]) / svc.id,
        Path(runtime["certs_dir"]) / svc.hostname,
        Path(runtime["tailscale_state_dir"]) / svc.edge_container_name,
    ]:
        if subdir.exists():
            shutil.rmtree(subdir, ignore_errors=True)

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


def _get_docker_socket(db: Session) -> str | None:
    """Read the user-configured Docker socket path from DB settings."""
    from app.settings_store import get_setting
    return get_setting(db, "docker_socket_path") or None


@router.post("/{service_id}/reload")
async def reload_service(service_id: str, db: Session = Depends(get_db)):
    """Reload Caddy config inside edge container."""
    from app.edge.container_manager import reload_caddy

    svc = _get_service_or_404(service_id, db)
    try:
        output = reload_caddy(svc.id, svc.edge_container_name, _get_docker_socket(db))
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
        restart_edge(svc.id, svc.edge_container_name, _get_docker_socket(db))
        _emit_event(db, svc.id, "edge_restarted", f"Edge container restarted for '{svc.name}'")
        db.commit()
        return {"success": True, "message": "Edge container restarted"}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from None


@router.post("/{service_id}/recreate-edge")
async def recreate_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Destroy and recreate edge container."""
    from app.edge.container_manager import recreate_edge as do_recreate
    from app.secrets import TAILSCALE_AUTH_KEY, read_secret
    from app.settings_store import get_runtime_paths

    svc = _get_service_or_404(service_id, db)
    try:
        ts_authkey = read_secret(TAILSCALE_AUTH_KEY)
        if not ts_authkey:
            raise HTTPException(status_code=400, detail="Tailscale auth key not configured")

        runtime = get_runtime_paths(db)
        container_id = do_recreate(
            svc, ts_authkey,
            runtime["generated_dir"], runtime["certs_dir"], runtime["tailscale_state_dir"],
            _get_docker_socket(db),
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

    from app.database import SessionLocal
    from app.reconciler.reconcile_loop import reconcile_one

    _get_service_or_404(service_id, db)
    socket = _get_docker_socket(db)

    def _run() -> dict:
        thread_db = SessionLocal()
        try:
            return reconcile_one(thread_db, service_id, socket_path=socket)
        finally:
            thread_db.close()

    try:
        result = await asyncio.to_thread(_run)
        return {"success": True, "phase": result["phase"], "error": result.get("error")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from None


@router.get("/{service_id}/logs/edge")
async def get_edge_logs(service_id: str, tail: int = 100, db: Session = Depends(get_db)):
    """Get edge container logs."""
    from app.edge.container_manager import get_edge_logs as fetch_logs

    svc = _get_service_or_404(service_id, db)
    logs = fetch_logs(svc.id, svc.edge_container_name, tail=tail, socket_path=_get_docker_socket(db))
    return {"logs": logs}


@router.post("/{service_id}/health-check-full")
async def full_health_check(service_id: str, db: Session = Depends(get_db)):
    """Run an extensive health check including live Cloudflare DNS verification.

    This is manual-only (not part of the reconcile loop) to avoid API rate limits.
    """
    from app.health.health_checker import run_health_checks
    from app.secrets import CLOUDFLARE_TOKEN, read_secret
    from app.settings_store import get_runtime_paths, get_setting

    svc = _get_service_or_404(service_id, db)
    runtime = get_runtime_paths(db)

    # Run standard health checks
    checks = run_health_checks(
        db, svc,
        runtime["generated_dir"], runtime["certs_dir"],
        runtime.get("docker_socket"),
    )

    # Extended: live Cloudflare DNS verification
    extended: dict[str, object] = {}
    cf_token = read_secret(CLOUDFLARE_TOKEN)
    zone_id = get_setting(db, "cf_zone_id")
    status = db.get(ServiceStatus, svc.id)
    current_ip = status.tailscale_ip if status else None

    if cf_token and zone_id:
        try:
            from app.adapters.cloudflare_adapter import find_record
            live_record = find_record(cf_token, zone_id, svc.hostname, "A")
            extended["cf_record_exists"] = live_record is not None
            if live_record:
                extended["cf_record_ip"] = live_record.get("content")
                extended["cf_ip_matches_tailscale"] = live_record.get("content") == current_ip
            else:
                extended["cf_record_ip"] = None
                extended["cf_ip_matches_tailscale"] = False
        except Exception as e:
            extended["cf_error"] = str(e)
    else:
        extended["cf_error"] = "Cloudflare token or zone ID not configured"

    return {
        "checks": checks,
        "extended": extended,
        "tailscale_ip": current_ip,
    }


@router.get("/{service_id}/edge-version")
async def get_edge_version_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Check the version of a service's edge container vs the orchestrator version."""
    from app.edge.container_manager import get_edge_version
    from app.version import __version__

    svc = _get_service_or_404(service_id, db)
    edge_version = None
    try:
        edge_version = get_edge_version(svc.id, svc.edge_container_name, _get_docker_socket(db))
    except Exception:
        pass

    return {
        "orchestrator_version": __version__,
        "edge_version": edge_version,
        "up_to_date": edge_version == __version__,
    }


@router.post("/{service_id}/update-edge")
async def update_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Recreate an edge container with the current orchestrator version.

    This rebuilds the edge image if needed and recreates the container,
    stamping it with the current version label.
    """
    import asyncio

    from app.database import SessionLocal
    from app.edge.container_manager import get_edge_version, recreate_edge
    from app.edge.image_builder import ensure_edge_image
    from app.secrets import TAILSCALE_AUTH_KEY, read_secret
    from app.settings_store import get_runtime_paths
    from app.version import __version__

    svc = _get_service_or_404(service_id, db)
    socket = _get_docker_socket(db)

    # Pre-check: already up to date?
    try:
        current = get_edge_version(svc.id, svc.edge_container_name, socket)
        if current == __version__:
            return {
                "success": True,
                "message": f"Edge container already at version {__version__}",
                "version": __version__,
            }
    except Exception:
        pass

    def _run() -> str:
        thread_db = SessionLocal()
        try:
            ts_authkey = read_secret(TAILSCALE_AUTH_KEY)
            if not ts_authkey:
                raise RuntimeError("Tailscale auth key not configured")

            runtime = get_runtime_paths(thread_db)
            ensure_edge_image(socket)
            container_id = recreate_edge(
                svc, ts_authkey,
                runtime["generated_dir"], runtime["certs_dir"],
                runtime["tailscale_state_dir"],
                socket,
            )

            # Update status
            status = thread_db.get(ServiceStatus, svc.id)
            if status:
                status.edge_container_id = container_id
            _emit_event(
                thread_db, svc.id, "edge_updated",
                f"Edge container updated to v{__version__} for '{svc.name}'",
            )
            thread_db.commit()
            return container_id
        finally:
            thread_db.close()

    try:
        container_id = await asyncio.to_thread(_run)
        return {
            "success": True,
            "message": f"Edge container updated to version {__version__}",
            "version": __version__,
            "container_id": container_id,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from None
