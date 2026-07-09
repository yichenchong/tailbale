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
#   - services/create.py            service_created / service_snippet_changed
#   - services/update.py            service_updated / service_snippet_changed
#   - services/delete.py            service_disabled / service_deleted /
#                                   dns_orphan_created (orphan-DNS cleanup job)
#   - services/edge_ops.py          caddy_reloaded / edge_restarted /
#                                   edge_recreated / edge_updated
#   - routers/jobs.py               orphaned-DNS cleanup job outcomes
#   - adapters/dns_reconciler.py    DNS record create/update/remove/cleanup
#   - reconciler/status.py          sole emit site for the reconciler event
#                                   dicts built in steps.py (edge_started /
#                                   tailscale_ip_acquired / caddy_reloaded /
#                                   reconcile_completed / dns_update_failed) and
#                                   reconciler.py (reconcile_failed), threaded
#                                   through ``_persist_status``
#   - reconciler/probe_retry.py     probe retry phase transitions
#   - certs/renewal_task.py         cert issue/renew/fail
#
# Adding a new kind anywhere MUST add it here; ``emit_event`` logs a WARNING
# drift canary (non-fatal) if handed a kind absent from this set.
EVENT_KINDS: frozenset[str] = frozenset(
    {
        # Service lifecycle (services/create.py, update.py, delete.py)
        "service_created",
        "service_updated",
        "service_disabled",
        "service_deleted",
        "service_snippet_changed",
        # Edge container / proxy lifecycle (services/edge_ops.py; reconciler
        # steps.py dicts emitted via status.py)
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
        # DNS records (adapters/dns_reconciler.py, reconciler/steps.py)
        "dns_created",
        "dns_updated",
        "dns_removed",
        "dns_update_failed",
        "dns_cleanup_failed",
        "dns_duplicate_removed",
        # Orphaned-DNS cleanup jobs (services/delete.py, routers/jobs.py)
        "dns_orphan_created",
        "dns_orphan_resolved",
        "dns_orphan_retry_failed",
        "dns_orphan_dismissed",
        # Reconciliation (reconciler/probe_retry.py, reconciler/steps.py,
        # reconciler/reconciler.py)
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
