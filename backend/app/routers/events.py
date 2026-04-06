"""Events API endpoints — query the structured event log."""

import json

from fastapi import APIRouter, Depends, HTTPException
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


def _event_to_dict(evt: Event) -> dict:
    return {
        "id": evt.id,
        "service_id": evt.service_id,
        "kind": evt.kind,
        "level": evt.level,
        "message": evt.message,
        "details": json.loads(evt.details) if evt.details else None,
        "created_at": evt.created_at.isoformat() if evt.created_at else None,
    }


@router.get("")
async def list_events(
    service_id: str | None = None,
    kind: str | None = None,
    level: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
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
        query = query.filter(Event.message.ilike(f"%{search}%"))

    total = query.count()
    events = (
        query.order_by(Event.created_at.desc())
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
    limit: int = 100,
    offset: int = 0,
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
        query.order_by(Event.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "events": [_event_to_dict(e) for e in events],
        "total": total,
    }
