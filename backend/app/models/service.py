import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def generate_id() -> str:
    return f"svc_{uuid.uuid4().hex[:12]}"


class Service(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=generate_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Upstream
    upstream_container_id: Mapped[str] = mapped_column(String, nullable=False)
    upstream_container_name: Mapped[str] = mapped_column(String, nullable=False)
    upstream_scheme: Mapped[str] = mapped_column(String, default="http")
    upstream_port: Mapped[int] = mapped_column(Integer, nullable=False)
    healthcheck_path: Mapped[str | None] = mapped_column(String, nullable=True)

    # Domain
    hostname: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    base_domain: Mapped[str] = mapped_column(String, nullable=False)

    # Edge (unique: each exposure gets its own edge container, network, and TS identity)
    edge_container_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    network_name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    ts_hostname: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    # Advanced
    preserve_host_header: Mapped[bool] = mapped_column(Boolean, default=True)
    custom_caddy_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_profile: Mapped[str | None] = mapped_column(String, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
