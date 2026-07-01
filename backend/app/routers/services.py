"""Service CRUD API endpoints.

The HTTP handlers here are intentionally thin: they parse the request, run
upstream validation (kept here because the test suite patches
``app.routers.services._validate_upstream``), delegate the lifecycle
orchestration to :mod:`app.services.service_ops`, and map results / failures to
HTTP responses. The error-mapping shells (``edge_action`` / ``_run_edge_job``)
and their ``app.routers.services`` logger stay here so the no-leak tests keep
asserting against this module.
"""

import asyncio
import contextlib
import functools
import logging
from typing import NoReturn

import docker
import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import commit_with_lock, db_write_section, get_db
from app.edge.docker_client import docker_client, resolve_socket
from app.events.event_emitter import emit_event
from app.locks import service_reconcile_lock
from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.schemas.services import (
    ServiceCreate,
    ServiceListResponse,
    ServiceResponse,
    ServiceUpdate,
)
from app.services import service_ops
from app.services.errors import (
    HostnameSuffixInvalid,
    ServiceError,
    ServiceNotFound,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/services",
    tags=["services"],
    dependencies=[Depends(get_current_user)],
)


def _raise_edge_failure(exc: Exception, *, failure_detail: str) -> NoReturn:
    """Translate an edge-action failure into the canonical HTTP status.

    Single source for the mapping the sync :func:`edge_action` decorator and the
    async :func:`_run_edge_job` wrapper both historically hand-rolled as an
    identical four-arm ``except`` ladder:

    * :class:`~app.services.errors.ServiceError` — re-raised unchanged so the
      central ``@app.exception_handler`` maps the service layer's own
      404/409/400/... to its canonical status + detail;
    * ``HTTPException`` — re-raised unchanged (a handler's own mapped 4xx);
    * ``RuntimeError`` — the operation failed → 500 with *failure_detail*;
    * Docker daemon unreachable (``docker`` ``DockerException`` or ``requests``
      ``ConnectionError``) → 503 ``"Docker is unavailable"``;
    * anything else → 500 with *failure_detail*.

    The full exception (with traceback) is logged server-side via
    ``logger.exception``; only the generic *failure_detail* — never ``str(exc)``
    — reaches the client.
    """
    if isinstance(exc, (ServiceError, HTTPException)):
        raise exc
    if isinstance(exc, RuntimeError):
        logger.exception("Edge action failed: %s", failure_detail)
        raise HTTPException(status_code=500, detail=failure_detail) from None
    if isinstance(exc, (docker.errors.DockerException, requests.exceptions.ConnectionError)):
        logger.exception("Docker unavailable during edge action: %s", failure_detail)
        raise HTTPException(status_code=503, detail="Docker is unavailable") from None
    logger.exception("Unexpected error during edge action: %s", failure_detail)
    raise HTTPException(status_code=500, detail=failure_detail) from None


def edge_action(*, failure_detail: str):
    """Wrap a sync edge-action handler so failures go through the shared mapping.

    The reload / restart / recreate endpoints all touch the Docker daemon; this
    funnels any failure through :func:`_raise_edge_failure` so the four-arm
    mapping lives in exactly one place.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                _raise_edge_failure(exc, failure_detail=failure_detail)

        return wrapper

    return decorator


async def _run_edge_job(work, *, failure_detail: str):
    """Async counterpart to :func:`edge_action` for endpoints that offload their
    Docker work to a worker thread.

    Awaits ``asyncio.to_thread(work)`` and funnels failures through the SAME
    :func:`_raise_edge_failure` mapping, keeping the async ``/reconcile`` and
    ``/update-edge`` endpoints consistent with the sync reload / restart /
    recreate endpoints (a Docker-unavailable failure → 503, not a generic 500).
    """
    try:
        return await asyncio.to_thread(work)
    except Exception as exc:
        _raise_edge_failure(exc, failure_detail=failure_detail)


def _validate_upstream(db: Session, container_id: str, port: int) -> None:
    """Validate that the upstream container exists and the port is plausible.

    Raises HTTPException on failure (422 if container/port invalid, 503 if
    Docker is unreachable).
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
        raise HTTPException(
            status_code=503,
            detail="Cannot connect to Docker to validate upstream container",
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
        service_ops.to_response(svc, status_map.get(svc.id), cert_map.get(svc.id))
        for svc in services
    ]
    return ServiceListResponse(services=result, total=len(result))


