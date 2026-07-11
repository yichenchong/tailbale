from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.schemas.settings import ConnectionTestResult
from app.services import diagnostics

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/test/docker", response_model=ConnectionTestResult)
def test_docker(db: Session = Depends(get_db)):
    return diagnostics.test_docker(db)


@router.post("/test/cloudflare", response_model=ConnectionTestResult)
def test_cloudflare(db: Session = Depends(get_db)):
    return diagnostics.test_cloudflare(db)


@router.post("/test/tailscale", response_model=ConnectionTestResult)
def test_tailscale():
    return diagnostics.test_tailscale()
