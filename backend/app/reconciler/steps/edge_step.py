"""Ensuring-edge step: create the edge container if absent, start it if stopped."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.edge import container_manager, container_session
from app.events.types import EventKind
from app.models.service import Service
from app.reconciler.status import _persist_status, _update_phase
from app.reconciler.steps.models import _RuntimePaths


def ensure_edge(
    db: Session,
    service: Service,
    ts_authkey: str,
    paths: _RuntimePaths,
    socket_path: str | None,
) -> None:
    """Ensuring-edge step: create the edge container if absent, start it if stopped."""
    service_id = service.id
    service_name = service.name
    _update_phase(db, service_id, "ensuring_edge", "Ensuring edge container")
    container = container_session._find_edge_container(service_id, service.edge_container_name, socket_path)
    if container is None:
        container_id = container_manager.create_edge_container(
            service,
            ts_authkey,
            paths.host_generated_dir,
            paths.host_certs_dir,
            paths.host_ts_state_dir,
            socket_path,
        )
        _persist_status(
            db,
            service_id,
            edge_container_id=container_id,
            event={
                "kind": EventKind.EDGE_STARTED,
                "message": f"Edge container created for '{service_name}'",
            },
        )
    else:
        _persist_status(db, service_id, edge_container_id=container.id)

    container = container_session._find_edge_container(service_id, service.edge_container_name, socket_path)
    if container and container.status != "running":
        container_manager.start_edge(service_id, service.edge_container_name, socket_path)
        _persist_status(
            db,
            service_id,
            event={
                "kind": EventKind.EDGE_STARTED,
                "message": f"Edge container started for '{service_name}'",
            },
        )
