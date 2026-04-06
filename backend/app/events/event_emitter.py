"""Centralized event emission for the tailBale event log."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models.event import Event


def emit_event(
    db: Session,
    service_id: str | None,
    kind: str,
    message: str,
    level: str = "info",
    details: dict | None = None,
) -> Event:
    """Create and persist an event record.

    Does NOT commit — the caller owns the transaction boundary.
    """
    event = Event(
        service_id=service_id,
        kind=kind,
        level=level,
        message=message,
        details=json.dumps(details) if details else None,
    )
    db.add(event)
    return event
