"""Service disable/delete lifecycle operations."""

import contextlib
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app import settings_store
from app.database import commit_with_lock, db_write_section
from app.edge import container_manager, network_manager
from app.edge.docker_client import resolve_socket
from app.events.event_emitter import emit_event
from app.events.types import EventKind
from app.locks import forget_reconcile_lock, lifecycle_then_reconcile
from app.models.certificate import Certificate
from app.models.dns_record import DnsRecord
from app.models.job import Job
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.schemas.services import ServiceResponse
from app.services.errors import ServiceNotFound
from app.services.lifecycle import mark_status_disabled, teardown_hostname_resources
from app.services.mapping import to_response


def disable_service(db: Session, service_id: str, *, cleanup_dns: bool = False) -> ServiceResponse:
    """Disable a service without deleting it.

    cleanup_dns: If true, also remove the Cloudflare DNS record so the hostname
    stops resolving to the (now-stopped) Tailscale IP.
    """
    svc = db.get(Service, service_id)
    if not svc:
        raise ServiceNotFound()

    socket = resolve_socket(db)
    with lifecycle_then_reconcile(service_id):
        with db_write_section(db):
            svc = db.get(Service, service_id, populate_existing=True)
            if not svc:
                raise ServiceNotFound()
            svc.enabled = False

            # Update status to "disabled" so the UI doesn't show stale "healthy"
            # or an already-scheduled probe retry for an offline service.
            status = db.get(ServiceStatus, svc.id)
            if status:
                mark_status_disabled(status, "Service disabled by user")
            emit_event(db, svc.id, EventKind.SERVICE_DISABLED, f"Service '{svc.name}' disabled")
            commit_with_lock(db)

        # Best-effort: stop the edge container so it stops serving traffic
        with contextlib.suppress(Exception):
            container_manager.stop_edge(svc.id, svc.edge_container_name, socket)

        # Optionally clean up the DNS record (spec §7.4). A disabled service
        # keeps its cert dir for a later re-enable, so only the DNS step runs.
        teardown_hostname_resources(
            db, svc, svc.hostname, cleanup_dns=cleanup_dns, remove_cert_state=False
        )
    db.refresh(svc)
    status = db.get(ServiceStatus, service_id)
    cert = db.get(Certificate, service_id)
    return to_response(svc, status, cert)


def delete_service_record(db: Session, svc: Service, *, cleanup_dns: bool) -> None:
    """Delete one service and best-effort clean up attached resources."""

    # Capture the id before teardown: _delete_service_record_locked commits the
    # row delete, which expires this ORM instance under expire_on_commit, so a
    # later svc.id read could lazy-load a now-vanished row and raise.
    sid = svc.id

    # Deletion tears down the same Docker, DNS, filesystem, and status state
    # that reconciliation converges.  Serialize with service creation/reset and
    # reconcile_service(): the DB write lock alone only protects commits, not a
    # concurrent creator or reconciler thread continuing with stale ORM objects
    # after this transaction removes the row.
    with lifecycle_then_reconcile(sid):
        _delete_service_record_locked(db, svc, cleanup_dns=cleanup_dns)
        # Strictly POST-commit (the row delete is now durable) and still inside
        # the lifecycle mutex: drop the dead service's reconcile-lock entry so
        # the registry stays bounded by live + in-flight ids. Popping earlier
        # would let a concurrent creator/reconciler grab a fresh lock and race
        # the still-present row. The meta-lock is taken alone, so lock order is
        # unchanged.
        forget_reconcile_lock(sid)


def _delete_service_record_locked(db: Session, svc: Service, *, cleanup_dns: bool) -> None:
    # Re-read under the lifecycle+reconcile lock, mirroring update_service /
    # disable_service (both take populate_existing=True inside the lock). The
    # router loaded ``svc`` BEFORE this path acquired the lock, so a hostname
    # change that committed while we blocked would leave svc.hostname stale — and
    # the filesystem teardown below keys the served cert dir + lego artifacts off
    # it, silently leaking the CURRENT hostname's cert state (and re-removing the
    # already-gone old one). A racing delete that removed the row leaves nothing
    # to tear down.
    svc = db.get(Service, svc.id, populate_existing=True)
    if svc is None:
        return
    socket = resolve_socket(db)
    with contextlib.suppress(Exception):
        container_manager.remove_edge(svc.id, svc.edge_container_name, socket)
    with contextlib.suppress(Exception):
        network_manager.remove_network(svc.network_name, socket)

    # DNS record + this hostname's on-disk cert state (served dir + SC2 lego
    # artifacts), best-effort like the rest of delete. cleanup_dns gates only
    # the DNS step; the cert-state removal always runs.
    teardown_hostname_resources(db, svc, svc.hostname, cleanup_dns=cleanup_dns)

    runtime = settings_store.get_runtime_paths(db)
    for subdir in [
        Path(runtime["generated_dir"]) / svc.id,
        Path(runtime["tailscale_state_dir"]) / svc.edge_container_name,
    ]:
        if subdir.exists():
            shutil.rmtree(subdir, ignore_errors=True)

    with db_write_section(db):
        surviving_dns = db.get(DnsRecord, svc.id)
        if surviving_dns and surviving_dns.record_id:
            orphan_job = Job(
                service_id=svc.id,
                kind="dns_orphan_cleanup",
                status="pending",
                message=f"Orphaned DNS record for deleted service '{svc.name}'",
                details={
                    "record_id": surviving_dns.record_id,
                    "hostname": surviving_dns.hostname,
                    "zone_id": settings_store.get_setting(db, "cf_zone_id"),
                    "value": surviving_dns.value,
                    "service_name": svc.name,
                },
            )
            db.add(orphan_job)
            emit_event(
                db, svc.id, EventKind.DNS_ORPHAN_CREATED,
                f"DNS cleanup job created for orphaned record '{surviving_dns.hostname}'",
                level="warning",
            )

        name = svc.name
        service_id = svc.id
        db.delete(svc)
        emit_event(db, None, EventKind.SERVICE_DELETED, f"Service '{name}' ({service_id}) deleted")
        commit_with_lock(db)
