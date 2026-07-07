"""Reconciler engine — converges a service's observed state toward its desired state.

Each ``reconcile_service()`` call is **idempotent**: running it twice in a
row without external changes produces the same result.  The reconciler
follows the 14-step sequence from the spec (section 11.3).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.database import rollback_with_lock
from app.locks import forget_reconcile_lock, service_reconcile_lock
from app.models.service import Service
from app.reconciler import steps
from app.reconciler.errors import ReconcileError
from app.reconciler.status import _persist_status, _update_phase

logger = logging.getLogger(__name__)


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
