"""Jobs API — manage orphaned DNS cleanup records and other background jobs.

Thin controller: each route parses its request params, delegates the whole
workflow to :mod:`app.jobs.orphan_cleanup`, and returns the result. The service
functions raise :class:`app.services.errors.ServiceError` for every validation /
lifecycle failure; the central handler in :mod:`app.main` maps them to the exact
HTTP status these routes used to raise inline (AR4).
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

# Re-exported for callers/tests that reference ``jobs.CF_CLEANUP_TIMEOUT`` — the
# single-source-of-truth cleanup cap lives in the adapter (never redefined here).
from app.adapters.cloudflare_adapter import CF_CLEANUP_TIMEOUT as CF_CLEANUP_TIMEOUT
from app.auth import get_current_user
from app.database import commit_with_lock, db_write_section, get_db
from app.jobs import orphan_cleanup
from app.models.job import Job

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/jobs",
    tags=["jobs"],
    dependencies=[Depends(get_current_user)],
)


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
    return orphan_cleanup.list_jobs(db, status=status, kind=kind, limit=limit, offset=offset)


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    """Get a single job."""
    return orphan_cleanup.get_job(db, job_id)


@router.post("/{job_id}/retry")
def retry_orphan_cleanup(job_id: str, db: Session = Depends(get_db)):
    """Retry an orphaned DNS cleanup job."""
    return orphan_cleanup.retry_orphan_cleanup(db, job_id)


@router.delete("/{job_id}", status_code=204)
def dismiss_orphan_job(job_id: str, db: Session = Depends(get_db)):
    """Dismiss/acknowledge an orphaned DNS job (e.g. after manual cleanup in Cloudflare)."""
    orphan_cleanup.dismiss_orphan_job(db, job_id)
