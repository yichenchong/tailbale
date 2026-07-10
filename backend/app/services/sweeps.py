"""Shared scaffolding for background "sweep over all enabled services" tasks (AR10).

``reconcile_all`` / ``health_check_all`` / ``run_renewal_scan`` each iterate every
enabled service and must survive the same hazard: a per-service commit expires
the session (``expire_on_commit``), so a later attribute access on a service
deleted mid-sweep raises and would abort the WHOLE sweep. The shared guard is to
SNAPSHOT the target set up front and re-fetch (or skip) per item inside the loop.

:func:`snapshot_enabled_service_ids` is that snapshot. :func:`run_id_sweep` is the
simplest shared loop shape — re-fetch by id, skip if deleted, run the body,
rollback+log+count on error — used by the full reconcile sweep. Sweeps with extra
per-item control flow (the health sweep's skip-if-lock-contended, the renewal
scan's ``session_scope`` ownership) consume only the snapshot and keep their own
loop, so a domain-specific guard is never flattened away.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.database import rollback_with_lock
from app.models.service import Service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def snapshot_enabled_service_ids(db: Session) -> list[str]:
    """Return the ids of all enabled services, read up front.

    Snapshotting ids (not ORM instances) lets the caller re-fetch each id inside
    its loop, so a service deleted mid-sweep only skips that one item instead of
    expiring a live instance and aborting the sweep.
    """
    return [s.id for s in db.query(Service).filter(Service.enabled.is_(True)).all()]


def run_id_sweep(
    db: Session,
    service_ids: list[str],
    per_service: Callable[[Session, Service], None],
    *,
    log_label: str,
) -> int:
    """Run *per_service* for each id, re-fetching the service inside the loop.

    Skips ids deleted since the snapshot; on any per-service error rolls back and
    logs (still counting it as processed, matching the historical sweep behavior).
    Returns the number of services processed. This is the reconcile-sweep shape;
    sweeps needing per-item lock/skip semantics keep their own loop and use only
    :func:`snapshot_enabled_service_ids`.
    """
    count = 0
    for service_id in service_ids:
        try:
            svc = db.get(Service, service_id)
            if svc is None:
                continue  # deleted since the snapshot — nothing to process
            per_service(db, svc)
            count += 1
        except Exception:
            rollback_with_lock(db)
            logger.error("Failed to %s service %s", log_label, service_id, exc_info=True)
            count += 1  # still counts as processed
    return count
