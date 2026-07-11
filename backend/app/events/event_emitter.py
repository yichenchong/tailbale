"""Centralized event emission for the tailBale event log."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.events.types import EVENT_KINDS
from app.models.event import Event

logger = logging.getLogger(__name__)

# The canonical registry of every event ``kind`` the app emits lives in
# :mod:`app.events.types` (``EVENT_KINDS``, derived from the ``EventKind``
# catalogue) and is imported above. It is re-exported from this module for
# backward compatibility with importers of
# ``app.events.event_emitter.EVENT_KINDS`` — the ``GET /api/events/kinds``
# endpoint and the ``emit_event`` drift canary below. Adding a new kind is a
# single edit in events/types.py (a new ``EventKind`` attribute); it flows here
# automatically.


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
    if kind not in EVENT_KINDS:
        # Drift canary: a kind missing from EVENT_KINDS means a call site emitted
        # a new event without registering it. Non-fatal — the event is still
        # persisted — but the frontend kind filter won't surface it until the
        # registry is updated.
        logger.warning(
            "emit_event called with unregistered kind %r; add it to EVENT_KINDS",
            kind,
        )
    event = Event(
        service_id=service_id,
        kind=kind,
        level=level,
        message=message,
        details=details,
    )
    db.add(event)
    return event
