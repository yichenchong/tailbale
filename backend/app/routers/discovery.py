"""Docker container discovery API."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.schemas.services import DiscoveryResponse
from app.services import diagnostics

router = APIRouter(
    prefix="/api/discovery",
    tags=["discovery"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/containers", response_model=DiscoveryResponse)
def list_containers(
    running_only: bool = Query(default=True),
    hide_managed: bool = Query(default=True),
    search: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """List Docker containers available for exposure."""
    return diagnostics.list_discoverable_containers(
        db,
        running_only=running_only,
        hide_managed=hide_managed,
        search=search,
    )
