"""Background HTTPS probe retry for newly-created services.

After the initial reconciliation the HTTPS probe often fails because
Caddy / Tailscale / DNS haven't fully converged yet.  This module
schedules lightweight retries that only re-run health checks (not
the full reconcile) and update the service status when the probe
finally succeeds.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.database import commit_with_lock, db_write_section, rollback_with_lock

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Backoff config
INITIAL_DELAY = 15        # seconds
MAX_DELAY = 3600           # 1 hour cap
MAX_RETRIES = 20           # ~12 hours of retries with exponential backoff
_ACTIVE_RETRIES: set[tuple[str, str | None]] = set()
_ACTIVE_RETRIES_LOCK = threading.Lock()




def _compute_delay(attempt: int) -> int:
    """Exponential backoff: 15, 30, 60, 120, 240, ... capped at 3600."""
    delay = INITIAL_DELAY * (2 ** attempt)
    return min(delay, MAX_DELAY)


def schedule_probe_retry(service_id: str, socket_path: str | None = None) -> None:
    """Kick off a background thread that retries the HTTPS probe.

    The thread runs health checks at escalating intervals.  As soon as
    the probe succeeds it updates the service status in the DB and stops.
    If all retries are exhausted it silently gives up (the regular
    reconcile loop will pick it up later).
    """
    retry_key = (service_id, socket_path)
    with _ACTIVE_RETRIES_LOCK:
        if retry_key in _ACTIVE_RETRIES:
            logger.info(
                "HTTPS probe retry already scheduled for service %s using socket %s",
                service_id,
                socket_path or "<default>",
            )
            return
        _ACTIVE_RETRIES.add(retry_key)

    t = threading.Thread(
        target=_probe_retry_loop_guarded,
        args=(service_id, socket_path),
        daemon=True,
        name=f"probe-retry-{service_id[:12]}",
    )
    try:
        t.start()
    except Exception:
        with _ACTIVE_RETRIES_LOCK:
            _ACTIVE_RETRIES.discard(retry_key)
        raise
    logger.info("Scheduled HTTPS probe retry for service %s", service_id)


def _probe_retry_loop_guarded(service_id: str, socket_path: str | None) -> None:
    retry_key = (service_id, socket_path)
    try:
        _probe_retry_loop(service_id, socket_path)
    finally:
        with _ACTIVE_RETRIES_LOCK:
            _ACTIVE_RETRIES.discard(retry_key)


def _probe_retry_loop(service_id: str, socket_path: str | None) -> None:
    """Worker that runs in a background thread."""
    import json

    from app.database import SessionLocal
    from app.events.event_emitter import emit_event
    from app.health.health_checker import aggregate_status, run_health_checks
    from app.models.service import Service
    from app.models.service_status import ServiceStatus
    from app.settings_store import get_runtime_paths

    now = lambda: datetime.now(UTC)  # noqa: E731

    for attempt in range(MAX_RETRIES):
        delay = _compute_delay(attempt)

        # Record the next retry time in the DB so the frontend can show it.
        # Stop immediately if the service was deleted or disabled after the
        # retry was scheduled; otherwise a disabled service can keep showing a
        # stale pending retry until the next sleep completes.
        if not _update_retry_state(service_id, attempt + 1, delay):
            return

        time.sleep(delay)

        db = SessionLocal()
        try:
            svc = db.get(Service, service_id)
            if not svc or not svc.enabled:
                logger.info("Probe retry: service %s gone or disabled, stopping", service_id)
                _clear_retry_state(service_id)
                return

            status = db.get(ServiceStatus, service_id)
            if not status:
                return

            # If already healthy, nothing to do
            if status.phase == "healthy":
                logger.info("Probe retry: service %s already healthy", service_id)
                _clear_retry_state(service_id)
                return

            observed_status = (
                status.phase,
                status.message,
                status.tailscale_ip,
                status.edge_container_id,
                status.health_checks,
                status.last_reconciled_at,
                status.probe_retry_at,
                status.probe_retry_attempt,
            )

            runtime = get_runtime_paths(db)
            checks = run_health_checks(
                db, svc, runtime["generated_dir"], runtime["certs_dir"], socket_path,
            )
            new_phase = aggregate_status(checks)
            with db_write_section(db):
                status = db.get(ServiceStatus, service_id, populate_existing=True)
                if not status:
                    return
                current_status = (
                    status.phase,
                    status.message,
                    status.tailscale_ip,
                    status.edge_container_id,
                    status.health_checks,
                    status.last_reconciled_at,
                    status.probe_retry_at,
                    status.probe_retry_attempt,
                )
                if current_status != observed_status:
                    logger.info(
                        "Probe retry: service %s status changed while probing; leaving newer status intact",
                        service_id,
                    )
                    continue

                status.last_probe_at = now()

                # Update status if improved
                if new_phase != status.phase:
                    old_phase = status.phase
                    status.phase = new_phase
                    status.health_checks = json.dumps(checks)
                    status.message = None
                    if new_phase == "healthy":
                        status.probe_retry_at = None
                        status.probe_retry_attempt = None

                    level = "info" if new_phase == "healthy" else "warning"
                    emit_event(
                        db, svc.id, "probe_retry_improved",
                        f"Service '{svc.name}' improved from {old_phase} to {new_phase} after probe retry",
                        level=level,
                        details={"phase": new_phase, "checks": checks},
                    )
                    commit_with_lock(db)
                    logger.info(
                        "Probe retry: service %s improved %s -> %s",
                        service_id, old_phase, new_phase,
                    )

                    if new_phase == "healthy":
                        return  # Done!
                else:
                    # Update health checks even if phase didn't change
                    status.health_checks = json.dumps(checks)
                    commit_with_lock(db)

        except Exception:
            rollback_with_lock(db)
            logger.info("Probe retry error for %s", service_id, exc_info=True)
        finally:
            db.close()

    # Exhausted all retries — clear retry state
    _clear_retry_state(service_id)
    logger.info("Probe retry: exhausted %d retries for service %s", MAX_RETRIES, service_id)


def _update_retry_state(service_id: str, attempt: int, delay: int) -> bool:
    """Persist the next retry time so the frontend can display it.

    Returns False when the retry loop should stop because the service no
    longer exists, is disabled, or has no status row to update.
    """
    from app.database import SessionLocal
    from app.models.service import Service
    from app.models.service_status import ServiceStatus

    db = SessionLocal()
    try:
        with db_write_section(db):
            svc = db.get(Service, service_id)
            status = db.get(ServiceStatus, service_id)
            if not status:
                return False
            if status.phase == "healthy":
                status.probe_retry_at = None
                status.probe_retry_attempt = None
                commit_with_lock(db)
                return False
            if not svc or not svc.enabled:
                status.probe_retry_at = None
                status.probe_retry_attempt = None
                commit_with_lock(db)
                return False
            status.probe_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
            status.probe_retry_attempt = attempt
            commit_with_lock(db)
            return True
    except Exception:
        logger.info("Failed to update retry state for %s", service_id, exc_info=True)
        return True
    finally:
        db.close()


def _clear_retry_state(service_id: str) -> None:
    """Clear retry tracking fields when retries are done."""
    from app.database import SessionLocal
    from app.models.service_status import ServiceStatus

    db = SessionLocal()
    try:
        with db_write_section(db):
            status = db.get(ServiceStatus, service_id)
            if status:
                status.probe_retry_at = None
                status.probe_retry_attempt = None
                commit_with_lock(db)
    except Exception:
        logger.info("Failed to clear retry state for %s", service_id, exc_info=True)
    finally:
        db.close()
