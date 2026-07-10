"""Service creation lifecycle operation."""

import hashlib

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.database import commit_with_lock, db_write_section, flush_with_lock
from app.edge.docker_client import resolve_socket
from app.events.event_emitter import emit_event
from app.events.types import EventKind
from app.locks import _SERVICE_LIFECYCLE_MUTEX
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.schemas.services import ServiceResponse
from app.services.errors import HostnameInUse
from app.services.lifecycle import reconcile_in_background
from app.services.mapping import derive_edge_names, to_response, unique_slug
from app.services.service_fields import CREATE_COPY_FIELDS


def create_service(
    db: Session,
    body,
    background_tasks: BackgroundTasks,
    configured_domain: str,
) -> ServiceResponse:
    """Persist a new service exposure and schedule its first reconcile.

    The caller (router) has already validated the hostname's base domain and the
    upstream container/port *before* this runs, mirroring the original ordering.
    """
    with _SERVICE_LIFECYCLE_MUTEX, db_write_section(db):
        existing = db.query(Service).filter(Service.hostname == body.hostname).first()
        if existing:
            raise HostnameInUse(body.hostname)

        slug = unique_slug(db, body.name)
        edge_container_name, network_name, ts_hostname = derive_edge_names(slug)
        svc = Service(
            base_domain=configured_domain,
            edge_container_name=edge_container_name,
            network_name=network_name,
            ts_hostname=ts_hostname,
            **{field: getattr(body, field) for field in CREATE_COPY_FIELDS},
        )
        db.add(svc)
        flush_with_lock(db)  # Generate ID

        status_phase = "pending" if svc.enabled else "disabled"
        status_message = (
            "Awaiting first reconciliation" if svc.enabled else "Service is disabled"
        )
        status = ServiceStatus(service_id=svc.id, phase=status_phase, message=status_message)
        db.add(status)
        emit_event(db, svc.id, EventKind.SERVICE_CREATED, f"Service '{svc.name}' created for {svc.hostname}")
        if svc.custom_caddy_snippet:
            snippet = svc.custom_caddy_snippet
            emit_event(
                db,
                svc.id,
                EventKind.SERVICE_SNIPPET_CHANGED,
                f"Custom Caddy snippet set for '{svc.name}'",
                level="warning",
                details={
                    "action": "set",
                    "new_len": len(snippet),
                    "new_sha256": hashlib.sha256(snippet.encode()).hexdigest(),
                },
            )
        commit_with_lock(db)

        db.refresh(svc)
        db.refresh(status)

    # Trigger immediate reconciliation so the frontend sees progress without
    # waiting for the periodic loop. Disabled services deliberately stay offline.
    if svc.enabled:
        background_tasks.add_task(reconcile_in_background, svc.id, resolve_socket(db))

    return to_response(svc, status)
