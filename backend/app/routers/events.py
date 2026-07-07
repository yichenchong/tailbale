"""Events API endpoints — query the structured event log."""

from collections.abc import Iterable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.events.event_emitter import EVENT_KINDS
from app.models.event import Event
from app.models.service import Service
from app.timeutil import iso

router = APIRouter(
    prefix="/api/events",
    tags=["events"],
    dependencies=[Depends(get_current_user)],
)


def _event_to_dict(evt: Event, fields: Iterable[str] | None = None) -> dict:
    """Shape an :class:`Event` row into its JSON wire form.

    The single event-serialization shape reused by the events endpoints,
    ``routers/services.get_cert_logs`` (cert-log subset), and
    ``routers/dashboard`` (recent-errors / recent-events subsets) (AR-R3-7).
    Pass *fields* to project a subset (preserving each caller's exact key set);
    ``None`` returns the full record. ``created_at`` uses the shared
    nullable-datetime wire format (AR-R3-9).
    """
    full = {
        "id": evt.id,
        "service_id": evt.service_id,
        "kind": evt.kind,
        "level": evt.level,
        "message": evt.message,
        "details": evt.details,
        "created_at": iso(evt.created_at),
    }
    if fields is None:
        return full
    return {key: full[key] for key in fields}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _query_events(
    db: Session,
    *,
    service_id: str | None = None,
    kind: str | None = None,
    level: str | None = None,
    search: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[Event], int]:
    """Filter → count → order → paginate the event log.

    The single query pipeline shared by ``list_events`` and ``service_events``
    (AR-R3-7): both previously hand-rolled a byte-near-identical
    filter/``count()``/``order_by(created_at.desc(), id.desc())``/offset+limit
    block. Returns ``(rows, total)`` where ``total`` is the pre-pagination match
    count. ``search`` is escaped via :func:`_escape_like` before the ``ILIKE``.
    """
    query = db.query(Event)
    if service_id:
        query = query.filter(Event.service_id == service_id)
    if kind:
        query = query.filter(Event.kind == kind)
    if level:
        query = query.filter(Event.level == level)
    if search:
        query = query.filter(Event.message.ilike(f"%{_escape_like(search)}%", escape="\\"))

    total = query.count()
    rows = (
        query.order_by(Event.created_at.desc(), Event.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return rows, total


@router.get("")
def list_events(
    service_id: str | None = None,
    kind: str | None = None,
    level: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List events with optional filters."""
    rows, total = _query_events(
        db,
        service_id=service_id,
        kind=kind,
        level=level,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {
        "events": [_event_to_dict(e) for e in rows],
        "total": total,
    }


@router.get("/kinds")
def event_kinds():
    """Return the canonical registry of event kinds.

    The single source the frontend's kind filter is built from, so the dropdown
    never drifts from what the backend actually emits.
    """
    return {"kinds": sorted(EVENT_KINDS)}


@router.get("/services/{service_id}")
def service_events(
    service_id: str,
    kind: str | None = None,
    level: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Get events for a specific service."""
    svc = db.get(Service, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    rows, total = _query_events(
        db,
        service_id=service_id,
        kind=kind,
        level=level,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {
        "events": [_event_to_dict(e) for e in rows],
        "total": total,
    }
