"""Service action API endpoints.

This router owns non-CRUD operations for a service: edge container actions,
manual reconciliation/update, cert actions, and action-specific log endpoints.
CRUD endpoints and upstream validation remain in :mod:`app.routers.services` so
legacy ``app.routers.services._validate_upstream`` patch targets stay live.
"""

import asyncio
import functools
import logging
from typing import NoReturn

import docker
import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import services as service_layer
from app.auth import get_current_user
from app.database import get_db
from app.events.querying import query_events
from app.events.serialization import event_to_dict
from app.models.service import Service
from app.reconciler import reconcile_loop
from app.services import resolve_socket
from app.services.errors import DockerUnavailable, ServiceError, ServiceNotFound

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/services",
    tags=["services"],
    dependencies=[Depends(get_current_user)],
)


def _raise_edge_failure(exc: Exception, *, failure_detail: str) -> NoReturn:
    """Translate an edge-action failure into the canonical HTTP status.

    Service-layer domain errors and deliberate HTTP exceptions pass through.
    Runtime failures become generic 500 service errors, and Docker transport
    failures become ``DockerUnavailable`` so the central service-error handler
    emits the canonical response without leaking socket paths or exception text.
    """
    if isinstance(exc, (ServiceError, HTTPException)):
        raise exc
    if isinstance(exc, RuntimeError):
        logger.exception("Edge action failed: %s", failure_detail)
        raise ServiceError(failure_detail, status_code=500) from None
    if isinstance(
        exc, (docker.errors.DockerException, requests.exceptions.ConnectionError)
    ):
        logger.exception("Docker unavailable during edge action: %s", failure_detail)
        raise DockerUnavailable() from None
    logger.exception("Unexpected error during edge action: %s", failure_detail)
    raise ServiceError(failure_detail, status_code=500) from None


def edge_action(*, failure_detail: str):
    """Wrap a sync edge-action handler so failures use the shared mapping."""

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
    """Run blocking edge work in a thread and map failures consistently."""
    try:
        return await asyncio.to_thread(work)
    except Exception as exc:
        _raise_edge_failure(exc, failure_detail=failure_detail)


def _get_service_or_404(service_id: str, db: Session) -> Service:
    svc = db.get(Service, service_id)
    if not svc:
        raise ServiceNotFound()
    return svc


@router.post("/{service_id}/reload")
@edge_action(failure_detail="Failed to reload Caddy config")
def reload_service(service_id: str, db: Session = Depends(get_db)):
    """Reload Caddy config inside edge container."""
    return service_layer.reload_caddy_action(db, service_id)


@router.post("/{service_id}/restart-edge")
@edge_action(failure_detail="Failed to restart edge container")
def restart_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Restart edge container."""
    return service_layer.restart_edge_action(db, service_id)


@router.post("/{service_id}/recreate-edge")
@edge_action(failure_detail="Failed to recreate edge container")
def recreate_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Destroy and recreate edge container."""
    return service_layer.recreate_edge(db, service_id)


@router.post("/{service_id}/renew-cert")
def renew_cert(
    service_id: str,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Manually issue or renew a service's certificate."""
    svc = _get_service_or_404(service_id, db)
    try:
        return service_layer.renew_cert(db, svc, force=force)
    except ServiceError:
        raise
    except Exception:
        logger.exception("Failed to renew certificate for service %s", service_id)
        raise ServiceError("Failed to renew certificate", status_code=500) from None


@router.get("/{service_id}/logs/cert")
def get_cert_logs(
    service_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Get cert-related events for a service."""
    _get_service_or_404(service_id, db)
    events, _ = query_events(
        db,
        service_id=service_id,
        kinds=("cert_issued", "cert_renewed", "cert_failed"),
        limit=limit,
        include_total=False,
    )
    return {
        "events": [
            event_to_dict(
                evt, fields=("id", "kind", "level", "message", "created_at", "details")
            )
            for evt in events
        ]
    }


@router.post("/{service_id}/reconcile")
async def reconcile_service_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Trigger manual reconciliation for a single service."""

    _get_service_or_404(service_id, db)
    socket = resolve_socket(db)

    result = await _run_edge_job(
        functools.partial(reconcile_loop.spawn_reconcile, service_id, socket),
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

    return service_layer.get_edge_logs(db, service_id, tail)


@router.post("/{service_id}/health-check-full")
def full_health_check(service_id: str, db: Session = Depends(get_db)):
    """Run an extensive health check including live Cloudflare DNS verification."""

    return service_layer.full_health_check(db, service_id)


@router.get("/{service_id}/edge-version")
def get_edge_version_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Check the version of a service's edge container vs the orchestrator version."""

    return service_layer.get_edge_version(db, service_id)


@router.post("/{service_id}/update-edge")
async def update_edge_endpoint(service_id: str, db: Session = Depends(get_db)):
    """Recreate an edge container with the current orchestrator version."""
    # Guard on the event loop (raises 404/409) before spawning the worker thread.
    service_layer.get_enabled_service_for_edge_action(service_id, db)
    socket = resolve_socket(db)

    return await _run_edge_job(
        functools.partial(service_layer.update_edge_job, service_id, socket),
        failure_detail="Failed to update edge container",
    )
