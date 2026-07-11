import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.types import JSONEncodedDict, NaiveUTCDateTime


def generate_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_id)
    service_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("services.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    level: Mapped[str] = mapped_column(String, nullable=False, default="info")
    message: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONEncodedDict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime, server_default=func.now(), nullable=False, index=True
    )
