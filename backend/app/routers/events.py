"""Events API endpoints — query the structured event log."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.event import Event
from app.models.service import Service

router = APIRouter(
    prefix="/api/events",
    tags=["events"],
    dependencies=[Depends(get_current_user)],
)


def _parse_event_details(details: str | None) -> dict | list | str | int | float | bool | None:
    if not details:
        return None
    try:
        return json.loads(details)
    except (json.JSONDecodeError, TypeError):
        return None


def _event_to_dict(evt: Event) -> dict:
    return {
        "id": evt.id,
        "service_id": evt.service_id,
        "kind": evt.kind,
        "level": evt.level,
        "message": evt.message,
        "details": _parse_event_details(evt.details),
        "created_at": evt.created_at.isoformat() if evt.created_at else None,
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("")
async def list_events(
    service_id: str | None = None,
    kind: str | None = None,
    level: str | None = None,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List events with optional filters."""
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
    events = (
        query.order_by(Event.created_at.desc(), Event.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "events": [_event_to_dict(e) for e in events],
        "total": total,
    }


@router.get("/services/{service_id}")
async def service_events(
    service_id: str,
    kind: str | None = None,
    level: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Get events for a specific service."""
    svc = db.get(Service, service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    query = db.query(Event).filter(Event.service_id == service_id)
    if kind:
        query = query.filter(Event.kind == kind)
    if level:
        query = query.filter(Event.level == level)

    total = query.count()
    events = (
        query.order_by(Event.created_at.desc(), Event.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "events": [_event_to_dict(e) for e in events],
        "total": total,
    }