@router.post("", response_model=ServiceResponse, status_code=201)
def create_service(body: ServiceCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create a new service exposure."""

    # The base domain is always the configured domain; the hostname must be a
    # subdomain of it. We derive base_domain server-side rather than trusting
    # the client so the persisted value always matches configuration.
    from app.settings_store import get_setting
    configured_domain = get_setting(db, "base_domain")
    if not configured_domain or not body.hostname.endswith(f".{configured_domain}"):
        raise HostnameSuffixInvalid(body.hostname, configured_domain)

    # Validate upstream container + port (spec §17 steps 2-3) — hard failure.
    # Kept in the router (not service_ops) so the test suite's
    # app.routers.services._validate_upstream patch still intercepts it.
    _validate_upstream(db, body.upstream_container_id, body.upstream_port)

    return service_ops.create_service(db, body, background_tasks, configured_domain)


@router.get("/{service_id}", response_model=ServiceResponse)
def get_service(service_id: str, db: Session = Depends(get_db)):
    """Get a single service by ID."""
    svc = db.get(Service, service_id)
    if not svc:
        raise ServiceNotFound()
    status = db.get(ServiceStatus, service_id)
    cert = db.get(Certificate, service_id)
    return service_ops.to_response(svc, status, cert)


@router.put("/{service_id}", response_model=ServiceResponse)
def update_service(service_id: str, body: ServiceUpdate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Update a service exposure."""

    # Revalidate the upstream port BEFORE delegating. service_ops.update_service
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

    return service_ops.update_service(db, service_id, body, background_tasks)


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
    return service_ops.disable_service(db, service_id, cleanup_dns=cleanup_dns)


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

    service_ops.delete_service_record(db, svc, cleanup_dns=cleanup_dns)


# --- Action endpoints ---


def _get_service_or_404(service_id: str, db: Session) -> Service:
    svc = db.get(Service, service_id)
    if not svc:
        raise ServiceNotFound()
    return svc


@router.post("/{service_id}/reload")
@edge_action(failure_detail="Failed to reload Caddy config")
def reload_service(service_id: str, db: Session = Depends(get_db)):
    """Reload Caddy config inside edge container."""
    from app.edge.container_manager import reload_caddy

    with service_reconcile_lock(service_id):
        svc = service_ops.get_enabled_service_for_edge_action(service_id, db)
        output = reload_caddy(svc.id, svc.edge_container_name, resolve_socket(db))
        with db_write_section(db):
            emit_event(db, svc.id, "caddy_reloaded", f"Caddy reloaded for '{svc.name}'")
            commit_with_lock(db)
        return {"success": True, "message": "Caddy config reloaded", "output": output}


@router.post("/{service_id}/restart-edge")
@edge_action(failure_detail="Failed to restart edge container")
def restart_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Restart edge container."""
    from app.edge.container_manager import restart_edge

    with service_reconcile_lock(service_id):
        svc = service_ops.get_enabled_service_for_edge_action(service_id, db)
        restart_edge(svc.id, svc.edge_container_name, resolve_socket(db))
        with db_write_section(db):
            emit_event(db, svc.id, "edge_restarted", f"Edge container restarted for '{svc.name}'")
            commit_with_lock(db)
    return {"success": True, "message": "Edge container restarted"}


@router.post("/{service_id}/recreate-edge")
@edge_action(failure_detail="Failed to recreate edge container")
def recreate_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Destroy and recreate edge container."""
    return service_ops.recreate_edge(db, service_id)


@router.post("/{service_id}/renew-cert")
def renew_cert(
    service_id: str,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Manually issue or renew a service's certificate.

    Mirrors the background renewal loop's intent: when the cert is missing or
    near/at/past expiry it issues/renews immediately. When the cert is healthy
    and still far from expiry, renewing would contact Let's Encrypt for no real
    benefit and risks rate limits, so the request is refused (``performed:
    false``, ``needs_force: true``) and the caller must retry with
    ``?force=true``. With ``force=true`` a renewal always happens, bypassing the
    healthy-noop and the per-cert failure backoff. A DISABLED service is offline
    and its cert is not served, and ``process_service_cert`` skips disabled
    services outright — so a renewal is reported as not performed rather than
    silently no-opping while claiming success.
    """
    svc = _get_service_or_404(service_id, db)
    try:
        return service_ops.renew_cert(db, svc, force=force)
    except Exception:
        logger.exception("Failed to renew certificate for service %s", service_id)
        raise HTTPException(status_code=500, detail="Failed to renew certificate") from None


@router.get("/{service_id}/logs/cert")
def get_cert_logs(
    service_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get cert-related events for a service."""
    _get_service_or_404(service_id, db)
    events = (
        db.query(Event)
        .filter(
            Event.service_id == service_id,
            Event.kind.in_(["cert_issued", "cert_renewed", "cert_failed"]),
        )
        .order_by(Event.created_at.desc(), Event.id.desc())
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
                "details": evt.details,
            }
            for evt in events
        ]
    }


