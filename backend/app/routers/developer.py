from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import settings_store
from app.auth import get_current_user
from app.database import commit_with_lock, db_write_section, get_db
from app.locks import lifecycle_lock
from app.models.event import Event
from app.models.job import Job
from app.models.service import Service
from app.models.setting import Setting
from app.models.user import User
from app.secrets import ALL_SECRETS, delete_secret
from app.services import delete_service_record, diagnostics

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user)],
)


def _require_developer_mode(db: Session) -> None:
    if settings_store.get_setting(db, "developer_mode") != "true":
        raise HTTPException(status_code=403, detail="Developer Mode must be enabled first")


@router.get("/developer/main-logs")
def get_main_container_logs(
    tail: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    _require_developer_mode(db)
    return diagnostics.get_main_logs(db, tail)


@router.post("/developer/reset-setup-complete")
def reset_setup_complete(db: Session = Depends(get_db)):
    _require_developer_mode(db)
    with db_write_section(db):
        settings_store.set_setting(db, "setup_complete", "false")
        commit_with_lock(db)
    return {"success": True, "message": "setup_complete reset"}


@router.post("/developer/reset-all")
def reset_all(db: Session = Depends(get_db)):
    _require_developer_mode(db)

    with lifecycle_lock():
        service_ids = [service_id for (service_id,) in db.query(Service.id).all()]
        for service_id in service_ids:
            service = db.get(Service, service_id)
            if service is not None:
                delete_service_record(db, service, cleanup_dns=True)

        with db_write_section(db):
            for job in db.query(Job).all():
                db.delete(job)
            for event in db.query(Event).all():
                db.delete(event)
            for user in db.query(User).all():
                db.delete(user)
            for setting in db.query(Setting).all():
                db.delete(setting)
            commit_with_lock(db)

        for secret_name in ALL_SECRETS:
            delete_secret(secret_name)

    return {"success": True, "message": "All setup state reset"}
