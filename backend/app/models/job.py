import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.types import JSONEncodedDict, NaiveUTCDateTime


def generate_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_id)
    service_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("services.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String, default="pending", index=True
    )  # pending/running/failed — a successful job deletes its row (no "completed" state)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONEncodedDict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime, server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
