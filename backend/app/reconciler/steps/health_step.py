"""Checking-health step: run checks, aggregate, and persist the result."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.events.types import EventKind
from app.health import health_checker
from app.health.status_policy import phase_level
from app.models.service import Service
from app.reconciler.status import _persist_status, _update_phase


def run_and_persist_health(
    db: Session,
    service: Service,
    generated_dir: Path,
    certs_dir: Path,
    socket_path: str | None,
) -> tuple[str, dict]:
    """Checking-health step: run checks, aggregate, and persist the result.

    Independently callable (run_health_checks + aggregate_status + _persist_status)
    so a standalone health loop can reuse it. Emits only the reconcile_completed
    event. Returns ``(phase, checks)``.
    """
    service_id = service.id
    service_name = service.name
    _update_phase(db, service_id, "checking_health", "Running health checks")
    checks = health_checker.run_health_checks(db, service, generated_dir, certs_dir, socket_path)

    phase = health_checker.aggregate_status(checks)
    now = datetime.now(UTC)
    level = phase_level(phase)
    _persist_status(
        db,
        service_id,
        phase=phase,
        message=None,
        health_checks=checks,
        last_probe_at=now,
        last_reconciled_at=now,
        event={
            "kind": EventKind.RECONCILE_COMPLETED,
            "message": f"Reconciliation completed for '{service_name}' — {phase}",
            "level": level,
            "details": {"phase": phase, "checks": checks},
        },
    )
    return phase, checks
