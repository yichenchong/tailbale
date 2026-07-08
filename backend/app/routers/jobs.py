"""Jobs API — manage orphaned DNS cleanup records and other background jobs."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.adapters import cloudflare_adapter
from app.adapters.cloudflare_adapter import CF_CLEANUP_TIMEOUT
from app.auth import get_current_user
from app.database import commit_with_lock, db_write_section, get_db
from app.events.event_emitter import emit_event
from app.locks import lifecycle_then_global_ops
from app.models.dns_record import DnsRecord
from app.models.job import Job
from app.secrets import cloudflare_credentials
from app.services.errors import UpstreamApiError
from app.timeutil import iso

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
    dependencies=[Depends(get_current_user)],
)


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


def reset_stale_running_jobs(db: Session) -> int:
    """Reset jobs left in "running" by a crashed/restarted process to "failed".

    A retry sets a job to "running" and then performs Cloudflare I/O; if the
    process stops in that window the job is stuck "running" forever because both
    retry and dismiss reject "running" jobs. On startup nothing is actually
    running, so reclaim them. Returns the number reset.
    """
    with db_write_section(db):
        stale = db.query(Job).filter(Job.status == "running").all()
        for job in stale:
            job.status = "failed"
            job.message = "Reset after restart (was running when the process stopped)"
        commit_with_lock(db)
    if stale:
        logger.info("Reset %d stale running job(s) on startup", len(stale))
    return len(stale)


@router.get("")
def list_jobs(
    status: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List jobs with optional filters."""
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


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    """Get a single job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_dict(job)


@router.post("/{job_id}/retry")
def retry_orphan_cleanup(job_id: str, db: Session = Depends(get_db)):
    """Retry an orphaned DNS cleanup job.

    Steps:
    1. Check whether the Cloudflare record still exists (via find_record).
    2. If it still exists and is still orphaned, delete it from Cloudflare.
    3. If a live service has reclaimed the record id, skip deletion (deleting it
       would cause an outage); if it is already gone, there is nothing to delete.
    4. On success (any of the above), delete the job row.
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.kind != "dns_orphan_cleanup":
        raise HTTPException(status_code=422, detail="Only dns_orphan_cleanup jobs can be retried")

    details = job.details
    if not isinstance(details, dict):
        raise HTTPException(
            status_code=422,
            detail="Job details are missing or malformed",
        )
    record_id = details.get("record_id")
    hostname = details.get("hostname")
    cf_token, cf_zone_id = cloudflare_credentials(db)
    zone_id = details.get("zone_id") or cf_zone_id

    if not record_id or not hostname or not zone_id:
        raise HTTPException(
            status_code=422,
            detail="Job is missing record_id, hostname, or zone_id in details",
        )

    if not cf_token:
        raise HTTPException(
            status_code=422,
            detail="Cloudflare API token is not configured",
        )
    # This job acts on a DNS record whose service is already deleted, so there is
    # no per-service reconcile lock to take. Serialize service-less/global ops via
    # lifecycle_then_global_ops, which takes _SERVICE_LIFECYCLE_MUTEX then
    # _GLOBAL_OPS_MUTEX — the documented tier-1 -> tier-2 (reconcile-slot) order.
    with lifecycle_then_global_ops():
        with db_write_section(db):
            job = db.get(Job, job_id, populate_existing=True)
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")
            if job.status == "running":
                raise HTTPException(status_code=409, detail="Job is already running")
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
            existing = next((r for r in records if r.get("id") == record_id), None)

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
                                db, current_job.service_id, "dns_orphan_resolved",
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
                        db, current_job.service_id, "dns_orphan_resolved",
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
                        db, current_job.service_id, "dns_orphan_resolved",
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
                        db, current_job.service_id, "dns_orphan_retry_failed",
                        f"Retry of orphaned DNS cleanup for '{hostname}' failed: {error_msg}",
                        details={"record_id": record_id, "hostname": hostname, "error": error_msg},
                        level="warning",
                    )
                    commit_with_lock(db)
            raise UpstreamApiError("Cloudflare API error") from exc


@router.delete("/{job_id}", status_code=204)
def dismiss_orphan_job(job_id: str, db: Session = Depends(get_db)):
    """Dismiss/acknowledge an orphaned DNS job (e.g. after manual cleanup in Cloudflare).

    Deletes the job row without attempting Cloudflare deletion.
    """
    with db_write_section(db):
        job = db.get(Job, job_id, populate_existing=True)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.kind != "dns_orphan_cleanup":
            raise HTTPException(status_code=422, detail="Only dns_orphan_cleanup jobs can be dismissed")
        if job.status == "running":
            raise HTTPException(status_code=409, detail="Job is already running")

        details = job.details if isinstance(job.details, dict) else {}
        hostname = details.get("hostname", "unknown")
        emit_event(
            db, job.service_id, "dns_orphan_dismissed",
            f"Orphaned DNS cleanup job for '{hostname}' dismissed by user",
            details={"record_id": details.get("record_id"), "hostname": hostname, "job_id": job.id},
        )
        db.delete(job)
        commit_with_lock(db)
