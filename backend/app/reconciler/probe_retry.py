"""Background HTTPS probe retry for newly-created services.

After the initial reconciliation the HTTPS probe often fails because
Caddy / Tailscale / DNS haven't fully converged yet.  This module
schedules lightweight retries that only re-run health checks (not the
full reconcile) and persist any resulting phase change — in either
direction (it recovers toward healthy, but also records a degradation),
stopping once the service becomes healthy.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from app.backoff import capped_exponential
from app.database import (
    SessionLocal,
    commit_with_lock,
    db_write_section,
    rollback_with_lock,
    session_scope,
)
from app.events.event_emitter import emit_event
from app.health.health_checker import aggregate_status, run_health_checks
from app.locks import forget_reconcile_lock, service_reconcile_lock
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.settings_store import get_runtime_paths

logger = logging.getLogger(__name__)

# Backoff config — capped exponential (see app.backoff). Jitter is OFF by
# default to keep the schedule deterministic; it is opt-in / injectable.
INITIAL_DELAY = 15        # seconds (base)
MAX_DELAY = 3600           # 1 hour cap
MAX_RETRIES = 20           # ~13 hours of retries with exponential backoff
RETRY_JITTER = 0.0         # fraction; >0 de-synchronises concurrent retries
_ACTIVE_RETRIES: set[tuple[str, str | None]] = set()
_ACTIVE_RETRIES_LOCK = threading.Lock()

# Phase severity ordering (lower = healthier) and the event level each phase
# warrants. The probe retry runs a full health check, so the aggregate phase can
# move in EITHER direction between attempts; these let the emitted event report
# the real direction and a level matching the new phase.
_PHASE_RANK = {"healthy": 0, "warning": 1, "error": 2}
_PHASE_LEVEL = {"healthy": "info", "warning": "warning", "error": "error"}
_UNKNOWN_PHASE_RANK = 3  # pending / failed / validating etc. — treat as worst


def _compute_delay(attempt: int, *, jitter: float = RETRY_JITTER, rng=None) -> int:
    """Exponential backoff: 15, 30, 60, 120, 240, ... capped at 3600.

    Routes through :func:`app.backoff.capped_exponential`. Jitter is OFF by
    default, keeping the schedule deterministic (and test-pinned); callers may
    opt in via *jitter* and inject *rng* for reproducibility.
    """
    return int(
        capped_exponential(
            attempt, base=INITIAL_DELAY, cap=MAX_DELAY, jitter=jitter, rng=rng
        )
    )


def schedule_probe_retry(service_id: str, socket_path: str | None = None) -> None:
    """Kick off a background thread that retries the HTTPS probe.

    The thread re-runs the full health checks at escalating intervals and
    persists any phase change.  It stops as soon as the service becomes
    healthy; if all retries are exhausted it gives up (the regular reconcile
    loop will pick it up later).
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
            with service_reconcile_lock(service_id), db_write_section(db):
                status = db.get(ServiceStatus, service_id, populate_existing=True)
                if not status:
                    # Status (and its cascaded service row) vanished between the
                    # pre-lock check and here; drop the entry this acquisition
                    # re-created so the lock registry stays bounded.
                    forget_reconcile_lock(service_id)
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

                status.last_probe_at = datetime.now(UTC)

                # Persist the new aggregate phase whenever it changes. A probe
                # retry runs a FULL health check, so the phase can move either
                # way between attempts (error->warning as things converge, or
                # warning->error if the edge container later dies); report the
                # real direction and a level matching the new phase instead of
                # always claiming an improvement.
                if new_phase != status.phase:
                    old_phase = status.phase
                    status.phase = new_phase
                    status.health_checks = checks
                    status.message = None
                    if new_phase == "healthy":
                        status.probe_retry_at = None
                        status.probe_retry_attempt = None

                    old_rank = _PHASE_RANK.get(old_phase, _UNKNOWN_PHASE_RANK)
                    new_rank = _PHASE_RANK.get(new_phase, _UNKNOWN_PHASE_RANK)
                    verb = (
                        "improved" if new_rank < old_rank
                        else "degraded" if new_rank > old_rank
                        else "changed"
                    )
                    message = (
                        f"Service '{svc.name}' {verb} from {old_phase} "
                        f"to {new_phase} after probe retry"
                    )
                    emit_event(
                        db, svc.id, "probe_retry_phase_change", message,
                        level=_PHASE_LEVEL.get(new_phase, "warning"),
                        details={"phase": new_phase, "checks": checks},
                    )
                    commit_with_lock(db)
                    logger.info(
                        "Probe retry: service %s phase %s -> %s",
                        service_id, old_phase, new_phase,
                    )

                    if new_phase == "healthy":
                        return  # Done!
                else:
                    # Update health checks even if phase didn't change
                    status.health_checks = checks
                    commit_with_lock(db)

        except Exception:
            rollback_with_lock(db)
            # WARNING, not INFO: this catches genuinely unexpected failures
            # (health-check/DB/lock errors), not a normal failing probe. At INFO
            # they vanish under a typical WARNING+ production filter and the loop
            # then silently gives up — surface them so operators can see them.
            logger.warning("Probe retry error for %s", service_id, exc_info=True)
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
    with session_scope() as db:
        try:
            with service_reconcile_lock(service_id), db_write_section(db):
                svc = db.get(Service, service_id)
                status = db.get(ServiceStatus, service_id)
                if not status:
                    # Service deleted while this retry outlived it (status cascades
                    # with the service row); drop the entry this acquisition just
                    # re-created so _RECONCILE_LOCKS stays bounded.
                    forget_reconcile_lock(service_id)
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
            # WARNING, not INFO: this is the same unexpected-failure class (DB /
            # lock errors) the main loop deliberately logs at WARNING. At INFO it
            # vanishes under a typical WARNING+ production filter, hiding a
            # persistently failing retry-state write (the UI never sees the next
            # retry time) even though the loop keeps going.
            logger.warning("Failed to update retry state for %s", service_id, exc_info=True)
            return True


def _clear_retry_state(service_id: str) -> None:
    """Clear retry tracking fields when retries are done."""
    with session_scope() as db:
        try:
            with service_reconcile_lock(service_id), db_write_section(db):
                status = db.get(ServiceStatus, service_id)
                if status:
                    status.probe_retry_at = None
                    status.probe_retry_attempt = None
                    commit_with_lock(db)
                else:
                    # No status row -> the service (and its cascaded status) is gone.
                    # This acquisition re-created the lock entry; drop it so the
                    # registry stays bounded by live + in-flight ids.
                    forget_reconcile_lock(service_id)
        except Exception:
            # WARNING, not INFO (see the main loop's matching rationale): a
            # swallowed clear leaves the probe-retry fields set forever, so the UI
            # shows a pending retry that will never come — invisible under a
            # WARNING+ production filter if logged at INFO.
            logger.warning("Failed to clear retry state for %s", service_id, exc_info=True)
