"""Reconcile step-helpers — the per-phase building blocks of one reconcile pass.

Each function is a cohesive, independently-testable step over
``(db, service, ...)``; :func:`app.reconciler.reconciler._reconcile_service_locked`
wires them together in spec order. Split out of ``reconciler.py`` so the
orchestration spine (``reconcile_service`` + status persistence) reads without
paging through every helper.

Status I/O (``_persist_status`` / ``_update_phase``) lives in the leaf module
``reconciler/status.py`` and the ``ReconcileError`` sentinel in
``reconciler/errors.py``; these helpers and ``reconciler.py`` both import those
leaves directly, so there is no reconciler↔steps import cycle to reach across.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import docker
from sqlalchemy.orm import Session

from app import secrets, settings_store
from app.adapters import dns_reconciler
from app.certs import cert_manager, renewal_task
from app.database import commit_with_lock, db_write_section
from app.edge import (
    caddy_admin,
    config_renderer,
    container_manager,
    container_session,
    network_manager,
    tailscale_ops,
)
from app.events.types import EventKind
from app.fsutil import atomic_write_text
from app.health import health_checker
from app.health.health_checker import CRITICAL_CHECKS
from app.health.status_policy import phase_level
from app.locks import global_ops_lock
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler import probe_retry
from app.reconciler.errors import ReconcileError
from app.reconciler.status import _persist_status, _update_phase
from app.secrets import cloudflare_credentials
from app.timeutil import as_utc, days_from_now

logger = logging.getLogger(__name__)


def _cert_fingerprint(cert_path: Path) -> str | None:
    """Return a stable fingerprint of the cert file, or None when it is absent.

    Lets the reconciler notice that a renewed certificate landed on disk and
    force a Caddy reload — Caddy never re-reads file-based certs on its own, and
    ``caddy reload`` skips them when the config text is unchanged.
    """
    try:
        return hashlib.sha256(cert_path.read_bytes()).hexdigest()
    except OSError:
        return None


class _RuntimePaths(NamedTuple):
    """Resolved on-disk + host-mapped directories for one reconcile pass."""

    generated_dir: Path
    certs_dir: Path
    ts_state_dir: Path
    host_generated_dir: Path
    host_certs_dir: Path
    host_ts_state_dir: Path


class _ConfigStage(NamedTuple):
    """Result of staging the Caddyfile: what changed and the reload markers."""

    config_changed: bool
    reload_pending_path: Path
    cert_state_path: Path
    current_cert_fp: str | None


def validate_and_prepare(db: Session, service: Service) -> tuple[str, _RuntimePaths]:
    """Validating step: check secrets, resolve runtime paths, create per-service dirs.

    Returns the Tailscale auth key and the resolved runtime paths. Raises
    ReconcileError when the auth key is not configured.
    """
    service_id = service.id
    _update_phase(db, service_id, "validating", "Checking settings and secrets")

    ts_authkey = secrets.read_secret(secrets.TAILSCALE_AUTH_KEY)
    if not ts_authkey:
        raise ReconcileError("Tailscale auth key not configured")

    runtime = settings_store.get_runtime_paths(db)
    paths = _RuntimePaths(
        generated_dir=Path(runtime["generated_dir"]),
        certs_dir=Path(runtime["certs_dir"]),
        ts_state_dir=Path(runtime["tailscale_state_dir"]),
        host_generated_dir=Path(runtime["host_generated_dir"]),
        host_certs_dir=Path(runtime["host_certs_dir"]),
        host_ts_state_dir=Path(runtime["host_tailscale_state_dir"]),
    )

    (paths.generated_dir / service_id).mkdir(parents=True, exist_ok=True)
    (paths.certs_dir / service.hostname).mkdir(parents=True, exist_ok=True)
    (paths.ts_state_dir / service.edge_container_name).mkdir(parents=True, exist_ok=True)
    return ts_authkey, paths


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


def ensure_cert(db: Session, service: Service, cert_path: Path) -> None:
    """Ensuring-cert step: (re)issue the cert when missing, expiring, or mismatched."""
    _update_phase(db, service.id, "ensuring_cert", "Checking certificate")
    if not cert_path.exists():
        renewal_task.process_service_cert(db, service)
        return

    expiry = cert_manager.get_cert_expiry(cert_path)
    cert_renewal_days = settings_store.get_positive_int_setting(db, "cert_renewal_window_days")
    if expiry is None:
        renewal_task.process_service_cert(db, service)
        return

    expiry_utc = as_utc(expiry)
    privkey_path = cert_path.with_name("privkey.pem")
    renewal_threshold = days_from_now(cert_renewal_days)
    # days_from_now returns None only when the (loosely-bounded) renewal window
    # overflows the datetime range; an absurd window means every cert is "within"
    # it, so treat None as renew-eagerly.
    if renewal_threshold is None or expiry_utc <= renewal_threshold:
        renewal_task.process_service_cert(db, service)
    elif not cert_manager.cert_key_pair_matches(cert_path, privkey_path):
        # The cert is issued/published atomically (one relative `current` symlink
        # swap, with the key/chain pair verified before publish), so an
        # unexpired-but-mismatched pair here means on-disk corruption or external
        # tampering rather than a partial write. Caddy would still serve a pair
        # whose key fails every TLS handshake while the expiry checks above notice
        # nothing — heal at reconcile/startup time instead of waiting for the
        # daily renewal scan.
        renewal_task.process_service_cert(db, service)


def render_and_stage_config(
    db: Session, service: Service, generated_dir: Path, cert_path: Path
) -> _ConfigStage:
    """Rendering-config step: render the Caddyfile, diff it, and set reload markers.

    Returns the staging result (config_changed + reload/cert markers) consumed by
    the reload step.
    """
    _update_phase(db, service.id, "rendering_config", "Generating Caddy configuration")
    new_config = config_renderer.render_caddyfile(service)
    service_config_dir = generated_dir / service.id
    existing_path = service_config_dir / "Caddyfile"
    # ".reload_pending" marks that the on-disk Caddyfile changed but the running
    # Caddy has not yet successfully reloaded it. Set when a config change is
    # detected, cleared only after reload_caddy succeeds — so a reload failure is
    # retried on the next reconcile even though the file on disk already matches
    # desired (config_changed would otherwise be False, leaving Caddy serving
    # stale config while the service reports healthy).
    reload_pending_path = service_config_dir / ".reload_pending"
    # Caddy with file-based certs never re-reads a renewed cert on its own, and
    # ``caddy reload`` skips cert files when the config text is unchanged. Track
    # the fingerprint of the cert last loaded into Caddy so a renewal (by this
    # loop or the daily renewal task) forces a reload.
    cert_state_path = service_config_dir / ".cert_loaded"
    current_cert_fp = _cert_fingerprint(cert_path)
    loaded_cert_fp = (
        cert_state_path.read_text(encoding="utf-8").strip()
        if cert_state_path.exists()
        else None
    )
    cert_changed = current_cert_fp is not None and current_cert_fp != loaded_cert_fp
    config_changed = True
    if existing_path.exists():
        config_changed = existing_path.read_text(encoding="utf-8") != new_config
    if config_changed:
        # Mark reload pending BEFORE writing so a crash/failure between the two
        # can never leave a changed on-disk config with no pending-reload marker
        # (which is exactly the desync this guards against). Durable atomic write:
        # a bare write_text wouldn't fsync, so a power-loss could lose the marker
        # and reintroduce the very changed-config-without-pending-reload desync.
        atomic_write_text(reload_pending_path, "1")
        config_renderer.write_caddyfile(service, generated_dir)
    elif cert_changed:
        # Config unchanged but the cert on disk was renewed — force a reload so
        # the edge actually serves the new certificate.
        atomic_write_text(reload_pending_path, "1")
    return _ConfigStage(
        config_changed=config_changed,
        reload_pending_path=reload_pending_path,
        cert_state_path=cert_state_path,
        current_cert_fp=current_cert_fp,
    )


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


def ensure_dns(db: Session, service: Service, ts_ip: str | None) -> None:
    """Ensuring-dns step: create/update the public DNS record (best-effort)."""
    service_id = service.id
    _update_phase(db, service_id, "ensuring_dns", "Updating DNS record")
    cf_token, zone_id = cloudflare_credentials(db)
    if cf_token and zone_id and ts_ip:
        try:
            # Serialize the DNS create/update against orphaned-DNS cleanup
            # (jobs.py holds _GLOBAL_OPS_MUTEX) so a manual orphan retry can't
            # delete a record this reconcile is mid-flight creating. Order stays
            # per-service -> _GLOBAL_OPS_MUTEX (no cycle); only the fast DNS step
            # is serialized, never the slow cert step.
            with global_ops_lock():
                dns_reconciler.reconcile_dns(db, service, ts_ip, cf_token, zone_id)
        except Exception:
            logger.warning("DNS reconciliation failed for %s", service_id, exc_info=True)
            _persist_status(
                db,
                service_id,
                event={
                    "kind": EventKind.DNS_UPDATE_FAILED,
                    "message": "DNS reconciliation failed",
                    "level": "warning",
                },
            )


def reload_if_needed(
    db: Session,
    service: Service,
    stage: _ConfigStage,
    socket_path: str | None,
    result: dict,
) -> None:
    """Reloading-caddy step: reload when config/cert changed; classify failures.

    Every reload failure raises ReconcileError so ``.reload_pending`` stays set
    and the reload is retried next reconcile. Post-reload bookkeeping (clearing
    the marker, recording the loaded-cert fingerprint, emitting the event) stays
    OUT of the try so its own errors are reported truthfully.
    """
    if not (stage.config_changed or stage.reload_pending_path.exists()):
        return

    service_id = service.id
    service_name = service.name
    _update_phase(db, service_id, "reloading_caddy", "Reloading Caddy")
    try:
        caddy_admin.reload_caddy(service_id, service.edge_container_name, socket_path)
    except RuntimeError as e:
        # Caddy rejected the config (bad Caddyfile / non-zero reload exit).
        logger.warning("Caddy reload failed for %s", service_id, exc_info=True)
        raise ReconcileError(f"Caddy reload failed: {e}") from e
    except (docker.errors.DockerException, ConnectionError) as e:
        # The edge container / Docker daemon was unreachable: exec_run re-raises a
        # docker.errors.APIError, or the admin-API socket refused the connection.
        logger.warning("Caddy reload failed for %s", service_id, exc_info=True)
        raise ReconcileError(f"Caddy reload failed: Docker/edge unavailable: {e}") from e
    except Exception as e:
        # Anything else is unexpected, but still a reload failure: raise
        # ReconcileError so the reload-pending marker survives for the next retry.
        logger.warning("Caddy reload failed for %s", service_id, exc_info=True)
        raise ReconcileError(f"Caddy reload failed (unexpected): {e}") from e
    result["caddy_reloaded"] = True
    stage.reload_pending_path.unlink(missing_ok=True)
    # Record the fingerprint captured before the reload as the cert now loaded
    # into Caddy. The per-service reconcile lock (held for the whole reconcile)
    # keeps cert_path immutable from that read through this reload, so it equals
    # the on-disk cert; recording the pre-reload value also avoids ever persisting
    # a cert that landed after Caddy read the old one — which would suppress the
    # next reload and serve a stale cert. Durable atomic write: this marker is a
    # crash-desync guard, so a bare write_text (no fsync) could lose it.
    if stage.current_cert_fp is not None:
        atomic_write_text(stage.cert_state_path, stage.current_cert_fp)
    _persist_status(
        db,
        service_id,
        event={
            "kind": EventKind.CADDY_RELOADED,
            "message": f"Caddy reloaded for '{service_name}'",
        },
    )


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


def maybe_schedule_probe_retry(
    checks: dict, phase: str, service_id: str, socket_path: str | None
) -> None:
    """Best-effort: schedule a background HTTPS-probe retry when only the probe failed.

    Never raises into the caller: the periodic sweep re-runs health checks
    regardless, so a failure to START the retry thread (thread exhaustion, etc.)
    MUST NOT flip an already-committed reconcile outcome to failed. The
    ``probe_retry`` import is hoisted to module top so an unresolvable module
    surfaces loudly at startup instead of being silently swallowed here.
    """
    if not checks.get("https_probe_ok") and phase in ("warning", "error"):
        critical_ok = all(checks.get(check, False) for check in CRITICAL_CHECKS)
        if critical_ok:
            # Only the thread START is best-effort: schedule_probe_retry re-raises
            # if Thread.start() fails, and that must NOT corrupt the already-
            # committed status. The import lives at module top (validated at
            # startup), so it is deliberately OUTSIDE this try.
            try:
                probe_retry.schedule_probe_retry(service_id, socket_path)
            except Exception:
                logger.warning(
                    "Failed to schedule probe retry for %s",
                    service_id,
                    exc_info=True,
                )
