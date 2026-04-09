"""Jobs API — manage orphaned DNS cleanup records and other background jobs."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models.event import Event
from app.models.job import Job

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
        "details": json.loads(job.details) if job.details else None,
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


@router.get("")
async def list_jobs(
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
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
        query.order_by(Job.created_at.desc())
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

    details = json.loads(job.details) if job.details else {}
    record_id = details.get("record_id")
    hostname = details.get("hostname", "unknown")
    zone_id = details.get("zone_id")

    if not record_id or not zone_id:
        raise HTTPException(
            status_code=422,
            detail="Job is missing record_id or zone_id in details",
        )

    from app.secrets import CLOUDFLARE_TOKEN, read_secret
    cf_token = read_secret(CLOUDFLARE_TOKEN)
    if not cf_token:
        raise HTTPException(
            status_code=422,
            detail="Cloudflare API token is not configured",
        )

    job.status = "running"
    db.flush()

    try:
        from app.adapters.cloudflare_adapter import delete_a_record, find_record

        # Check whether the record still exists in Cloudflare
        existing = find_record(cf_token, zone_id, hostname, "A")

        if existing and existing.get("id") == record_id:
            # Record still exists — delete it
            delete_a_record(cf_token, zone_id, record_id)
            _emit_event(
                db, job.service_id, "dns_orphan_resolved",
                f"Orphaned DNS record for '{hostname}' successfully deleted from Cloudflare",
                details={"record_id": record_id, "hostname": hostname, "job_id": job.id},
            )
        else:
            # Record is already gone (manually deleted or different record now)
            _emit_event(
                db, job.service_id, "dns_orphan_resolved",
                f"Orphaned DNS record for '{hostname}' was already removed from Cloudflare",
                details={"record_id": record_id, "hostname": hostname, "job_id": job.id},
            )

        db.delete(job)
        db.commit()
        return {"success": True, "message": f"DNS record for '{hostname}' cleaned up"}

    except Exception as exc:
        error_msg = str(exc)
        logger.warning(
            "Retry of orphan cleanup job %s failed: %s",
            job.id, error_msg, exc_info=True,
        )
        job.status = "failed"
        job.message = f"Retry failed: {error_msg}"
        _emit_event(
            db, job.service_id, "dns_orphan_retry_failed",
            f"Retry of orphaned DNS cleanup for '{hostname}' failed: {error_msg}",
            details={"record_id": record_id, "hostname": hostname, "error": error_msg},
            level="warning",
        )
        db.commit()
        raise HTTPException(status_code=502, detail=f"Cloudflare API error: {error_msg}")


@router.delete("/{job_id}", status_code=204)
async def dismiss_orphan_job(job_id: str, db: Session = Depends(get_db)):
    """Dismiss/acknowledge an orphaned DNS job (e.g. after manual cleanup in Cloudflare).

    Deletes the job row without attempting Cloudflare deletion.
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.kind != "dns_orphan_cleanup":
        raise HTTPException(status_code=422, detail="Only dns_orphan_cleanup jobs can be dismissed")

    details = json.loads(job.details) if job.details else {}
    hostname = details.get("hostname", "unknown")

    _emit_event(
        db, job.service_id, "dns_orphan_dismissed",
        f"Orphaned DNS cleanup job for '{hostname}' dismissed by user",
        details={"record_id": details.get("record_id"), "hostname": hostname, "job_id": job.id},
    )
    db.delete(job)
    db.commit()
