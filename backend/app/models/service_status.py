from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ServiceStatus(Base):
    __tablename__ = "service_status"

    service_id: Mapped[str] = mapped_column(
        String, ForeignKey("services.id", ondelete="CASCADE"), primary_key=True
    )
    phase: Mapped[str] = mapped_column(String, default="pending")
    message: Mapped[str | None] = mapped_column(String, nullable=True)
    tailscale_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    edge_container_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # JSON-encoded dict of subcheck name -> bool
    health_checks: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