@router.post("/{service_id}/reconcile")
async def reconcile_service_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Trigger manual reconciliation for a single service."""
    from app.reconciler.reconcile_loop import spawn_reconcile

    _get_service_or_404(service_id, db)
    socket = resolve_socket(db)

    result = await _run_edge_job(
        functools.partial(spawn_reconcile, service_id, socket),
        failure_detail="Failed to reconcile service",
    )
    return {"success": True, "phase": result["phase"], "error": result.get("error")}


@router.get("/{service_id}/logs/edge")
@edge_action(failure_detail="Failed to fetch edge logs")
def get_edge_logs(
    service_id: str,
    tail: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """Get edge container logs."""
    from app.edge.container_manager import get_edge_logs as fetch_logs

    svc = _get_service_or_404(service_id, db)
    logs = fetch_logs(
        svc.id, svc.edge_container_name, tail=tail, socket_path=resolve_socket(db)
    )
    return {"logs": logs}


@router.post("/{service_id}/health-check-full")
def full_health_check(service_id: str, db: Session = Depends(get_db)):
    """Run an extensive health check including live Cloudflare DNS verification.

    This is manual-only (not part of the reconcile loop) to avoid API rate limits.
    """
    from app.health.health_checker import run_health_checks
    from app.secrets import CLOUDFLARE_TOKEN, read_secret
    from app.settings_store import get_runtime_paths, get_setting

    svc = _get_service_or_404(service_id, db)
    runtime = get_runtime_paths(db)

    # Run standard health checks. DNS uses DB state here; the single live
    # Cloudflare lookup below overrides it so we don't query Cloudflare twice.
    # resolve_socket mirrors the reconciler/probe socket resolution (configured
    # path or None -> from_env, honoring DOCKER_HOST), keeping full-health-check
    # pointed at the same daemon as the rest of the app.
    checks = run_health_checks(
        db, svc,
        runtime["generated_dir"], runtime["certs_dir"],
        resolve_socket(db),
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
            # Reflect the live DNS state in the standard checks using the single
            # lookup above (this is the manual endpoint's live-verification path).
            checks["dns_record_present"] = live_record is not None
            checks["dns_matches_ip"] = bool(
                live_record and current_ip and live_record.get("content") == current_ip
            )
        except Exception as e:
            logger.exception(
                "Live Cloudflare DNS verification failed for service %s", service_id
            )
            extended["cf_error"] = f"Cloudflare verification failed ({type(e).__name__})"
    else:
        extended["cf_error"] = "Cloudflare token or zone ID not configured"

    return {
        "checks": checks,
        "extended": extended,
        "tailscale_ip": current_ip,
    }


@router.get("/{service_id}/edge-version")
def get_edge_version_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Check the version of a service's edge container vs the orchestrator version."""
    from app.edge.container_manager import get_edge_version
    from app.version import __version__

    svc = _get_service_or_404(service_id, db)
    edge_version = None
    with contextlib.suppress(Exception):
        edge_version = get_edge_version(svc.id, svc.edge_container_name, resolve_socket(db))

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
    # Guard on the event loop (raises 404/409) before spawning the worker thread.
    service_ops.get_enabled_service_for_edge_action(service_id, db)
    socket = resolve_socket(db)

    return await _run_edge_job(
        functools.partial(service_ops.update_edge_job, service_id, socket),
        failure_detail="Failed to update edge container",
    )
