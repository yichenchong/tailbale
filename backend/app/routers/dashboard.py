"""Dashboard summary API endpoint."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import settings_store
from app.auth import get_current_user
from app.database import get_db
from app.events.serialization import event_to_dict
from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.timeutil import days_from_now, iso

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    """Return at-a-glance summary data for the dashboard."""
    total = db.query(Service).count()

    # Count phases with a single GROUP BY (uses ix_service_status_phase) instead
    # of loading every status row and tallying in Python. The inner join keeps
    # this identical to the previous per-service lookup: only statuses attached
    # to an existing service count, and a service with no status contributes to
    # `total` alone.
    phase_counts = dict(
        db.query(ServiceStatus.phase, func.count())
        .join(Service, Service.id == ServiceStatus.service_id)
        .group_by(ServiceStatus.phase)
        .all()
    )
    healthy = phase_counts.get("healthy", 0)
    warning = phase_counts.get("warning", 0)
    error = phase_counts.get("error", 0) + phase_counts.get("failed", 0)

    # Cert expiries needing attention: expiring within the operator-configured
    # renewal window (or already expired). Reads the same
    # ``cert_renewal_window_days`` setting the reconciler/renewal/health paths
    # use, so the dashboard attention list tracks the actual renewal policy
    # instead of a hard-coded 30 days.
    window_days = settings_store.get_positive_int_setting(db, "cert_renewal_window_days")
    # cert_renewal_window_days is only loosely bounded at write, so a
    # legacy/directly-set huge value would push the horizon past the maximum
    # representable date. days_from_now returns None on that OverflowError; an
    # unbounded horizon means "every cert is within range", so clamp to
    # datetime.max (matches the saturating policy in services/cert_ops.py).
    threshold = days_from_now(window_days)
    if threshold is None:
        threshold = datetime.max.replace(tzinfo=UTC)
    certs = (
        db.query(Certificate)
        .filter(
            Certificate.expires_at.isnot(None),
            Certificate.expires_at < threshold,
        )
        # Soonest-to-expire (and already-expired) first so the most urgent cert
        # heads the list; service_id breaks ties for deterministic ordering.
        .order_by(Certificate.expires_at.asc(), Certificate.service_id.asc())
        .all()
    )
    cert_services = {
        s.id: s
        for s in db.query(Service)
        .filter(Service.id.in_([cert.service_id for cert in certs]))
        .all()
    }
    expiring_certs = []
    for cert in certs:
        svc = cert_services.get(cert.service_id)
        expiring_certs.append({
            "service_id": cert.service_id,
            "service_name": svc.name if svc else "Unknown",
            "hostname": svc.hostname if svc else "Unknown",
            "expires_at": iso(cert.expires_at),
        })

    # Recent errors (last 20 error-level events)
    recent_errors = (
        db.query(Event)
        .filter(Event.level == "error")
        .order_by(Event.created_at.desc(), Event.id.desc())
        .limit(20)
        .all()
    )

    # Recent events (last 20)
    recent_events = (
        db.query(Event)
        .order_by(Event.created_at.desc(), Event.id.desc())
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
            event_to_dict(e, fields=("id", "service_id", "kind", "message", "created_at"))
            for e in recent_errors
        ],
        "recent_events": [
            event_to_dict(
                e, fields=("id", "service_id", "kind", "level", "message", "created_at")
            )
            for e in recent_events
        ],
    }
