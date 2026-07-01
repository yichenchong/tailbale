from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.types import JSONEncodedDict, NaiveUTCDateTime


class ServiceStatus(Base):
    __tablename__ = "service_status"

    service_id: Mapped[str] = mapped_column(
        String, ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )
    phase: Mapped[str] = mapped_column(String, default="pending", index=True)
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    tailscale_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    edge_container_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # JSON-encoded dict of subcheck name -> bool
    health_checks: Mapped[dict | None] = mapped_column(JSONEncodedDict, nullable=True)

    last_reconciled_at: Mapped[datetime | None] = mapped_column(NaiveUTCDateTime, nullable=True)

    # Probe retry tracking — when the next background retry is scheduled
    probe_retry_at: Mapped[datetime | None] = mapped_column(NaiveUTCDateTime, nullable=True)
    probe_retry_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # When the HTTPS probe last ran (pass or fail)
    last_probe_at: Mapped[datetime | None] = mapped_column(NaiveUTCDateTime, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        NaiveUTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
