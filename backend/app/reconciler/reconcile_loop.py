"""Background reconciliation loop and trigger helpers.

Provides:
- ``reconcile_all()`` — full sweep of all enabled services (14-step reconcile)
- ``reconcile_loop()`` — slow async background loop (hourly full sweep)
- ``health_check_all()`` — lightweight health sweep, escalates unhealthy services
- ``health_check_loop()`` — fast async background loop (per-minute health sweep)
- ``reconcile_one()`` — reconcile a single service by ID (for manual trigger)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app import settings_store
from app.backoff import capped_exponential, run_periodic
from app.database import rollback_with_lock, session_scope
from app.edge import docker_client
from app.health import health_checker
from app.locks import forget_reconcile_lock, try_service_reconcile_lock
from app.models.service import Service
from app.reconciler import reconciler
from app.reconciler.reconciler import reconcile_service

logger = logging.getLogger(__name__)

# Fixed backoff after a sweep error. A degenerate capped exponential
# (``base == cap`` — no growth); the loop carries no cross-iteration attempt
# counter, so it is computed at attempt 0. See app.backoff.
ERROR_BACKOFF_SECONDS = 30


def reconcile_all(db: Session, *, socket_path: str | None = None) -> int:
    """Reconcile every enabled service. Returns the count processed."""
    # Snapshot ids up front: reconcile_service commits per service, which (with
    # expire_on_commit) expires every other ORM object in this session. A later
    # `svc.id` would then reload — and raise ObjectDeletedError if that service
    # was deleted mid-sweep, aborting the whole sweep. Re-fetch each by id inside
    # the loop so one concurrent delete only skips that service.
    service_ids = [s.id for s in db.query(Service).filter(Service.enabled.is_(True)).all()]
    count = 0
    for service_id in service_ids:
        try:
            svc = db.get(Service, service_id)
            if svc is None:
                continue  # deleted since the snapshot — nothing to reconcile
            reconcile_service(db, svc, socket_path=socket_path)
            count += 1
        except Exception:
            rollback_with_lock(db)
            logger.error("Failed to reconcile service %s", service_id, exc_info=True)
            count += 1  # still counts as processed
    return count


def reconcile_one(db: Session, service_id: str, *, socket_path: str | None = None) -> dict:
    """Reconcile a single service by ID.

    Returns the reconcile result dict, or raises if service not found.
    """
    svc = db.get(Service, service_id)
    if not svc:
        raise ValueError(f"Service {service_id} not found")
    return reconcile_service(db, svc, socket_path=socket_path)


def spawn_reconcile(service_id: str, socket_path: str | None) -> dict:
    """Reconcile a single service in a fresh, self-contained session.

    The single body behind every "reconcile off the request" trigger: the
    manual ``/reconcile`` endpoint (run via ``asyncio.to_thread``) and the
    fire-and-forget post-create/enable background tasks. Callers own the
    threading strategy and error handling; fire-and-forget callers discard the
    returned result and swallow exceptions, the manual endpoint forwards both.
    """
    with session_scope() as db:
        return reconcile_one(db, service_id, socket_path=socket_path)


def health_check_all(db: Session, *, socket_path: str | None = None) -> int:
    """Lightweight health sweep over every enabled service.

    For each enabled service: run the health subchecks, aggregate, and persist
    the result WITHOUT emitting an event — a passing check just shows healthy in
    status, so the steady state stays cheap and silent. A service that aggregates
    to anything other than ``healthy`` is escalated to a full reconcile via
    :func:`reconcile_one`, which logs its own ``reconcile_completed`` event and
    repairs the drift. Returns the count of services processed.

    This is the fast counterpart to :func:`reconcile_all`: it runs often (default
    60s) so detection + repair latency for an unhealthy service stays ~1 min,
    while the full 14-step reconcile only runs hourly or on escalation.
    """
    paths = settings_store.get_runtime_paths(db)
    generated_dir = Path(paths["generated_dir"])
    certs_dir = Path(paths["certs_dir"])

    # Snapshot ids up front (see reconcile_all): per-service commits expire the
    # session, so re-fetch each id inside the loop and skip ones deleted mid-sweep.
    service_ids = [s.id for s in db.query(Service).filter(Service.enabled.is_(True)).all()]
    count = 0
    for service_id in service_ids:
        try:
            with try_service_reconcile_lock(service_id) as acquired:
                if not acquired:
                    # A reconcile / operation already holds this service's lock
                    # (e.g. a minutes-long lego DNS-01 issuance). Acquiring it
                    # would stall the whole sweep behind that one service — the
                    # head-of-line blocking this skip-if-contended path fixes. Its
                    # status is being actively managed by the in-progress op, so it
                    # is fresh: skip it this round (don't block, don't count) and
                    # retry on the next sweep.
                    logger.debug(
                        "Health sweep: skipping service %s — reconcile lock held",
                        service_id,
                    )
                    continue
                svc = db.get(Service, service_id)
                if svc is None:
                    # Deleted since the snapshot — nothing to check. The
                    # try-acquire above re-created this id's registry entry via
                    # reconcile_lock_for(); drop it (mirroring reconcile_service's
                    # 'service gone' branch) so _RECONCILE_LOCKS stays bounded by
                    # live + in-flight ids. Done while still holding the lock,
                    # exactly as the reconcile path does.
                    forget_reconcile_lock(service_id)
                    continue
                checks = health_checker.run_health_checks(db, svc, generated_dir, certs_dir, socket_path)
                phase = health_checker.aggregate_status(checks)
                reconciler._persist_status(
                    db,
                    service_id,
                    phase=phase,
                    # Clear any stale message: a non-failure phase always carries a
                    # null message (the full reconcile's health persist sets it to
                    # None). Without this a recovered service would keep showing the
                    # last failure message — e.g. "healthy" alongside "Caddy reload
                    # failed" — until the next hourly full reconcile finally cleared it.
                    message=None,
                    health_checks=checks,
                    last_probe_at=datetime.now(UTC),
                    event=None,
                )
                if phase != "healthy":
                    # Drift detected — escalate to a full reconcile, which emits the
                    # reconcile_completed event and repairs.
                    reconcile_one(db, service_id, socket_path=socket_path)
                count += 1
        except Exception:
            rollback_with_lock(db)
            logger.error("Health check failed for service %s", service_id, exc_info=True)
            count += 1  # still counts as processed
    return count


async def reconcile_loop() -> None:
    """Async background loop that periodically reconciles all enabled services."""
    # Holds the interval read inside the most recent successful sweep so the
    # loop keeps honoring the dynamic reconcile_interval_seconds setting without
    # opening a second session just to re-read it.
    interval = {"value": ERROR_BACKOFF_SECONDS}

    async def _work() -> None:
        def _run_sweep() -> tuple[int, int]:
            with session_scope() as db:
                iv = settings_store.get_positive_int_setting(db, "reconcile_interval_seconds")
                cnt = reconcile_all(db, socket_path=docker_client.resolve_socket(db))
                return cnt, iv

        count, iv = await asyncio.to_thread(_run_sweep)
        interval["value"] = iv
        logger.info("Reconcile sweep complete — %d services processed", count)

    await run_periodic(
        name="Reconcile loop",
        startup_delay=5,
        interval_fn=lambda: interval["value"],
        work=_work,
        on_error=lambda _exc: capped_exponential(
            0, base=ERROR_BACKOFF_SECONDS, cap=ERROR_BACKOFF_SECONDS
        ),
        logger=logger,
    )


async def health_check_loop() -> None:
    """Async background loop: lightweight health sweep over enabled services.

    Runs far more often than the full reconcile loop (default 60s vs hourly) so
    an unhealthy service is detected and escalated to a full reconcile within
    ~1 min, while the healthy steady state stays cheap and silent.
    """
    # See reconcile_loop: stash the last dynamic interval read in-session.
    interval = {"value": ERROR_BACKOFF_SECONDS}

    async def _work() -> None:
        def _run_sweep() -> tuple[int, int]:
            with session_scope() as db:
                iv = settings_store.get_positive_int_setting(db, "health_check_interval_seconds")
                cnt = health_check_all(db, socket_path=docker_client.resolve_socket(db))
                return cnt, iv

        count, iv = await asyncio.to_thread(_run_sweep)
        interval["value"] = iv
        logger.info("Health sweep complete — %d services processed", count)

    await run_periodic(
        name="Health check loop",
        startup_delay=5,
        interval_fn=lambda: interval["value"],
        work=_work,
        on_error=lambda _exc: capped_exponential(
            0, base=ERROR_BACKOFF_SECONDS, cap=ERROR_BACKOFF_SECONDS
        ),
        logger=logger,
    )
