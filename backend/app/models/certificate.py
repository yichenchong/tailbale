from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.types import NaiveUTCDateTime


class Certificate(Base):
    __tablename__ = "certificates"

    service_id: Mapped[str] = mapped_column(
        String, ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )
    hostname: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        NaiveUTCDateTime, nullable=True, index=True
    )
    last_renewed_at: Mapped[datetime | None] = mapped_column(NaiveUTCDateTime, nullable=True)
    last_failure: Mapped[str | None] = mapped_column(String, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(NaiveUTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
