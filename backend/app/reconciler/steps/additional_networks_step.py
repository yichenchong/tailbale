"""Ensuring additional edge Docker networks and aliases."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.edge import network_manager
from app.models.service import Service
from app.reconciler.status import _update_phase

logger = logging.getLogger(__name__)


def ensure_additional_networks(db: Session, service: Service, socket_path: str | None) -> None:
    """Attach the edge container to configured external networks with aliases."""
    if service.additional_networks is None:
        return
    service_id = service.id
    _update_phase(
        db,
        service_id,
        "ensuring_additional_networks",
        "Ensuring additional edge networks",
    )
    network_manager.reconcile_additional_edge_networks(
        service.edge_container_name,
        service.network_name,
        service.additional_networks,
        socket_path,
    )
    logger.info("Additional edge networks ready for service %s", service_id)
