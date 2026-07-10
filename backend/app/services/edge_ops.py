"""Edge-container orchestration and service-layer edge/health actions.

Split out of the former ``service_ops`` god-module (AR1). Holds the operations
that rebuild, reload, restart, query, and health-check a service's edge
container, plus ``get_enabled_service_for_edge_action`` (promoted from private
in AR4) that edge-action endpoints reuse to fetch + guard enabled-only actions.

Docker daemon failures raised by ``container_manager`` / ``image_builder``
propagate to the router, which maps them to 503 via the shared edge-action
mapping (``routers.services._raise_edge_failure``). Lock order is unchanged: the
per-service reconcile lock (tier 2) is taken alone here — a lifecycle op is not
in flight — then the tier-3 DB write lock via ``db_write_section``.
"""

import contextlib
import logging

from sqlalchemy.orm import Session

from app import secrets, settings_store
from app.database import commit_with_lock, db_write_section, session_scope
from app.edge import caddy_admin, container_manager, image_builder
from app.edge.docker_client import resolve_socket
from app.events.event_emitter import emit_event
from app.events.types import EventKind
from app.health import health_checker
from app.locks import service_reconcile_lock
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.services.errors import ServiceDisabled, ServiceNotFound, TailscaleAuthKeyMissing
from app.version import __version__

logger = logging.getLogger(__name__)


def get_enabled_service_for_edge_action(service_id: str, db: Session) -> Service:
    """Fetch *service_id* and require it exist and be enabled, or raise.

    Public service-layer API (AR4): the reload / restart / recreate / update-edge
    router endpoints reuse this to guard an edge action. Raises
    :class:`ServiceNotFound` (404) if absent, :class:`ServiceDisabled` (409) if
    the service is disabled.
    """
    svc = db.get(Service, service_id, populate_existing=True)
    if not svc:
        raise ServiceNotFound()
    if not svc.enabled:
        raise ServiceDisabled()
    return svc


def get_service_for_edge_query(service_id: str, db: Session) -> Service:
    """Fetch *service_id* for edge read/query operations without requiring enabled."""
    svc = db.get(Service, service_id)
    if not svc:
        raise ServiceNotFound()
    return svc


def reload_caddy_action(db: Session, service_id: str) -> dict:
    """Reload Caddy config inside an enabled service's edge container."""
    with service_reconcile_lock(service_id):
        svc = get_enabled_service_for_edge_action(service_id, db)
        output = caddy_admin.reload_caddy(svc.id, svc.edge_container_name, resolve_socket(db))
        with db_write_section(db):
            emit_event(db, svc.id, EventKind.CADDY_RELOADED, f"Caddy reloaded for '{svc.name}'")
            commit_with_lock(db)
        return {"success": True, "message": "Caddy config reloaded", "output": output}


def restart_edge_action(db: Session, service_id: str) -> dict:
    """Restart an enabled service's edge container."""
    with service_reconcile_lock(service_id):
        svc = get_enabled_service_for_edge_action(service_id, db)
        container_manager.restart_edge(svc.id, svc.edge_container_name, resolve_socket(db))
        with db_write_section(db):
            emit_event(db, svc.id, EventKind.EDGE_RESTARTED, f"Edge container restarted for '{svc.name}'")
            commit_with_lock(db)
    return {"success": True, "message": "Edge container restarted"}


def recreate_edge(db: Session, service_id: str) -> dict:
    """Destroy and recreate a service's edge container."""
    get_enabled_service_for_edge_action(service_id, db)

    ts_authkey = secrets.read_secret(secrets.TAILSCALE_AUTH_KEY)
    if not ts_authkey:
        raise TailscaleAuthKeyMissing()

    with service_reconcile_lock(service_id):
        # Re-fetch inside the mutex: a concurrent delete or disable (which
        # also holds this mutex) may have changed the service while we
        # waited. Acting on a disabled/stale object would bring it back
        # online or orphan a fresh edge container after deletion.
        svc = get_enabled_service_for_edge_action(service_id, db)
        runtime = settings_store.get_runtime_paths(db)
        container_id = container_manager.recreate_edge(
            svc, ts_authkey,
            runtime["host_generated_dir"], runtime["host_certs_dir"],
            runtime["host_tailscale_state_dir"],
            resolve_socket(db),
            ts_control_url=settings_store.get_setting(db, "ts_control_url"),
        )

        with db_write_section(db):
            # Update status with new container ID
            status = db.get(ServiceStatus, svc.id)
            if status:
                status.edge_container_id = container_id

            emit_event(db, svc.id, EventKind.EDGE_RECREATED, f"Edge container recreated for '{svc.name}'")
            commit_with_lock(db)
    return {"success": True, "message": "Edge container recreated", "container_id": container_id}


