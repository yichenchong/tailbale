"""Jobs API — manage orphaned DNS cleanup records and other background jobs."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import commit_with_lock, db_write_section, get_db
from app.models.event import Event
from app.models.job import Job

logger = logging.getLogger(__name__)


def _job_details(job: Job, *, required: bool = False) -> dict:
    """Parse a job details payload, raising 422 when a required payload is unusable."""
    if not job.details:
        return {}
    try:
        details = json.loads(job.details)
    except json.JSONDecodeError as exc:
        if required:
            raise HTTPException(status_code=422, detail="Job details contain invalid JSON") from exc
        logger.warning("Job %s has invalid JSON details", job.id)
        return {}
    if not isinstance(details, dict):
        if required:
            raise HTTPException(status_code=422, detail="Job details must be a JSON object")
        logger.warning("Job %s has non-object JSON details", job.id)
        return {}
    return details


def _job_details_for_response(job: Job) -> dict | None:
    if not job.details:
        return None
    try:
        details = json.loads(job.details)
    except json.JSONDecodeError:
        logger.warning("Job %s has invalid JSON details", job.id)
        return None
    if not isinstance(details, dict):
        logger.warning("Job %s has non-object JSON details", job.id)
        return None
    return details

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
        "details": _job_details_for_response(job),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _emit_event(
    db: Session, service_id: str | None, kind: str, message: str,
    details: dict | None = None, level: str = "info",
) -> None:
    event = Event(
        service_id=service_id,
        kind=kind,
        level=level,
        message=message,
        details=json.dumps(details) if details else None,
    )
    db.add(event)


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
async def list_jobs(
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
async def get_job(job_id: str, db: Session = Depends(get_db)):
    """Get a single job."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_dict(job)


@router.post("/{job_id}/retry")
async def retry_orphan_cleanup(job_id: str, db: Session = Depends(get_db)):
    """Retry an orphaned DNS cleanup job.

    Steps:
    1. Check if the Cloudflare record still exists (via find_record).
    2. If it exists, delete it.
    3. If it's already gone, mark completed.
    4. On success, delete the job row.
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.kind != "dns_orphan_cleanup":
        raise HTTPException(status_code=422, detail="Only dns_orphan_cleanup jobs can be retried")

    details = _job_details(job, required=True)
    record_id = details.get("record_id")
    hostname = details.get("hostname", "unknown")
    zone_id = details.get("zone_id")
    if not zone_id:
        from app.settings_store import get_setting

        zone_id = get_setting(db, "cf_zone_id")

    if not record_id or not zone_id:
        raise HTTPException(
            status_code=422,
            detail="Job is missing record_id or zone_id in details",
        )

    from app.reconciler.reconciler import _RECONCILE_MUTEX
    from app.routers.services import _SERVICE_LIFECYCLE_MUTEX
    from app.secrets import CLOUDFLARE_TOKEN, read_secret
    cf_token = read_secret(CLOUDFLARE_TOKEN)
    if not cf_token:
        raise HTTPException(
            status_code=422,
            detail="Cloudflare API token is not configured",
        )
    with _SERVICE_LIFECYCLE_MUTEX, _RECONCILE_MUTEX:
        with db_write_section(db):
            job = db.get(Job, job_id, populate_existing=True)
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")
            if job.status == "running":
                raise HTTPException(status_code=409, detail="Job is already running")
            job.status = "running"
            commit_with_lock(db)

        try:
            from app.adapters.cloudflare_adapter import delete_a_record, find_record

            # Check whether the record still exists in Cloudflare
            existing = find_record(cf_token, zone_id, hostname, "A")

            if existing and existing.get("id") == record_id:
                # Guard against deleting a record a service has since reclaimed:
                # reconcile_dns reuses the same Cloudflare record id when a hostname
                # is re-exposed, so this id may now belong to a live service.
                from app.models.dns_record import DnsRecord
                owner = db.query(DnsRecord).filter(DnsRecord.record_id == record_id).first()
                if owner is not None:
                    with db_write_section(db):
                        current_job = db.get(Job, job_id, populate_existing=True)
                        if current_job is not None:
                            _emit_event(
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
                delete_a_record(cf_token, zone_id, record_id)
                with db_write_section(db):
                    current_job = db.get(Job, job_id, populate_existing=True)
                    if current_job is None:
                        return {"success": True, "message": f"DNS record for '{hostname}' cleaned up"}
                    _emit_event(
                        db, current_job.service_id, "dns_orphan_resolved",
                        f"Orphaned DNS record for '{hostname}' successfully deleted from Cloudflare",
                        details={"record_id": record_id, "hostname": hostname, "job_id": job_id},
                    )
                    db.delete(current_job)
                    commit_with_lock(db)
            else:
                # Record is already gone (manually deleted or different record now)
                with db_write_section(db):
                    current_job = db.get(Job, job_id, populate_existing=True)
                    if current_job is None:
                        return {"success": True, "message": f"DNS record for '{hostname}' cleaned up"}
                    _emit_event(
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
                    _emit_event(
                        db, current_job.service_id, "dns_orphan_retry_failed",
                        f"Retry of orphaned DNS cleanup for '{hostname}' failed: {error_msg}",
                        details={"record_id": record_id, "hostname": hostname, "error": error_msg},
                        level="warning",
                    )
                    commit_with_lock(db)
            raise HTTPException(status_code=502, detail=f"Cloudflare API error: {error_msg}") from exc


@router.delete("/{job_id}", status_code=204)
async def dismiss_orphan_job(job_id: str, db: Session = Depends(get_db)):
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

        details = _job_details(job)
        hostname = details.get("hostname", "unknown")
        _emit_event(
            db, job.service_id, "dns_orphan_dismissed",
            f"Orphaned DNS cleanup job for '{hostname}' dismissed by user",
            details={"record_id": details.get("record_id"), "hostname": hostname, "job_id": job.id},
        )
        db.delete(job)
        commit_with_lock(db)
