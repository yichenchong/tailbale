"""Reconciler engine — converges a service's observed state toward its desired state.

Each ``reconcile_service()`` call is **idempotent**: running it twice in a
row without external changes produces the same result.  The reconciler
follows the 14-step sequence from the spec (section 11.3).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.database import commit_with_lock, db_write_section, rollback_with_lock
from app.events.event_emitter import emit_event
from app.locks import forget_reconcile_lock, service_reconcile_lock
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler import steps

logger = logging.getLogger(__name__)


_UNSET = object()


class ReconcileError(Exception):
    """Raised when reconciliation hits a non-recoverable failure."""


def _load_or_create_status(db: Session, service_id: str) -> ServiceStatus:
    status = db.get(ServiceStatus, service_id)
    if status is None:
        status = ServiceStatus(service_id=service_id, phase="pending")
        db.add(status)
    return status


def _persist_status(
    db: Session,
    service_id: str,
    *,
    phase: str | object = _UNSET,
    message: str | None | object = _UNSET,
    edge_container_id: str | None | object = _UNSET,
    tailscale_ip: str | None | object = _UNSET,
    health_checks: dict | object = _UNSET,
    last_probe_at: datetime | None | object = _UNSET,
    last_reconciled_at: datetime | None | object = _UNSET,
    event: dict | None = None,
) -> None:
    with service_reconcile_lock(service_id), db_write_section(db):
        status = _load_or_create_status(db, service_id)
        if phase is not _UNSET:
            status.phase = phase
        if message is not _UNSET:
            status.message = message
        if edge_container_id is not _UNSET:
            status.edge_container_id = edge_container_id
        if tailscale_ip is not _UNSET:
            status.tailscale_ip = tailscale_ip
        if health_checks is not _UNSET:
            status.health_checks = health_checks
        if last_probe_at is not _UNSET:
            status.last_probe_at = last_probe_at
        if last_reconciled_at is not _UNSET:
            status.last_reconciled_at = last_reconciled_at
        if event is not None:
            emit_event(
                db,
                event.get("service_id", service_id),
                event["kind"],
                event["message"],
                level=event.get("level", "info"),
                details=event.get("details"),
            )
        commit_with_lock(db)


def _update_phase(db: Session, service_id: str, phase: str, message: str | None = None) -> None:
    _persist_status(db, service_id, phase=phase, message=message)


def reconcile_service(
    db: Session,
    service: Service,
    *,
    socket_path: str | None = None,
) -> dict:
    """Run the full reconciliation loop for a single service.

    Returns a summary dict with keys:
      phase, tailscale_ip, health_checks, caddy_reloaded, error
    """

    result = {
        "phase": "pending",
        "tailscale_ip": None,
        "health_checks": {},
        "caddy_reloaded": False,
        "error": None,
    }

    with service_reconcile_lock(service.id):
        return _reconcile_service_locked(db, service, socket_path=socket_path, result=result)


def _reconcile_service_locked(
    db: Session,
    service: Service,
    *,
    socket_path: str | None,
    result: dict,
) -> dict:
    """Orchestrate the reconcile while holding the per-service reconcile lock.

    Each cohesive step lives in its own helper in ``reconciler.steps``; this
    function wires them together in spec order, preserving the side effects,
    status/event writes, and partial-failure handling of the original inline
    loop.
    """
    service_id = service.id

    # Honor disable: never converge a service the operator turned off. Re-read
    # inside the reconcile mutex so a disable that committed while we waited is
    # respected — this covers both the manual /reconcile trigger (no enabled
    # filter) and a periodic sweep that snapshotted this service before the
    # disable landed. Without this, reconcile silently restarts the edge and
    # recreates the public DNS record for a service taken offline.
    fresh = db.get(Service, service_id, populate_existing=True)
    if fresh is None:
        # The service was deleted while this reconcile waited for / held its
        # per-service lock. reconcile_lock_for() just (re-)created the registry
        # entry for a now-absent id; drop it so _RECONCILE_LOCKS stays bounded by
        # live + in-flight ids. Only the leaf meta-lock is taken here, so the
        # lifecycle -> reconcile -> db-write order is preserved.
        forget_reconcile_lock(service_id)
        result["phase"] = "deleted"
        return result
    if not fresh.enabled:
        _update_phase(db, service_id, "disabled", "Service is disabled")
        result["phase"] = "disabled"
        return result
    service = fresh
    service_name = service.name

    try:
        ts_authkey, paths = steps._validate_and_prepare(db, service)
        cert_path = paths.certs_dir / service.hostname / "current" / "fullchain.pem"

        steps._ensure_network(db, service, socket_path)
        steps._ensure_cert(db, service, cert_path)
        stage = steps._render_and_stage_config(db, service, paths.generated_dir, cert_path)
        steps._ensure_edge(db, service, ts_authkey, paths, socket_path)

        ts_ip = steps._detect_and_persist_ip(db, service, socket_path)
        if ts_ip:
            result["tailscale_ip"] = ts_ip

        steps._ensure_dns(db, service, ts_ip)
        steps._reload_if_needed(db, service, stage, socket_path, result)

        phase, checks = steps._run_and_persist_health(
            db, service, paths.generated_dir, paths.certs_dir, socket_path
        )
        result["health_checks"] = checks
        result["phase"] = phase

        steps._maybe_schedule_probe_retry(checks, phase, service_id, socket_path)

    except ReconcileError as e:
        rollback_with_lock(db)
        logger.error("Reconcile failed for %s: %s", service_id, e)
        result["phase"] = "failed"
        result["error"] = str(e)
        _persist_status(
            db,
            service_id,
            phase="failed",
            message=str(e),
            last_reconciled_at=datetime.now(UTC),
            event={
                "kind": "reconcile_failed",
                "message": f"Reconciliation failed for '{service_name}': {e}",
                "level": "error",
            },
        )

    except Exception as e:
        rollback_with_lock(db)
        logger.error("Unexpected error reconciling %s: %s", service_id, e, exc_info=True)
        result["phase"] = "failed"
        result["error"] = str(e)
        _persist_status(
            db,
            service_id,
            phase="failed",
            message=f"Unexpected error: {e}",
            last_reconciled_at=datetime.now(UTC),
            event={
                "kind": "reconcile_failed",
                "message": f"Reconciliation failed for '{service_name}': {e}",
                "level": "error",
            },
        )

    return result