def update_edge_job(service_id: str, socket: str | None) -> dict:
    """Worker-thread body for the update-edge endpoint.

    Rebuilds the edge image if needed and recreates the container with a fresh
    session inside the per-service reconcile lock. Runs off the event loop via
    ``_run_edge_job`` in the router, which maps Docker failures to 503.
    """
    with session_scope() as thread_db, service_reconcile_lock(service_id):
        thread_svc = get_enabled_service_for_edge_action(service_id, thread_db)
        # Pre-check (off-loop): already at the target version?
        with contextlib.suppress(Exception):
            if container_manager.get_edge_version(thread_svc.id, thread_svc.edge_container_name, socket) == __version__:
                return {
                    "success": True,
                    "message": f"Edge container already at version {__version__}",
                    "version": __version__,
                }

        ts_authkey = secrets.read_secret(secrets.TAILSCALE_AUTH_KEY)
        if not ts_authkey:
            raise TailscaleAuthKeyMissing()

        runtime = settings_store.get_runtime_paths(thread_db)
        image_builder.ensure_edge_image(socket)
        container_id = container_manager.recreate_edge(
            thread_svc, ts_authkey,
            runtime["host_generated_dir"], runtime["host_certs_dir"],
            runtime["host_tailscale_state_dir"],
            socket,
            ts_control_url=settings_store.get_setting(thread_db, "ts_control_url"),
        )

        with db_write_section(thread_db):
            # Update status
            status = thread_db.get(ServiceStatus, service_id)
            if status:
                status.edge_container_id = container_id
            emit_event(
                thread_db, service_id, EventKind.EDGE_UPDATED,
                f"Edge container updated to v{__version__} for '{thread_svc.name}'",
            )
            commit_with_lock(thread_db)
        return {
            "success": True,
            "message": f"Edge container updated to version {__version__}",
            "version": __version__,
            "container_id": container_id,
        }


def get_edge_logs(db: Session, service_id: str, tail: int) -> dict:
    """Return edge-container logs for an existing service, even when disabled."""
    svc = get_service_for_edge_query(service_id, db)
    logs = container_manager.get_edge_logs(
        svc.id, svc.edge_container_name, tail=tail, socket_path=resolve_socket(db)
    )
    return {"logs": logs}


def full_health_check(db: Session, service_id: str) -> dict:
    """Run an extensive health check including live Cloudflare DNS verification.

    This is manual-only (not part of the reconcile loop) to avoid API rate limits.
    """
    svc = get_service_for_edge_query(service_id, db)
    runtime = settings_store.get_runtime_paths(db)

    socket = resolve_socket(db)
    # Standard checks run through the health layer.
    checks = health_checker.run_health_checks(
        db,
        svc,
        runtime["generated_dir"],
        runtime["certs_dir"],
        socket,
    )

    status = db.get(ServiceStatus, svc.id)
    # Verify live Cloudflare DNS against the service's LIVE Tailscale IP -- the
    # same one run_health_checks' live_dns path compares against internally -- not
    # the persisted ServiceStatus.tailscale_ip, which lags a tailnet IP change
    # until the next reconcile and would make a now-stale DNS record read as
    # matching. Fall back to the stored IP only when the edge/Docker is
    # unreachable, so a transient outage yields the best-known value rather than a
    # false negative.
    current_ip = health_checker.get_live_tailscale_ip(svc, socket)
    if current_ip is None and status is not None:
        current_ip = status.tailscale_ip
    dns_record_present, dns_matches_ip, extended = health_checker.check_live_dns(
        db, svc, current_ip,
    )
    checks["dns_record_present"] = dns_record_present
    checks["dns_matches_ip"] = dns_matches_ip

    return {
        "checks": checks,
        "extended": extended,
        "tailscale_ip": current_ip,
    }


def get_edge_version(db: Session, service_id: str) -> dict:
    """Return a service edge container's version compared with the orchestrator."""
    svc = get_service_for_edge_query(service_id, db)
    edge_version = None
    with contextlib.suppress(Exception):
        edge_version = container_manager.get_edge_version(
            svc.id, svc.edge_container_name, resolve_socket(db)
        )

    return {
        "orchestrator_version": __version__,
        "edge_version": edge_version,
        "up_to_date": edge_version == __version__,
    }
