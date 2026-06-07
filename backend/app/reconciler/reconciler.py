"""Reconciler engine — converges a service's observed state toward its desired state.

Each ``reconcile_service()`` call is **idempotent**: running it twice in a
row without external changes produces the same result.  The reconciler
follows the 14-step sequence from the spec (section 11.3).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.events.event_emitter import emit_event
from app.models.service_status import ServiceStatus

if TYPE_CHECKING:
    from app.models.service import Service

logger = logging.getLogger(__name__)
_RECONCILE_MUTEX = threading.Lock()


class ReconcileError(Exception):
    """Raised when reconciliation hits a non-recoverable failure."""


def _load_or_create_status(db: Session, service_id: str) -> ServiceStatus:
    status = db.get(ServiceStatus, service_id)
    if status is None:
        status = ServiceStatus(service_id=service_id, phase="pending")
        db.add(status)
    return status


def _update_phase(db: Session, service_id: str, phase: str, message: str | None = None) -> None:
    status = _load_or_create_status(db, service_id)
    status.phase = phase
    if message is not None:
        status.message = message
    db.flush()


def _mark_failed_status(db: Session, service_id: str, message: str) -> None:
    status = _load_or_create_status(db, service_id)
    status.phase = "failed"
    status.message = message
    status.last_reconciled_at = datetime.now(timezone.utc)

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
    from app.health.health_checker import aggregate_status, run_health_checks
    from app.secrets import CLOUDFLARE_TOKEN, TAILSCALE_AUTH_KEY, read_secret
    from app.settings_store import get_setting

    service_id = service.id
    service_name = service.name

    with _RECONCILE_MUTEX:
        result = {
            "phase": "pending",
            "tailscale_ip": None,
            "health_checks": {},
            "caddy_reloaded": False,
            "error": None,
        }

        status = _load_or_create_status(db, service_id)

        try:
            # ── Step 1: Validate references / settings ──
            _update_phase(db, service_id, "validating", "Checking settings and secrets")

            ts_authkey = read_secret(TAILSCALE_AUTH_KEY)
            if not ts_authkey:
                raise ReconcileError("Tailscale auth key not configured")

            # Read paths from DB settings (user-configurable) with env fallback
            from app.settings_store import get_runtime_paths
            runtime = get_runtime_paths(db)
            generated_dir = Path(runtime["generated_dir"])
            certs_dir = Path(runtime["certs_dir"])
            ts_state_dir = Path(runtime["tailscale_state_dir"])

            # Host-side equivalents for Docker bind mounts (differ when
            # HOST_DATA_DIR is set, i.e. tailBale runs inside a container).
            host_generated_dir = Path(runtime["host_generated_dir"])
            host_certs_dir = Path(runtime["host_certs_dir"])
            host_ts_state_dir = Path(runtime["host_tailscale_state_dir"])

            # ── Step 2: Ensure generated directories ──
            (generated_dir / service_id).mkdir(parents=True, exist_ok=True)
            (certs_dir / service.hostname).mkdir(parents=True, exist_ok=True)
            (ts_state_dir / service.edge_container_name).mkdir(parents=True, exist_ok=True)

            # ── Step 3: Ensure Docker network + app connected ──
            _update_phase(db, service_id, "creating_network", "Ensuring Docker network")
            ensure_network(service.network_name, service.upstream_container_id, socket_path)
            logger.info("Network %s ready for service %s", service.network_name, service_id)

            # ── Step 4: Ensure cert exists ──
            _update_phase(db, service_id, "ensuring_cert", "Checking certificate")
            cert_path = certs_dir / service.hostname / "fullchain.pem"
            if not cert_path.exists():
                process_service_cert(db, service)
            else:
                # Check if renewal is needed
                expiry = get_cert_expiry(cert_path)
                cert_renewal_days = int(get_setting(db, "cert_renewal_window_days") or "30")
                if expiry is not None:
                    from datetime import timedelta
                    if expiry < datetime.now(timezone.utc) + timedelta(days=cert_renewal_days):
                        process_service_cert(db, service)

            # ── Step 5: Render Caddy config ──
            _update_phase(db, service_id, "rendering_config", "Generating Caddy configuration")
            new_config = render_caddyfile(service)
            existing_path = generated_dir / service_id / "Caddyfile"
            config_changed = True
            if existing_path.exists():
                config_changed = existing_path.read_text(encoding="utf-8") != new_config
            if config_changed:
                write_caddyfile(service, generated_dir)

            # ── Step 6: Ensure edge container exists ──
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
                status.edge_container_id = container_id
                emit_event(
                    db,
                    service_id,
                    "edge_started",
                    f"Edge container created for '{service_name}'",
                )
            else:
                status.edge_container_id = container.id

            # ── Step 7: Ensure edge container running ──
            container = _find_edge_container(service_id, service.edge_container_name, socket_path)
            if container and container.status != "running":
                start_edge(service_id, service.edge_container_name, socket_path)
                emit_event(
                    db,
                    service_id,
                    "edge_started",
                    f"Edge container started for '{service_name}'",
                )

            # ── Step 8: Detect Tailscale IP ──
            _update_phase(db, service_id, "detecting_ip", "Waiting for Tailscale IP")
            ts_ip = detect_tailscale_ip(
                service_id,
                service.edge_container_name,
                socket_path,
                max_retries=5,
                retry_delay=1.0,
            )
            if ts_ip:
                if status.tailscale_ip != ts_ip:
                    emit_event(
                        db,
                        service_id,
                        "tailscale_ip_acquired",
                        f"Tailscale IP {ts_ip} assigned to '{service_name}'",
                        details={"ip": ts_ip},
                    )
                status.tailscale_ip = ts_ip
                result["tailscale_ip"] = ts_ip

            # ── Step 9: Ensure DNS record ──
            _update_phase(db, service_id, "ensuring_dns", "Updating DNS record")
            cf_token = read_secret(CLOUDFLARE_TOKEN)
            zone_id = get_setting(db, "cf_zone_id")
            if cf_token and zone_id and ts_ip:
                try:
                    reconcile_dns(db, service, ts_ip, cf_token, zone_id)
                except Exception:
                    logger.warning("DNS reconciliation failed for %s", service_id, exc_info=True)
                    emit_event(
                        db,
                        service_id,
                        "dns_update_failed",
                        "DNS reconciliation failed",
                        level="warning",
                    )

            # ── Step 10: Reload Caddy if needed ──
            if config_changed:
                _update_phase(db, service_id, "reloading_caddy", "Reloading Caddy")
                try:
                    reload_caddy(service_id, service.edge_container_name, socket_path)
                    result["caddy_reloaded"] = True
                    emit_event(
                        db,
                        service_id,
                        "caddy_reloaded",
                        f"Caddy reloaded for '{service_name}'",
                    )
                except RuntimeError:
                    logger.warning("Caddy reload failed for %s", service_id, exc_info=True)

            # ── Step 11: Run health checks ──
            _update_phase(db, service_id, "checking_health", "Running health checks")
            checks = run_health_checks(db, service, generated_dir, certs_dir, socket_path)
            result["health_checks"] = checks
            status.health_checks = json.dumps(checks)
            status.last_probe_at = datetime.now(timezone.utc)

            # ── Step 12: Determine final phase ──
            phase = aggregate_status(checks)
            status.phase = phase
            status.message = None
            status.last_reconciled_at = datetime.now(timezone.utc)
            result["phase"] = phase

            level = "info" if phase == "healthy" else "warning" if phase == "warning" else "error"
            emit_event(
                db,
                service_id,
                "reconcile_completed",
                f"Reconciliation completed for '{service_name}' — {phase}",
                level=level,
                details={"phase": phase, "checks": checks},
            )

            # ── Step 13: Schedule probe retry if HTTPS probe failed ──
            # When critical checks pass but the HTTPS probe fails (common on
            # newly-created services), schedule background retries so the
            # status converges without waiting for the next full sweep.
            if not checks.get("https_probe_ok") and phase in ("warning", "error"):
                critical_ok = all(
                    checks.get(check, False)
                    for check in (
                        "edge_container_present",
                        "edge_container_running",
                        "tailscale_ip_present",
                        "cert_present",
                    )
                )
                if critical_ok:
                    from app.reconciler.probe_retry import schedule_probe_retry

                    schedule_probe_retry(service_id, socket_path)

        except ReconcileError as e:
            db.rollback()
            logger.error("Reconcile failed for %s: %s", service_id, e)
            _mark_failed_status(db, service_id, str(e))
            result["phase"] = "failed"
            result["error"] = str(e)
            emit_event(
                db,
                service_id,
                "reconcile_failed",
                f"Reconciliation failed for '{service_name}': {e}",
                level="error",
            )

        except Exception as e:
            db.rollback()
            logger.error("Unexpected error reconciling %s: %s", service_id, e, exc_info=True)
            _mark_failed_status(db, service_id, f"Unexpected error: {e}")
            result["phase"] = "failed"
            result["error"] = str(e)
            emit_event(
                db,
                service_id,
                "reconcile_failed",
                f"Reconciliation failed for '{service_name}': {e}",
                level="error",
            )

        db.commit()
        return result
