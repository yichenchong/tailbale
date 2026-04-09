"""DNS reconciliation logic for Cloudflare A records.

Given a service with a known Tailscale IP, ensures the DNS record
matches the desired state. Also provides drift detection.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.adapters.cloudflare_adapter import (
    create_a_record,
    delete_a_record,
    find_record,
    update_a_record,
)
from app.models.dns_record import DnsRecord
from app.models.event import Event

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)


def _emit_event(
    db: Session,
    service_id: str | None,
    kind: str,
    level: str,
    message: str,
    details: dict | None = None,
) -> None:
    event = Event(
        service_id=service_id,
        kind=kind,
        level=level,
        message=message,
        details=json.dumps(details) if details else None,
    )
    db.add(event)


def reconcile_dns(
    db: Session,
    service: Service,
    tailscale_ip: str,
    cf_token: str,
    zone_id: str,
) -> DnsRecord:
    """Ensure DNS A record for a service matches its Tailscale IP.

    Logic:
    1. Find existing A record for hostname
    2. If absent: create it pointing to Tailscale IP
    3. If present but wrong IP: update it
    4. If present and correct: no-op

    Returns the updated DnsRecord.
    """
    hostname = service.hostname

    # Get or create dns_record entry
    dns_record = db.get(DnsRecord, service.id)
    if not dns_record:
        dns_record = DnsRecord(
            service_id=service.id,
            hostname=hostname,
            record_type="A",
        )
        db.add(dns_record)
        db.flush()

    # Check Cloudflare for existing record
    existing = find_record(cf_token, zone_id, hostname, "A")

    if existing is None:
        # Create new record
        result = create_a_record(cf_token, zone_id, hostname, tailscale_ip)
        dns_record.record_id = result.get("id")
        dns_record.value = tailscale_ip
        _emit_event(
            db, service.id, "dns_created", "info",
            f"Created DNS A record {hostname} -> {tailscale_ip}",
            details={"hostname": hostname, "ip": tailscale_ip, "record_id": dns_record.record_id},
        )
        logger.info("Created DNS record for %s -> %s", hostname, tailscale_ip)

    elif existing.get("content") != tailscale_ip:
        # Update existing record with new IP
        record_id = existing["id"]
        old_ip = existing.get("content")
        update_a_record(cf_token, zone_id, record_id, tailscale_ip)
        dns_record.record_id = record_id
        dns_record.value = tailscale_ip
        _emit_event(
            db, service.id, "dns_updated", "info",
            f"Updated DNS A record {hostname}: {old_ip} -> {tailscale_ip}",
            details={"hostname": hostname, "old_ip": old_ip, "new_ip": tailscale_ip, "record_id": record_id},
        )
        logger.info("Updated DNS record for %s: %s -> %s", hostname, old_ip, tailscale_ip)

    else:
        # Record exists and matches — no-op
        dns_record.record_id = existing["id"]
        dns_record.value = tailscale_ip
        logger.debug("DNS record for %s already correct (%s)", hostname, tailscale_ip)

    return dns_record


def detect_dns_drift(
    db: Session,
    service: Service,
    current_tailscale_ip: str | None,
) -> dict:
    """Compare stored DNS value against current Tailscale IP.

    Returns a dict with drift status:
    - dns_record_present: bool
    - dns_matches_ip: bool
    - stored_ip: str | None
    - current_ip: str | None
    - drifted: bool
    """
    dns_record = db.get(DnsRecord, service.id)

    result = {
        "dns_record_present": dns_record is not None and dns_record.record_id is not None,
        "dns_matches_ip": False,
        "stored_ip": dns_record.value if dns_record else None,
        "current_ip": current_tailscale_ip,
        "drifted": False,
    }

    if dns_record and dns_record.value and current_tailscale_ip:
        result["dns_matches_ip"] = dns_record.value == current_tailscale_ip
        result["drifted"] = dns_record.value != current_tailscale_ip

    return result


def cleanup_dns_record(
    db: Session,
    service: Service,
    cf_token: str,
    zone_id: str,
) -> dict:
    """Attempt to remove DNS record for a service from Cloudflare.

    Returns a structured result:
        deleted_remote: True if Cloudflare record was deleted
        deleted_local: True if local DnsRecord row was removed
        error: error message string if remote deletion failed, else None

    The local DnsRecord row is ONLY deleted when the remote deletion
    succeeds.  This prevents orphaning Cloudflare records that we lose
    the local handle for.
    """
    dns_record = db.get(DnsRecord, service.id)
    if not dns_record or not dns_record.record_id:
        return {"deleted_remote": False, "deleted_local": False, "error": None}

    try:
        delete_a_record(cf_token, zone_id, dns_record.record_id)
        _emit_event(
            db, service.id, "dns_removed", "info",
            f"Removed DNS record for {service.hostname}",
            details={"hostname": service.hostname, "record_id": dns_record.record_id},
        )
        logger.info("Deleted DNS record for %s", service.hostname)
        db.delete(dns_record)
        return {"deleted_remote": True, "deleted_local": True, "error": None}
    except Exception as exc:
        error_msg = str(exc)
        logger.warning(
            "Failed to delete DNS record %s from Cloudflare: %s",
            dns_record.record_id, error_msg, exc_info=True,
        )
        _emit_event(
            db, service.id, "dns_cleanup_failed", "warning",
            f"Failed to remove DNS record for {service.hostname} from Cloudflare: {error_msg}",
            details={"hostname": service.hostname, "record_id": dns_record.record_id, "error": error_msg},
        )
        return {"deleted_remote": False, "deleted_local": False, "error": error_msg}
