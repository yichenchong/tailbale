"""Dashboard summary API endpoint."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.settings_store import get_positive_int_setting

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
    window_days = get_positive_int_setting(db, "cert_renewal_window_days")
    # No upper bound is enforced at write (settings validate ge=1 only — same
    # convention as event_retention_days, see events/retention_task.py), so an
    # absurdly large stored window would push the threshold past the maximum
    # representable date and make ``datetime`` raise OverflowError, 500-ing the
    # whole dashboard. An unbounded horizon means "every cert is within range",
    # so clamp to datetime.max instead of raising.
    try:
        threshold = datetime.now(UTC) + timedelta(days=window_days)
    except OverflowError:
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
            "expires_at": cert.expires_at.isoformat() if cert.expires_at else None,
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
