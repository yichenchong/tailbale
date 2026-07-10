"""Events API endpoints — query the structured event log."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.events.event_emitter import EVENT_KINDS
from app.events.querying import query_events
from app.events.serialization import event_to_dict
from app.models.service import Service

router = APIRouter(
    prefix="/api/events",
    tags=["events"],
    dependencies=[Depends(get_current_user)],
)


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
    rows, total = query_events(
        db,
        service_id=service_id,
        kind=kind,
        level=level,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {
        "events": [event_to_dict(e) for e in rows],
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

    rows, total = query_events(
        db,
        service_id=service_id,
        kind=kind,
        level=level,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {
        "events": [event_to_dict(e) for e in rows],
        "total": total,
    }
