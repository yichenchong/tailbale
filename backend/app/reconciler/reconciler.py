"""Reconciler engine — converges a service's observed state toward its desired state.

Each ``reconcile_service()`` call is **idempotent**: running it twice in a
row without external changes produces the same result.  The reconciler
follows the 14-step sequence from the spec (section 11.3).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.database import commit_with_lock, db_write_section, rollback_with_lock
from app.events.event_emitter import emit_event
from app.models.service_status import ServiceStatus

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)
_RECONCILE_MUTEX = threading.RLock()
_UNSET = object()


class ReconcileError(Exception):
    """Raised when reconciliation hits a non-recoverable failure."""


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
    event: dict | None = None,
) -> None:
    with _RECONCILE_MUTEX, db_write_section(db):
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
            status.health_checks = json.dumps(health_checks)
        if last_probe_at is not _UNSET:
            status.last_probe_at = last_probe_at
        if last_reconciled_at is not _UNSET:
            status.last_reconciled_at = last_reconciled_at
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


def reconcile_service(
    db: Session,
    service: Service,
    *,
    socket_path: str | None = None,
) -> dict:
    """Run the full reconciliation loop for a single service.

    Returns a summary dict with keys:
      phase, tailscale_ip, health_checks, caddy_reloaded, error
    """

    result = {
        "phase": "pending",
        "tailscale_ip": None,
        "health_checks": {},
        "caddy_reloaded": False,
        "error": None,
    }

    with _RECONCILE_MUTEX:
        return _reconcile_service_locked(db, service, socket_path=socket_path, result=result)


def _reconcile_service_locked(
    db: Session,
    service: Service,
    *,
    socket_path: str | None,
    result: dict,
) -> dict:
    """Run reconciliation while holding the process-local reconcile mutex."""
    from app.adapters.dns_reconciler import reconcile_dns
    from app.certs.cert_manager import get_cert_expiry
    from app.certs.renewal_task import process_service_cert
    from app.edge.config_renderer import render_caddyfile, write_caddyfile
    from app.edge.container_manager import (
        _find_edge_container,
        create_edge_container,
        detect_tailscale_ip,
        reload_caddy,
        start_edge,
    )
    from app.edge.network_manager import ensure_network
    from app.health.health_checker import CRITICAL_CHECKS, aggregate_status, run_health_checks
    from app.models.service import Service
    from app.secrets import CLOUDFLARE_TOKEN, TAILSCALE_AUTH_KEY, read_secret
    from app.settings_store import get_positive_int_setting, get_setting

    service_id = service.id

    # Honor disable: never converge a service the operator turned off. Re-read
    # inside the reconcile mutex so a disable that committed while we waited is
    # respected — this covers both the manual /reconcile trigger (no enabled
    # filter) and a periodic sweep that snapshotted this service before the
    # disable landed. Without this, reconcile silently restarts the edge and
    # recreates the public DNS record for a service taken offline.
    fresh = db.get(Service, service_id, populate_existing=True)
    if fresh is None:
        result["phase"] = "deleted"
        return result
    if not fresh.enabled:
        _update_phase(db, service_id, "disabled", "Service is disabled")
        result["phase"] = "disabled"
        return result
    service = fresh
    service_name = service.name

    try:
        _update_phase(db, service_id, "validating", "Checking settings and secrets")

        ts_authkey = read_secret(TAILSCALE_AUTH_KEY)
        if not ts_authkey:
            raise ReconcileError("Tailscale auth key not configured")

        from app.settings_store import get_runtime_paths
        runtime = get_runtime_paths(db)
        generated_dir = Path(runtime["generated_dir"])
        certs_dir = Path(runtime["certs_dir"])
        ts_state_dir = Path(runtime["tailscale_state_dir"])
        host_generated_dir = Path(runtime["host_generated_dir"])
        host_certs_dir = Path(runtime["host_certs_dir"])
        host_ts_state_dir = Path(runtime["host_tailscale_state_dir"])

        (generated_dir / service_id).mkdir(parents=True, exist_ok=True)
        (certs_dir / service.hostname).mkdir(parents=True, exist_ok=True)
        (ts_state_dir / service.edge_container_name).mkdir(parents=True, exist_ok=True)

        _update_phase(db, service_id, "creating_network", "Ensuring Docker network")
        network_result = ensure_network(
            service.network_name,
            service.upstream_container_id,
            socket_path,
            service.upstream_container_name,
        )
        resolved_upstream_id = (
            network_result[1]
            if isinstance(network_result, tuple) and len(network_result) == 2
            else service.upstream_container_id
        )
        if resolved_upstream_id != service.upstream_container_id:
            with db_write_section(db):
                service.upstream_container_id = resolved_upstream_id
                commit_with_lock(db)
        logger.info("Network %s ready for service %s", service.network_name, service_id)

        _update_phase(db, service_id, "ensuring_cert", "Checking certificate")
        cert_path = certs_dir / service.hostname / "fullchain.pem"
        if not cert_path.exists():
            process_service_cert(db, service)
        else:
            expiry = get_cert_expiry(cert_path)
            cert_renewal_days = get_positive_int_setting(db, "cert_renewal_window_days")
            if expiry is None:
                process_service_cert(db, service)
            else:
                from datetime import timedelta
                expiry_utc = expiry if expiry.tzinfo else expiry.replace(tzinfo=UTC)
                if expiry_utc <= datetime.now(UTC) + timedelta(days=cert_renewal_days):
                    process_service_cert(db, service)

        _update_phase(db, service_id, "rendering_config", "Generating Caddy configuration")
        new_config = render_caddyfile(service)
        existing_path = generated_dir / service_id / "Caddyfile"
        config_changed = True
        if existing_path.exists():
            config_changed = existing_path.read_text(encoding="utf-8") != new_config
        if config_changed:
            write_caddyfile(service, generated_dir)

        _update_phase(db, service_id, "ensuring_edge", "Ensuring edge container")
        container = _find_edge_container(service_id, service.edge_container_name, socket_path)
        if container is None:
            container_id = create_edge_container(
                service,
                ts_authkey,
                host_generated_dir,
                host_certs_dir,
                host_ts_state_dir,
                socket_path,
            )
            _persist_status(
                db,
                service_id,
                edge_container_id=container_id,
                event={
                    "kind": "edge_started",
                    "message": f"Edge container created for '{service_name}'",
                },
            )
        else:
            _persist_status(db, service_id, edge_container_id=container.id)

        container = _find_edge_container(service_id, service.edge_container_name, socket_path)
        if container and container.status != "running":
            start_edge(service_id, service.edge_container_name, socket_path)
            _persist_status(
                db,
                service_id,
                event={
                    "kind": "edge_started",
                    "message": f"Edge container started for '{service_name}'",
                },
            )

        _update_phase(db, service_id, "detecting_ip", "Waiting for Tailscale IP")
        ts_ip = detect_tailscale_ip(
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
                    "kind": "tailscale_ip_acquired",
                    "message": f"Tailscale IP {ts_ip} assigned to '{service_name}'",
                    "details": {"ip": ts_ip},
                }
            _persist_status(db, service_id, tailscale_ip=ts_ip, event=event)
            result["tailscale_ip"] = ts_ip

        _update_phase(db, service_id, "ensuring_dns", "Updating DNS record")
        cf_token = read_secret(CLOUDFLARE_TOKEN)
        zone_id = get_setting(db, "cf_zone_id")
        if cf_token and zone_id and ts_ip:
            try:
                reconcile_dns(db, service, ts_ip, cf_token, zone_id)
            except Exception:
                logger.warning("DNS reconciliation failed for %s", service_id, exc_info=True)
                _persist_status(
                    db,
                    service_id,
                    event={
                        "kind": "dns_update_failed",
                        "message": "DNS reconciliation failed",
                        "level": "warning",
                    },
                )

        if config_changed:
            _update_phase(db, service_id, "reloading_caddy", "Reloading Caddy")
            try:
                reload_caddy(service_id, service.edge_container_name, socket_path)
                result["caddy_reloaded"] = True
                _persist_status(
                    db,
                    service_id,
                    event={
                        "kind": "caddy_reloaded",
                        "message": f"Caddy reloaded for '{service_name}'",
                    },
                )
            except RuntimeError as e:
                logger.warning("Caddy reload failed for %s", service_id, exc_info=True)
                raise ReconcileError(f"Caddy reload failed: {e}") from e

        _update_phase(db, service_id, "checking_health", "Running health checks")
        checks = run_health_checks(db, service, generated_dir, certs_dir, socket_path)
        result["health_checks"] = checks

        phase = aggregate_status(checks)
        now = datetime.now(UTC)
        result["phase"] = phase
        level = "info" if phase == "healthy" else "warning" if phase == "warning" else "error"
        _persist_status(
            db,
            service_id,
            phase=phase,
            message=None,
            health_checks=checks,
            last_probe_at=now,
            last_reconciled_at=now,
            event={
                "kind": "reconcile_completed",
                "message": f"Reconciliation completed for '{service_name}' — {phase}",
                "level": level,
                "details": {"phase": phase, "checks": checks},
            },
        )

        if not checks.get("https_probe_ok") and phase in ("warning", "error"):
            critical_ok = all(checks.get(check, False) for check in CRITICAL_CHECKS)
            if critical_ok:
                from app.reconciler.probe_retry import schedule_probe_retry

                schedule_probe_retry(service_id, socket_path)

    except ReconcileError as e:
        rollback_with_lock(db)
        logger.error("Reconcile failed for %s: %s", service_id, e)
        result["phase"] = "failed"
        result["error"] = str(e)
        _persist_status(
            db,
            service_id,
            phase="failed",
            message=str(e),
            last_reconciled_at=datetime.now(UTC),
            event={
                "kind": "reconcile_failed",
                "message": f"Reconciliation failed for '{service_name}': {e}",
                "level": "error",
            },
        )

    except Exception as e:
        rollback_with_lock(db)
        logger.error("Unexpected error reconciling %s: %s", service_id, e, exc_info=True)
        result["phase"] = "failed"
        result["error"] = str(e)
        _persist_status(
            db,
            service_id,
            phase="failed",
            message=f"Unexpected error: {e}",
            last_reconciled_at=datetime.now(UTC),
            event={
                "kind": "reconcile_failed",
                "message": f"Reconciliation failed for '{service_name}': {e}",
                "level": "error",
            },
        )

    return result
