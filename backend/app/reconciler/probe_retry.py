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
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Backoff config
INITIAL_DELAY = 15        # seconds
MAX_DELAY = 3600           # 1 hour cap
MAX_RETRIES = 20           # ~12 hours of retries with exponential backoff


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
    t = threading.Thread(
        target=_probe_retry_loop,
        args=(service_id, socket_path),
        daemon=True,
        name=f"probe-retry-{service_id[:12]}",
    )
    t.start()
    logger.info("Scheduled HTTPS probe retry for service %s", service_id)


def _probe_retry_loop(service_id: str, socket_path: str | None) -> None:
    """Worker that runs in a background thread."""
    import json

    from app.database import SessionLocal
    from app.events.event_emitter import emit_event
    from app.health.health_checker import aggregate_status, run_health_checks
    from app.models.service import Service
    from app.models.service_status import ServiceStatus
    from app.settings_store import get_runtime_paths

    now = lambda: datetime.now(timezone.utc)  # noqa: E731

    for attempt in range(MAX_RETRIES):
        delay = _compute_delay(attempt)

        # Record the next retry time in the DB so the frontend can show it
        _update_retry_state(service_id, attempt + 1, delay)

        time.sleep(delay)

        db = SessionLocal()
        try:
            svc = db.get(Service, service_id)
            if not svc or not svc.enabled:
                logger.debug("Probe retry: service %s gone or disabled, stopping", service_id)
                _clear_retry_state(service_id)
                return

            status = db.get(ServiceStatus, service_id)
            if not status:
                return

            # If already healthy, nothing to do
            if status.phase == "healthy":
                logger.debug("Probe retry: service %s already healthy", service_id)
                _clear_retry_state(service_id)
                return

            runtime = get_runtime_paths(db)
            checks = run_health_checks(
                db, svc, runtime["generated_dir"], runtime["certs_dir"], socket_path,
            )
            new_phase = aggregate_status(checks)
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
                db.commit()

                level = "info" if new_phase == "healthy" else "warning"
                emit_event(
                    db, svc.id, "probe_retry_improved",
                    f"Service '{svc.name}' improved from {old_phase} to {new_phase} after probe retry",
                    level=level,
                    details={"phase": new_phase, "checks": checks},
                )
                logger.info(
                    "Probe retry: service %s improved %s -> %s",
                    service_id, old_phase, new_phase,
                )

                if new_phase == "healthy":
                    return  # Done!
            else:
                # Update health checks even if phase didn't change
                status.health_checks = json.dumps(checks)
                db.commit()

        except Exception:
            logger.debug("Probe retry error for %s", service_id, exc_info=True)
        finally:
            db.close()

    # Exhausted all retries — clear retry state
    _clear_retry_state(service_id)
    logger.info("Probe retry: exhausted %d retries for service %s", MAX_RETRIES, service_id)


def _update_retry_state(service_id: str, attempt: int, delay: int) -> None:
    """Persist the next retry time so the frontend can display it."""
    from app.database import SessionLocal
    from app.models.service_status import ServiceStatus

    db = SessionLocal()
    try:
        status = db.get(ServiceStatus, service_id)
        if status:
            status.probe_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            status.probe_retry_attempt = attempt
            db.commit()
    except Exception:
        logger.debug("Failed to update retry state for %s", service_id, exc_info=True)
    finally:
        db.close()


def _clear_retry_state(service_id: str) -> None:
    """Clear retry tracking fields when retries are done."""
    from app.database import SessionLocal
    from app.models.service_status import ServiceStatus

    db = SessionLocal()
    try:
        status = db.get(ServiceStatus, service_id)
        if status:
            status.probe_retry_at = None
            status.probe_retry_attempt = None
            db.commit()
    except Exception:
        logger.debug("Failed to clear retry state for %s", service_id, exc_info=True)
    finally:
        db.close()
