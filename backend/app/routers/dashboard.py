"""Dashboard summary API endpoint."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/summary")
async def dashboard_summary(db: Session = Depends(get_db)):
    """Return at-a-glance summary data for the dashboard."""
    services = db.query(Service).all()
    total = len(services)

    healthy = 0
    warning = 0
    error = 0
    for svc in services:
        status = db.get(ServiceStatus, svc.id)
        if not status:
            continue
        phase = status.phase
        if phase == "healthy":
            healthy += 1
        elif phase == "warning":
            warning += 1
        elif phase in ("error", "failed"):
            error += 1

    # Upcoming cert expiries (within 30 days)
    threshold = datetime.now(timezone.utc) + timedelta(days=30)
    certs = db.query(Certificate).filter(
        Certificate.expires_at.isnot(None),
        Certificate.expires_at < threshold,
    ).all()
    expiring_certs = []
    for cert in certs:
        svc = db.get(Service, cert.service_id)
        expiring_certs.append({
            "service_id": cert.service_id,
            "service_name": svc.name if svc else "Unknown",
            "hostname": svc.hostname if svc else "Unknown",
            "expires_at": cert.expires_at.isoformat() if cert.expires_at else None,
        })

    # Recent errors (last 20 error-level events)
    recent_errors = (
        db.query(Event)
        .filter(Event.level == "error")
        .order_by(Event.created_at.desc())
        .limit(20)
        .all()
    )

    # Recent events (last 20)
    recent_events = (
        db.query(Event)
        .order_by(Event.created_at.desc())
        .limit(20)
        .all()
    )

    return {
        "services": {
            "total": total,
            "healthy": healthy,
            "warning": warning,
            "error": error,
        },
        "expiring_certs": expiring_certs,
        "recent_errors": [
            {
                "id": e.id,
                "service_id": e.service_id,
                "kind": e.kind,
                "message": e.message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in recent_errors
        ],
        "recent_events": [
            {
                "id": e.id,
                "service_id": e.service_id,
                "kind": e.kind,
                "level": e.level,
                "message": e.message,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in recent_events
        ],
    }
