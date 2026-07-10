"""DNS subchecks: stored-record presence/match plus live Cloudflare verification.

``cloudflare_adapter`` is imported as a module (not its symbols) so tests — here
and in the reconciler suite, which drives the health path through
``reconcile_service`` — can patch ``find_record`` at the source module and have
the patch take effect: the attribute is resolved at call time rather than bound
once at import.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.adapters import cloudflare_adapter
from app.models.dns_record import DnsRecord
from app.secrets import cloudflare_credentials

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.service import Service

logger = logging.getLogger(__name__)


def _check_stored_dns(db: Session, service: Service, current_ip: str | None) -> tuple[bool, bool]:
    dns_record = db.get(DnsRecord, service.id)
    db_record_present = dns_record is not None and dns_record.record_id is not None
    db_matches_ip = bool(
        dns_record
        and dns_record.value
        and current_ip
        and dns_record.value == current_ip
    )
    return db_record_present, db_matches_ip


def check_live_dns(
    db: Session, service: Service, current_ip: str | None,
) -> tuple[bool, bool, dict[str, object]]:
    """Return live Cloudflare DNS booleans plus manual-check extended fields."""
    db_record_present, db_matches_ip = _check_stored_dns(db, service, current_ip)

    try:
        cf_token, zone_id = cloudflare_credentials(db)
    except Exception:
        logger.info("Could not load Cloudflare settings for DNS health", exc_info=True)
        return db_record_present, db_matches_ip, {}

    if not cf_token or not zone_id:
        return (
            db_record_present,
            db_matches_ip,
            {"cf_error": "Cloudflare token or zone ID not configured"},
        )

    try:
        live_record = cloudflare_adapter.find_record(cf_token, zone_id, service.hostname, "A")
    except Exception as e:
        logger.exception("Live Cloudflare DNS verification failed for service %s", service.id)
        return (
            db_record_present,
            False,
            {"cf_error": f"Cloudflare verification failed ({type(e).__name__})"},
        )

    record_ip = live_record.get("content") if live_record else None
    matches_ip = bool(current_ip and record_ip == current_ip)
    return (
        live_record is not None,
        matches_ip,
        {
            "cf_record_exists": live_record is not None,
            "cf_record_ip": record_ip,
            "cf_ip_matches_tailscale": matches_ip,
        },
    )


def _check_dns(db: Session, service: Service, current_ip: str | None, *, live: bool = False) -> tuple[bool, bool]:
    if not live:
        return _check_stored_dns(db, service, current_ip)

    present, matches_ip, _extended = check_live_dns(db, service, current_ip)
    return present, matches_ip
