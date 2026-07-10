"""Detecting-ip step: detect the Tailscale IP and persist it (event on change)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.edge import tailscale_ops
from app.events.types import EventKind
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler.status import _persist_status, _update_phase


def detect_and_persist_ip(db: Session, service: Service, socket_path: str | None) -> str | None:
    """Detecting-ip step: detect the Tailscale IP and persist it (event on change)."""
    service_id = service.id
    service_name = service.name
    _update_phase(db, service_id, "detecting_ip", "Waiting for Tailscale IP")
    ts_ip = tailscale_ops.detect_tailscale_ip(
        service_id,
        service.edge_container_name,
        socket_path,
        max_retries=5,
        retry_delay=1.0,
    )
    if ts_ip:
        event = None
        current_status = db.get(ServiceStatus, service_id)
        if current_status and current_status.tailscale_ip != ts_ip:
            event = {
                "kind": EventKind.TAILSCALE_IP_ACQUIRED,
                "message": f"Tailscale IP {ts_ip} assigned to '{service_name}'",
                "details": {"ip": ts_ip},
            }
        _persist_status(db, service_id, tailscale_ip=ts_ip, event=event)
    return ts_ip
