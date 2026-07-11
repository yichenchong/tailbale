"""Creating-network step: ensure the Docker network and heal a stale upstream id."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.database import commit_with_lock, db_write_section
from app.edge import network_manager
from app.models.service import Service
from app.reconciler.status import _update_phase

logger = logging.getLogger(__name__)


def ensure_network(db: Session, service: Service, socket_path: str | None) -> None:
    """Creating-network step: ensure the Docker network and heal a stale upstream id."""
    service_id = service.id
    _update_phase(db, service_id, "creating_network", "Ensuring Docker network")
    network_id, resolved_upstream_id = network_manager.ensure_network(
        service.network_name,
        service.upstream_container_id,
        socket_path,
        service.upstream_container_name,
    )
    if resolved_upstream_id != service.upstream_container_id:
        with db_write_section(db):
            service.upstream_container_id = resolved_upstream_id
            commit_with_lock(db)
    logger.info(
        "Network %s (%s) ready for service %s",
        service.network_name,
        network_id,
        service_id,
    )
