"""Orphaned-DNS cleanup job service (AR4).

The retry/dismiss workflows, the ``Job`` serializer, and the list/get + page
shaping used to live inline in :mod:`app.routers.jobs`. They are extracted here
as transport-agnostic functions so that router stays a thin controller (parse
params -> call service -> return). Validation and lifecycle failures are signaled
with :class:`app.services.errors.ServiceError` (reusing the mapped
:class:`~app.services.errors.UpstreamApiError` for the Cloudflare 502); the single
``@app.exception_handler(ServiceError)`` in :mod:`app.main` translates each to the
EXACT status code + ``{"detail": ...}`` body the router used to raise inline, so
the observable HTTP behavior is unchanged.

Behavior preserved verbatim: the ``lifecycle_then_global_ops`` lock order, the
live-owner TOCTOU re-check before deleting a record a live service may have
reclaimed, the short ``CF_CLEANUP_TIMEOUT`` (10s) cap on both Cloudflare calls,
every emitted event (kind/level/message/details), and the HTTP status mapping.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.adapters import cloudflare_adapter
from app.adapters.cloudflare_adapter import CF_CLEANUP_TIMEOUT
from app.adapters.cloudflare_dns_records import find_by_id
from app.database import commit_with_lock, db_write_section
from app.events.event_emitter import emit_event
from app.events.types import EventKind
from app.locks import lifecycle_then_global_ops
from app.models.dns_record import DnsRecord
from app.models.job import Job
from app.secrets import cloudflare_credentials
from app.services.errors import ServiceError, UpstreamApiError
from app.timeutil import iso

logger = logging.getLogger(__name__)


def _job_to_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "service_id": job.service_id,
        "kind": job.kind,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "details": job.details,
        "created_at": iso(job.created_at),
        "updated_at": iso(job.updated_at),
    }


def list_jobs(
    db: Session,
    *,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List jobs with optional filters, shaped as ``{"jobs": [...], "total": n}``."""
    query = db.query(Job)
    if status:
        query = query.filter(Job.status == status)
    if kind:
        query = query.filter(Job.kind == kind)

    total = query.count()
    jobs = (
        query.order_by(Job.created_at.desc(), Job.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {"jobs": [_job_to_dict(j) for j in jobs], "total": total}


def get_job(db: Session, job_id: str) -> dict:
    """Return a single job as a dict, or raise 404 when it does not exist."""
    job = db.get(Job, job_id)
    if not job:
        raise ServiceError("Job not found", status_code=404)
    return _job_to_dict(job)


def retry_orphan_cleanup(db: Session, job_id: str) -> dict:
    """Retry an orphaned DNS cleanup job.

    Steps:
    1. List the hostname's A records (list_a_records) and locate our SPECIFIC
       orphan by record_id — not find_record's lowest-id pick, which could miss it.
    2. If it still exists and is still orphaned, delete it from Cloudflare.
    3. If a live service has reclaimed the record id, skip deletion (deleting it
       would cause an outage); if it is already gone, there is nothing to delete.
    4. On success (any of the above), delete the job row.
    """
    job = db.get(Job, job_id)
    if not job:
        raise ServiceError("Job not found", status_code=404)
    if job.kind != "dns_orphan_cleanup":
        raise ServiceError("Only dns_orphan_cleanup jobs can be retried", status_code=422)

    details = job.details
    if not isinstance(details, dict):
        raise ServiceError("Job details are missing or malformed", status_code=422)
    record_id = details.get("record_id")
    hostname = details.get("hostname")
    cf_token, cf_zone_id = cloudflare_credentials(db)
    zone_id = details.get("zone_id") or cf_zone_id

    if not record_id or not hostname or not zone_id:
        raise ServiceError(
            "Job is missing record_id, hostname, or zone_id in details",
            status_code=422,
        )

    if not cf_token:
        raise ServiceError("Cloudflare API token is not configured", status_code=422)
    # This job acts on a DNS record whose service is already deleted, so there is
    # no per-service reconcile lock to take. Serialize service-less/global ops via
    # lifecycle_then_global_ops, which takes _SERVICE_LIFECYCLE_MUTEX then
    # _GLOBAL_OPS_MUTEX — the documented tier-1 -> tier-2 (reconcile-slot) order.
    with lifecycle_then_global_ops():
        with db_write_section(db):
            job = db.get(Job, job_id, populate_existing=True)
            if not job:
                raise ServiceError("Job not found", status_code=404)
            if job.status == "running":
                raise ServiceError("Job is already running", status_code=409)
            job.status = "running"
            commit_with_lock(db)

        try:
            # Locate our SPECIFIC orphaned record by record_id among ALL A records
            # for the hostname. find_record() returns only the lowest-id match, so
            # if the hostname carries several A records and the orphan is not the
            # lowest-id one, that call returns a DIFFERENT record: the id compare
            # would fail and we'd wrongly report "cleaned up" and drop the job while
            # the orphaned record survives in Cloudflare. Matching record_id
            # explicitly across the full record set avoids that silent orphan leak.
            records = cloudflare_adapter.list_a_records(cf_token, zone_id, hostname, "A", timeout=CF_CLEANUP_TIMEOUT)
            existing = find_by_id(records, record_id)

            if existing is not None:
                # Guard against deleting a record a service has since reclaimed:
                # reconcile_dns reuses the same Cloudflare record id when a hostname
                # is re-exposed, so this id may now belong to a live service.
                owner = db.query(DnsRecord).filter(DnsRecord.record_id == record_id).first()
                if owner is not None:
                    with db_write_section(db):
                        current_job = db.get(Job, job_id, populate_existing=True)
                        if current_job is not None:
                            emit_event(
                                db, current_job.service_id, EventKind.DNS_ORPHAN_RESOLVED,
                                f"Orphaned DNS record for '{hostname}' is now in use by an "
                                f"active service; skipping deletion and clearing the job",
                                details={"record_id": record_id, "hostname": hostname, "job_id": job_id},
                            )
                            db.delete(current_job)
                            commit_with_lock(db)
                    return {
                        "success": True,
                        "message": f"DNS record for '{hostname}' is now in use by an active service; orphan job cleared",
                    }
                # Record still exists and is orphaned — delete it
                cloudflare_adapter.delete_a_record(cf_token, zone_id, record_id, timeout=CF_CLEANUP_TIMEOUT)
                with db_write_section(db):
                    current_job = db.get(Job, job_id, populate_existing=True)
                    if current_job is None:
                        return {"success": True, "message": f"DNS record for '{hostname}' cleaned up"}
                    emit_event(
                        db, current_job.service_id, EventKind.DNS_ORPHAN_RESOLVED,
                        f"Orphaned DNS record for '{hostname}' successfully deleted from Cloudflare",
                        details={"record_id": record_id, "hostname": hostname, "job_id": job_id},
                    )
                    db.delete(current_job)
                    commit_with_lock(db)
            else:
                # Our specific orphaned record is no longer present (manually
                # deleted, or the hostname now carries only other records).
                with db_write_section(db):
                    current_job = db.get(Job, job_id, populate_existing=True)
                    if current_job is None:
                        return {"success": True, "message": f"DNS record for '{hostname}' cleaned up"}
                    emit_event(
                        db, current_job.service_id, EventKind.DNS_ORPHAN_RESOLVED,
                        f"Orphaned DNS record for '{hostname}' was already removed from Cloudflare",
                        details={"record_id": record_id, "hostname": hostname, "job_id": job_id},
                    )
                    db.delete(current_job)
                    commit_with_lock(db)
            return {"success": True, "message": f"DNS record for '{hostname}' cleaned up"}

        except Exception as exc:
            error_msg = str(exc)
            logger.warning(
                "Retry of orphan cleanup job %s failed: %s",
                job_id, error_msg, exc_info=True,
            )
            with db_write_section(db):
                current_job = db.get(Job, job_id, populate_existing=True)
                if current_job is not None:
                    current_job.status = "failed"
                    current_job.message = f"Retry failed: {error_msg}"
                    emit_event(
                        db, current_job.service_id, EventKind.DNS_ORPHAN_RETRY_FAILED,
                        f"Retry of orphaned DNS cleanup for '{hostname}' failed: {error_msg}",
                        details={"record_id": record_id, "hostname": hostname, "error": error_msg},
                        level="warning",
                    )
                    commit_with_lock(db)
            raise UpstreamApiError("Cloudflare API error") from exc


def dismiss_orphan_job(db: Session, job_id: str) -> None:
    """Dismiss/acknowledge an orphaned DNS job (e.g. after manual cleanup in Cloudflare).

    Deletes the job row without attempting Cloudflare deletion.
    """
    with db_write_section(db):
        job = db.get(Job, job_id, populate_existing=True)
        if not job:
            raise ServiceError("Job not found", status_code=404)
        if job.kind != "dns_orphan_cleanup":
            raise ServiceError("Only dns_orphan_cleanup jobs can be dismissed", status_code=422)
        if job.status == "running":
            raise ServiceError("Job is already running", status_code=409)

        details = job.details if isinstance(job.details, dict) else {}
        hostname = details.get("hostname", "unknown")
        emit_event(
            db, job.service_id, EventKind.DNS_ORPHAN_DISMISSED,
            f"Orphaned DNS cleanup job for '{hostname}' dismissed by user",
            details={"record_id": details.get("record_id"), "hostname": hostname, "job_id": job.id},
        )
        db.delete(job)
        commit_with_lock(db)
