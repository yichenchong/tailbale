"""Reconciler status I/O — the ServiceStatus persistence primitives.

A leaf module: it depends only on the database/locks/events/model layers, never
on ``reconciler.py`` or ``steps.py``. Both of those import *this* module, so the
status writes have a single owner and the old ``steps → reconciler`` call-time
back-import (a real two-way cycle) is gone. See ``reconciler/errors.py`` for the
companion ``ReconcileError`` leaf.

``_persist_status`` is the single choke point for every ServiceStatus write in a
reconcile pass: it takes the per-service reconcile lock (reentrant) and the
db-write section, applies only the explicitly-passed fields, optionally emits an
event, and commits — one lock+commit round-trip per call. ``_update_phase`` is
the phase-only shorthand. Each call is an independently-committed, observable
status transition (the live UI polls between them), which is why the reconcile
loop issues one per phase rather than batching.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.database import commit_with_lock, db_write_section
from app.events.event_emitter import emit_event
from app.locks import service_reconcile_lock
from app.models.service_status import ServiceStatus

_UNSET = object()


def _load_or_create_status(db: Session, service_id: str) -> ServiceStatus:
    status = db.get(ServiceStatus, service_id)
    if status is None:
        status = ServiceStatus(service_id=service_id, phase="pending")
        db.add(status)
    return status


def _persist_status(
    db: Session,
    service_id: str,
    *,
    phase: str | object = _UNSET,
    message: str | None | object = _UNSET,
    edge_container_id: str | None | object = _UNSET,
    tailscale_ip: str | None | object = _UNSET,
    health_checks: dict | object = _UNSET,
    last_probe_at: datetime | None | object = _UNSET,
    last_reconciled_at: datetime | None | object = _UNSET,
    probe_retry_at: datetime | None | object = _UNSET,
    probe_retry_attempt: int | None | object = _UNSET,
    event: dict | None = None,
) -> None:
    with service_reconcile_lock(service_id), db_write_section(db):
        status = _load_or_create_status(db, service_id)
        if phase is not _UNSET:
            status.phase = phase
        if message is not _UNSET:
            status.message = message
        if edge_container_id is not _UNSET:
            status.edge_container_id = edge_container_id
        if tailscale_ip is not _UNSET:
            status.tailscale_ip = tailscale_ip
        if health_checks is not _UNSET:
            status.health_checks = health_checks
        if last_probe_at is not _UNSET:
            status.last_probe_at = last_probe_at
        if last_reconciled_at is not _UNSET:
            status.last_reconciled_at = last_reconciled_at
        if probe_retry_at is not _UNSET:
            status.probe_retry_at = probe_retry_at
        if probe_retry_attempt is not _UNSET:
            status.probe_retry_attempt = probe_retry_attempt
        if event is not None:
            emit_event(
                db,
                event.get("service_id", service_id),
                event["kind"],
                event["message"],
                level=event.get("level", "info"),
                details=event.get("details"),
            )
        commit_with_lock(db)


def _update_phase(db: Session, service_id: str, phase: str, message: str | None = None) -> None:
    _persist_status(db, service_id, phase=phase, message=message)
