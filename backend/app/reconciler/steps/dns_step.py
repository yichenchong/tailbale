"""Ensuring-dns step: create/update the public DNS record (best-effort)."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.adapters import dns_reconciler
from app.events.types import EventKind
from app.locks import global_ops_lock
from app.models.service import Service
from app.reconciler.status import _persist_status, _update_phase
from app.secrets import cloudflare_credentials

logger = logging.getLogger(__name__)


def ensure_dns(db: Session, service: Service, ts_ip: str | None) -> None:
    """Ensuring-dns step: create/update the public DNS record (best-effort)."""
    service_id = service.id
    _update_phase(db, service_id, "ensuring_dns", "Updating DNS record")
    cf_token, zone_id = cloudflare_credentials(db)
    if cf_token and zone_id and ts_ip:
        try:
            # Serialize the DNS create/update against orphaned-DNS cleanup
            # (jobs.py holds _GLOBAL_OPS_MUTEX) so a manual orphan retry can't
            # delete a record this reconcile is mid-flight creating. Order stays
            # per-service -> _GLOBAL_OPS_MUTEX (no cycle); only the fast DNS step
            # is serialized, never the slow cert step.
            with global_ops_lock():
                dns_reconciler.reconcile_dns(db, service, ts_ip, cf_token, zone_id)
        except Exception:
            logger.warning("DNS reconciliation failed for %s", service_id, exc_info=True)
            _persist_status(
                db,
                service_id,
                event={
                    "kind": EventKind.DNS_UPDATE_FAILED,
                    "message": "DNS reconciliation failed",
                    "level": "warning",
                },
            )
