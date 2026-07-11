"""DNS reconciliation logic for Cloudflare A records.

Given a service with a known Tailscale IP, ensures the DNS record
matches the desired state, and removes it on teardown.

Error-signaling convention
--------------------------
HARD failures a caller must not silently continue past PROPAGATE as exceptions:
``reconcile_dns`` lets Cloudflare API failures (``CloudflareAPIError``) bubble
out, and itself raises ``RuntimeError`` via ``_require_record_id`` when a create
returns no record id (or the selected existing record carries none) — so reconcile
never persists a phantom record on a hard failure.

BEST-EFFORT/partial results use a return value by design: ``cleanup_dns_record``
returns a summary dict ``{deleted_remote, deleted_local, error}`` so a caller
tearing a service down can record the failure and still finish teardown. The
local row is dropped only when the remote delete succeeded (or the record was
already gone); callers that must NOT proceed on a failed cleanup inspect
``error`` and raise themselves.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.adapters.cloudflare_adapter import (
    CF_CLEANUP_TIMEOUT,
    create_a_record,
    delete_a_record,
    is_not_found_error,
    list_a_records,
    ownership_comment,
    update_a_record,
)
from app.adapters.cloudflare_dns_records import owned_duplicates, select_owned_or_lowest
from app.database import commit_with_lock, db_write_section, flush_with_lock
from app.events.event_emitter import emit_event
from app.events.types import EventKind
from app.models.dns_record import DnsRecord

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)


def _require_record_id(record: dict, action: str, hostname: str) -> str:
    record_id = record.get("id") if isinstance(record, dict) else None
    if not record_id:
        raise RuntimeError(f"Cloudflare {action} for {hostname} did not return a DNS record id")
    return str(record_id)


def _remove_owned_duplicates(
    records: list[dict],
    *,
    canonical_id: str,
    own_comment: str,
    cf_token: str,
    zone_id: str,
    hostname: str,
) -> list[str]:
    """Delete every A record (other than the canonical one) that PROVABLY carries our
    ownership marker for this service — duplicates left by a partially-failed create.

    Safety invariant: a record is deleted ONLY when its ``comment`` exactly equals our
    marker. Records without the marker (external/manual, or another service's) are
    NEVER touched. Best-effort: each delete is capped at CF_CLEANUP_TIMEOUT and a hard
    failure is logged and skipped rather than aborting reconcile; an already-gone
    record counts as removed. Returns the ids that are no longer present.
    """
    removed: list[str] = []
    for record in owned_duplicates(records, canonical_id=canonical_id, own_comment=own_comment):
        record_id = record.get("id")
        try:
            delete_a_record(cf_token, zone_id, str(record_id), timeout=CF_CLEANUP_TIMEOUT)
            removed.append(str(record_id))
        except Exception as exc:
            if is_not_found_error(exc):
                removed.append(str(record_id))  # already gone -> desired state reached
            else:
                logger.warning(
                    "Best-effort cleanup of duplicate DNS record %s for %s failed: %s",
                    record_id, hostname, exc,
                )
    return removed


def reconcile_dns(
    db: Session,
    service: Service,
    tailscale_ip: str,
    cf_token: str,
    zone_id: str,
) -> DnsRecord:
    """Ensure DNS A record for a service matches its Tailscale IP.

    Logic:
    1. List all A records for the hostname and pick OUR record: prefer one carrying
       our ownership marker, else the deterministic lowest-id fallback.
    2. If absent: create it (stamped with our ownership marker).
    3. If present but wrong IP: update it (re-stamping the marker).
    4. If present, correct IP, but unmarked: stamp the marker (first-time adoption).
    5. If present, correct IP, and already ours: no-op.
    6. Best-effort: delete any OTHER A records that provably carry our marker
       (duplicates from a partially-failed create). Never touches unmarked records.

    Returns the updated DnsRecord.
    """
    hostname = service.hostname
    own_comment = ownership_comment(service.id)

    # Cloudflare I/O is deliberately outside the SQLite write lock.  Only the
    # local row/event mutations below are serialized. Cap each call at the short
    # CF_CLEANUP_TIMEOUT (10s): the DNS step runs these calls while co-holding the
    # per-service reconcile lock AND the GLOBAL ops mutex (locks._GLOBAL_OPS_MUTEX,
    # tier 2b), which every service's DNS step must take -- so a slow/unreachable
    # Cloudflare here stalls the DNS step of ALL services (and the orphan-cleanup
    # job) for the full window. The short cap bounds that worst case (list +
    # create/update + best-effort duplicate deletes) instead of letting it stretch
    # toward the 30s default. This mirrors cleanup_dns_record / the orphan-cleanup job.
    records = list_a_records(cf_token, zone_id, hostname, "A", timeout=CF_CLEANUP_TIMEOUT)
    existing = select_owned_or_lowest(records, own_comment)
    action = "noop"
    record_id = None
    old_ip = None

    if existing is None:
        result = create_a_record(
            cf_token, zone_id, hostname, tailscale_ip,
            timeout=CF_CLEANUP_TIMEOUT, comment=own_comment,
        )
        record_id = _require_record_id(result, "create", hostname)
        action = "created"
    else:
        record_id = _require_record_id(existing, "find", hostname)
        if existing.get("content") != tailscale_ip:
            old_ip = existing.get("content")
            update_a_record(
                cf_token, zone_id, record_id, tailscale_ip,
                timeout=CF_CLEANUP_TIMEOUT, comment=own_comment,
            )
            action = "updated"
        elif existing.get("comment") != own_comment:
            # Correct IP but no ownership marker: first-time adoption of a
            # pre-existing / externally-created record. Re-stamp with our marker
            # (no IP change) so it is provably ours on the next pass.
            update_a_record(
                cf_token, zone_id, record_id, tailscale_ip,
                timeout=CF_CLEANUP_TIMEOUT, comment=own_comment,
            )
            action = "adopted"

    # Best-effort removal of OTHER records that provably carry our marker.
    removed_dups = _remove_owned_duplicates(
        records,
        canonical_id=record_id,
        own_comment=own_comment,
        cf_token=cf_token,
        zone_id=zone_id,
        hostname=hostname,
    )

    # Conflicting siblings we must NOT touch: A records for this hostname that are
    # neither our canonical record nor a provably-owned duplicate we removed (i.e.
    # unmarked or another owner's). They are left untouched by design, but they
    # make DNS resolve to multiple/likely-wrong IPs, so warn the operator -- the
    # find_record path warns on >1 record; this is the reconcile-path equivalent.
    conflicts = [
        str(r.get("id"))
        for r in records
        if str(r.get("id")) != str(record_id) and r.get("comment") != own_comment
    ]
    if conflicts:
        logger.warning(
            "DNS hostname %s has %d A record(s) not managed by tailBale (ids=%s); "
            "they were left untouched and may cause DNS to resolve to the wrong IP. "
            "Remove them manually.",
            hostname, len(conflicts), conflicts,
        )

    with db_write_section(db):
        dns_record = db.get(DnsRecord, service.id)
        if not dns_record:
            dns_record = DnsRecord(
                service_id=service.id,
                hostname=hostname,
                record_type="A",
            )
            db.add(dns_record)
            flush_with_lock(db)

        dns_record.hostname = hostname
        dns_record.record_id = record_id
        dns_record.value = tailscale_ip

        if action == "created":
            emit_event(
                db, service.id, EventKind.DNS_CREATED,
                f"Created DNS A record {hostname} -> {tailscale_ip}",
                level="info",
                details={"hostname": hostname, "ip": tailscale_ip, "record_id": record_id},
            )
            logger.info("Created DNS record for %s -> %s", hostname, tailscale_ip)
        elif action == "updated":
            emit_event(
                db, service.id, EventKind.DNS_UPDATED,
                f"Updated DNS A record {hostname}: {old_ip} -> {tailscale_ip}",
                level="info",
                details={"hostname": hostname, "old_ip": old_ip, "new_ip": tailscale_ip, "record_id": record_id},
            )
            logger.info("Updated DNS record for %s: %s -> %s", hostname, old_ip, tailscale_ip)
        elif action == "adopted":
            emit_event(
                db, service.id, EventKind.DNS_UPDATED,
                f"Adopted DNS A record {hostname} and stamped ownership marker",
                level="info",
                details={"hostname": hostname, "ip": tailscale_ip, "record_id": record_id},
            )
            logger.info("Adopted and stamped DNS record for %s (id=%s)", hostname, record_id)
        else:
            logger.debug("DNS record for %s already correct (%s)", hostname, tailscale_ip)

        if removed_dups:
            emit_event(
                db, service.id, EventKind.DNS_DUPLICATE_REMOVED,
                f"Removed {len(removed_dups)} duplicate DNS A record(s) for {hostname}",
                level="warning",
                details={
                    "hostname": hostname,
                    "canonical_record_id": record_id,
                    "removed_record_ids": removed_dups,
                },
            )
            logger.warning(
                "Removed %d duplicate owned DNS record(s) for %s: %s",
                len(removed_dups), hostname, removed_dups,
            )
        commit_with_lock(db)

        return dns_record


def cleanup_dns_record(
    db: Session,
    service: Service,
    cf_token: str,
    zone_id: str,
    timeout: float = CF_CLEANUP_TIMEOUT,
) -> dict:
    """Attempt to remove DNS record for a service from Cloudflare.

    Returns a structured result:
        deleted_remote: True if Cloudflare record was deleted
        deleted_local: True if local DnsRecord row was removed
        error: error message string if remote deletion failed, else None

    The local DnsRecord row is deleted when the remote delete succeeds OR
    Cloudflare reports the record already gone; it is preserved on any other
    failure, so we never orphan a Cloudflare record we still hold locally.
    """
    dns_record = db.get(DnsRecord, service.id)
    if not dns_record or not dns_record.record_id:
        return {"deleted_remote": False, "deleted_local": False, "error": None}

    record_id = dns_record.record_id

    # Phase 1 -- classify the REMOTE delete in ISOLATION. Only a genuine Cloudflare
    # failure may set ``error`` / report ``deleted_remote=False``. A failure of the
    # LOCAL bookkeeping in phase 2 is NOT a remote failure and must never be
    # misreported as one (otherwise a caller raises a misleading "Cloudflare delete
    # failed" 502 even though the record is gone, plus a false dns_cleanup_failed
    # audit event is logged). This mirrors reconcile_dns, where a post-CF DB error
    # is never swallowed into a phantom remote-failure.
    deleted_remote = False
    try:
        delete_a_record(cf_token, zone_id, record_id, timeout=timeout)
        deleted_remote = True
    except Exception as exc:
        if not is_not_found_error(exc):
            error_msg = str(exc)
            logger.warning(
                "Failed to delete DNS record %s from Cloudflare: %s",
                record_id, error_msg, exc_info=True,
            )
            with db_write_section(db):
                emit_event(
                    db, service.id, EventKind.DNS_CLEANUP_FAILED,
                    f"Failed to remove DNS record for {service.hostname} from Cloudflare: {error_msg}",
                    level="warning",
                    details={"hostname": service.hostname, "record_id": record_id, "error": error_msg},
                )
                commit_with_lock(db)
            return {"deleted_remote": False, "deleted_local": False, "error": error_msg}
        logger.info("DNS record %s for %s was already absent in Cloudflare", record_id, service.hostname)

    # Phase 2 -- the remote record is gone (just deleted, or already absent). Drop the
    # local row under the ownership guard: only when the handle still points at the
    # record we acted on. A concurrent reconcile may have reclaimed the hostname
    # under a NEW id; that row is live, so never delete it nor log a misleading
    # "removed" event. Local bookkeeping is best-effort: a DB error here is logged
    # and the ACCURATE remote outcome is still returned with error=None -- the stale
    # local row self-heals on the next cleanup/reconcile pass.
    deleted_local = False
    try:
        with db_write_section(db):
            current = db.get(DnsRecord, service.id)
            if current and current.record_id == record_id:
                message = (
                    f"Removed DNS record for {service.hostname}"
                    if deleted_remote
                    else f"Removed stale local DNS record for {service.hostname}; Cloudflare record was already absent"
                )
                emit_event(
                    db, service.id, EventKind.DNS_REMOVED, message,
                    level="info",
                    details={"hostname": service.hostname, "record_id": record_id},
                )
                db.delete(current)
                deleted_local = True
            commit_with_lock(db)
    except Exception:
        logger.warning(
            "Cloudflare record for %s was removed but the local DNS row could not be "
            "updated; it will be reconciled on the next pass",
            service.hostname, exc_info=True,
        )
        deleted_local = False
    if deleted_remote:
        logger.info("Deleted DNS record for %s", service.hostname)
    return {"deleted_remote": deleted_remote, "deleted_local": deleted_local, "error": None}
