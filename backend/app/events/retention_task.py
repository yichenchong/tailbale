"""Background event-log retention.

The events table grows unbounded as services reconcile, fail, and recover. This
module trims it: :func:`purge_old_events` deletes rows older than the configured
window, and :func:`retention_loop` runs that purge about once a day, reading the
``event_retention_days`` setting each pass so an operator change takes effect on
the next sweep.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.backoff import run_periodic
from app.database import SessionLocal, commit_with_lock, db_write_section
from app.models.event import Event
from app.settings_store import get_positive_int_setting
from app.timeutil import days_from_now

logger = logging.getLogger(__name__)

# Daily cadence between retention sweeps. A fixed cadence in the app.backoff
# vocabulary (``base == cap``); event retention needn't be more frequent.
RETENTION_INTERVAL_SECONDS = 86400


def purge_old_events(db: Session, *, retention_days: int) -> int:
    """Delete events older than ``now - retention_days``. Returns rows deleted.

    Event timestamps are stored as naive UTC; the tz-aware cutoff is normalized
    by the column's ``NaiveUTCDateTime`` bind so the comparison is apples-to-apples.

    An absurdly large ``retention_days`` (no upper bound is enforced at write —
    settings only validate ``ge=1``) would push the cutoff past the minimum
    representable date, so ``days_from_now`` returns ``None`` instead of a
    datetime. Left unguarded (raising ``OverflowError``) that would abort every
    sweep, so the retention loop backs off forever and the events table grows
    unbounded — the exact failure retention exists to prevent. Treat a ``None``
    cutoff as "no event is old enough to delete" (return 0).
    """
    cutoff = days_from_now(-retention_days)
    if cutoff is None:
        logger.warning(
            "event_retention_days=%d is too large to compute a cutoff; "
            "nothing is old enough to purge",
            retention_days,
        )
        return 0
    with db_write_section(db):
        deleted = (
            db.query(Event)
            .filter(Event.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        commit_with_lock(db)
    return deleted


def run_retention_purge() -> int:
    """Open a session, read the retention window, and purge. Returns rows deleted."""
    db = SessionLocal()
    try:
        retention_days = get_positive_int_setting(db, "event_retention_days")
        return purge_old_events(db, retention_days=retention_days)
    finally:
        db.close()


async def retention_loop() -> None:
    """Async background loop that purges old events about once a day."""

    async def _work() -> None:
        deleted = await asyncio.to_thread(run_retention_purge)
        logger.info("Event retention sweep complete — %d events purged", deleted)

    # Brief startup delay so the app is fully ready, then a daily sweep. A scan
    # error backs off the same daily interval (never crashes or tight-loops).
    await run_periodic(
        name="Event retention loop",
        startup_delay=15,
        interval_fn=lambda: RETENTION_INTERVAL_SECONDS,
        work=_work,
        logger=logger,
    )
