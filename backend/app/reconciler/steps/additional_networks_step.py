"""Ensuring additional edge Docker networks and aliases."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.edge import network_manager
from app.models.service import Service

logger = logging.getLogger(__name__)


def ensure_additional_networks(db: Session, service: Service, socket_path: str | None) -> None:
    """Attach the edge container to configured external networks with aliases.

    Runs as the final reconcile step, so it deliberately does NOT write a
    progress phase: the terminal ServiceStatus phase is owned by the preceding
    health step, and a phase write here would clobber it on the happy path.
    """
    if service.additional_networks is None:
        return
    network_manager.reconcile_additional_edge_networks(
        service.edge_container_name,
        service.network_name,
        service.additional_networks,
        socket_path,
    )
    logger.info("Additional edge networks ready for service %s", service.id)
