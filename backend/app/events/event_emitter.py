"""Centralized event emission for the tailBale event log."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.event import Event

logger = logging.getLogger(__name__)

# Canonical registry of every event ``kind`` the app emits — the single source
# of truth. The frontend fetches it via ``GET /api/events/kinds`` to build the
# Events page kind filter, instead of hardcoding a mirror that silently drifts.
#
# Built EXHAUSTIVELY from every ``emit_event(...)`` call site across
# ``backend/app`` (grep ``emit_event(``):
#   - services/crud.py              service lifecycle, orphan-DNS job creation
#   - services/edge_ops.py          edge recreate/update
#   - routers/services.py           manual Caddy reload / edge restart endpoints
#   - routers/jobs.py               orphaned-DNS cleanup job outcomes
#   - adapters/dns_reconciler.py    DNS record create/update/remove/cleanup
#   - reconciler/reconciler.py      edge/tailscale/caddy/reconcile/dns-failure
#                                   event dicts threaded through ``_persist_status``
#   - reconciler/probe_retry.py     probe retry phase transitions
#   - certs/renewal_task.py         cert issue/renew/fail
#
# Adding a new kind anywhere MUST add it here; ``emit_event`` logs a WARNING
# drift canary (non-fatal) if handed a kind absent from this set.
EVENT_KINDS: frozenset[str] = frozenset(
    {
        # Service lifecycle (services/crud.py)
        "service_created",
        "service_updated",
        "service_disabled",
        "service_deleted",
        "service_snippet_changed",
        # Edge container / proxy lifecycle (reconciler/reconciler.py,
        # routers/services.py, services/edge_ops.py)
        "edge_started",
        "edge_restarted",
        "edge_recreated",
        "edge_updated",
        "caddy_reloaded",
        "tailscale_ip_acquired",
        # Certificates (certs/renewal_task.py)
        "cert_issued",
        "cert_renewed",
        "cert_failed",
        # DNS records (adapters/dns_reconciler.py, reconciler/reconciler.py)
        "dns_created",
        "dns_updated",
        "dns_removed",
        "dns_update_failed",
        "dns_cleanup_failed",
        "dns_duplicate_removed",
        # Orphaned-DNS cleanup jobs (services/crud.py, routers/jobs.py)
        "dns_orphan_created",
        "dns_orphan_resolved",
        "dns_orphan_retry_failed",
        "dns_orphan_dismissed",
        # Reconciliation (reconciler/probe_retry.py, reconciler/reconciler.py)
        "probe_retry_phase_change",
        "reconcile_completed",
        "reconcile_failed",
    }
)


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
