"""Background reconciliation loop and trigger helpers.

Provides:
- ``reconcile_all()`` — sweep all enabled services
- ``reconcile_loop()`` — async background loop (periodic sweep)
- ``reconcile_one()`` — reconcile a single service by ID (for manual trigger)
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from app.models.service import Service
from app.reconciler.reconciler import reconcile_service

logger = logging.getLogger(__name__)


def reconcile_all(db: Session, *, socket_path: str | None = None) -> int:
    """Reconcile every enabled service. Returns the count processed."""
    services = db.query(Service).filter(Service.enabled.is_(True)).all()
    count = 0
    for svc in services:
        try:
            reconcile_service(db, svc, socket_path=socket_path)
            count += 1
        except Exception:
            logger.error("Failed to reconcile service %s", svc.id, exc_info=True)
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


async def reconcile_loop() -> None:
    """Async background loop that periodically reconciles all enabled services."""
    # Brief delay at startup so the app is fully ready
    await asyncio.sleep(5)
    logger.info("Reconcile loop started")

    while True:
        try:
            from app.database import SessionLocal
            from app.settings_store import get_setting

            def _run_sweep() -> tuple[int, int]:
                db = SessionLocal()
                try:
                    iv = int(get_setting(db, "reconcile_interval_seconds") or "60")
                    cnt = reconcile_all(db)
                    return cnt, iv
                finally:
                    db.close()

            count, interval = await asyncio.to_thread(_run_sweep)
            logger.info("Reconcile sweep complete — %d services processed", count)

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("Reconcile loop cancelled")
            raise
        except Exception:
            logger.error("Error in reconcile loop", exc_info=True)
            await asyncio.sleep(30)  # back off on error
