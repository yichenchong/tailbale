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
from datetime import UTC, datetime, timedelta

from app.backoff import capped_exponential
from app.database import (
    commit_with_lock,
    db_write_section,
    rollback_with_lock,
    session_scope,
)
from app.events.event_emitter import emit_event
from app.events.types import EventKind
from app.health.health_checker import aggregate_status, run_health_checks
from app.health.status_policy import phase_level, transition_verb
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

# Cancellation channel for in-flight retries, keyed by the same
# ``(service_id, socket_path)`` retry key as ``_ACTIVE_RETRIES``. Signalling an
# entry's event wakes the sleeping loop immediately so it stops within
# milliseconds when the service is declared healthy elsewhere, instead of
# lingering out its current backoff (up to an hour on later attempts).
_CANCEL_EVENTS: dict[tuple[str, str | None], threading.Event] = {}
_CANCEL_EVENTS_LOCK = threading.Lock()


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

    # Fresh cancel event keyed by the full retry key, passed by reference to the
    # thread so a later reschedule that replaces the registry entry never
    # orphans this thread's own signal.
    cancel = threading.Event()
    with _CANCEL_EVENTS_LOCK:
        _CANCEL_EVENTS[retry_key] = cancel

    t = threading.Thread(
        target=_probe_retry_loop_guarded,
        args=(service_id, socket_path, cancel),
        daemon=True,
        name=f"probe-retry-{service_id[:12]}",
    )
    try:
        t.start()
    except Exception:
        with _ACTIVE_RETRIES_LOCK:
            _ACTIVE_RETRIES.discard(retry_key)
        _forget_cancel_event(retry_key, cancel)
        raise
    logger.info("Scheduled HTTPS probe retry for service %s", service_id)


def cancel_probe_retry(service_id: str) -> None:
    """Signal every in-flight probe retry for *service_id* to stop promptly.

    Called when the service is declared healthy by another path (the health
    sweep or a full reconcile) so the background thread wakes and exits within
    milliseconds instead of lingering out its current backoff. Fans out across
    every active retry key for the id (retries are keyed by
    ``(service_id, socket_path)`` and a healthy service should stop all of
    them). A no-op when no retry is running.
    """
    with _CANCEL_EVENTS_LOCK:
        events = [e for key, e in _CANCEL_EVENTS.items() if key[0] == service_id]
    for event in events:
        event.set()


def _forget_cancel_event(
    retry_key: tuple[str, str | None], cancel: threading.Event
) -> None:
    """Drop the cancel-registry entry, but only if it is still *cancel*.

    A newer schedule for the same key may have replaced it; never delete
    another thread's event.
    """
    with _CANCEL_EVENTS_LOCK:
        if _CANCEL_EVENTS.get(retry_key) is cancel:
            del _CANCEL_EVENTS[retry_key]


def _probe_retry_loop_guarded(
    service_id: str, socket_path: str | None, cancel: threading.Event
) -> None:
    retry_key = (service_id, socket_path)
    try:
        _probe_retry_loop(service_id, socket_path, cancel)
    finally:
        with _ACTIVE_RETRIES_LOCK:
            _ACTIVE_RETRIES.discard(retry_key)
        _forget_cancel_event(retry_key, cancel)


def _probe_retry_loop(
    service_id: str, socket_path: str | None, cancel: threading.Event | None = None
) -> None:
    """Worker that runs in a background thread.

    *cancel* is signalled when the service is declared healthy (or gone) by
    another path (the health sweep or a full reconcile); the loop then wakes
    from its backoff wait immediately and clears its retry bookkeeping instead
    of lingering out the full delay. Defaults to a private unset event so the
    loop can be driven directly (e.g. in tests) without one.
    """
    if cancel is None:
        cancel = threading.Event()

    for attempt in range(MAX_RETRIES):
        if cancel.is_set():
            _clear_retry_state(service_id)
            return

        delay = _compute_delay(attempt)

        # Record the next retry time in the DB so the frontend can show it.
        # Stop immediately if the service was deleted or disabled after the
        # retry was scheduled; otherwise a disabled service can keep showing a
        # stale pending retry until the next sleep completes.
        if not _update_retry_state(service_id, attempt + 1, delay):
            return

        # Interruptible backoff: wakes the moment cancel is signalled (the
        # service became healthy elsewhere) instead of waiting the full delay.
        if cancel.wait(delay):
            _clear_retry_state(service_id)
            return

        with session_scope() as db:
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

                        verb = transition_verb(old_phase, new_phase)
                        message = (
                            f"Service '{svc.name}' {verb} from {old_phase} "
                            f"to {new_phase} after probe retry"
                        )
                        emit_event(
                            db, svc.id, EventKind.PROBE_RETRY_PHASE_CHANGE, message,
                            level=phase_level(new_phase, unknown="warning"),
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
