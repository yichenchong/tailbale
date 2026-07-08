"""Service CRUD: create / update / disable / delete + response mapping + slugs.

Split out of the former ``service_ops`` god-module (AR1). Holds the service-row
mutators — the service-lifecycle acquirers of the tier-1
``_SERVICE_LIFECYCLE_MUTEX`` (the service-less orphan-DNS cleanup retry in
``routers/jobs.py`` and the developer reset sweep in ``routers/settings.py`` also
take it, always tier-1 first) — plus the slug derivation and ``to_response``
shaping the router reuses.

Lock acquisition order is preserved verbatim (tier-1 lifecycle mutex FIRST, then
the tier-2 per-service reconcile lock, then the tier-3 DB write lock via
``db_write_section``); see :mod:`app.locks` and ``test_reconcile_locking.py``.

Symbols the test suite patches at their *source* module (``app.edge.*``,
``app.secrets``, ``app.adapters.dns_reconciler``, ``app.reconciler.reconcile_loop``,
``app.settings_store``) are imported as modules and called by attribute so a
``patch("app.edge.container_manager.remove_edge")`` still resolves at call time.
"""

import contextlib
import hashlib
import logging
import re
import shutil
from pathlib import Path

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app import secrets, settings_store
from app.adapters import dns_reconciler
from app.database import commit_with_lock, db_write_section, flush_with_lock
from app.edge import container_manager, network_manager
from app.edge.docker_client import resolve_socket
from app.events.event_emitter import emit_event
from app.locks import (
    _SERVICE_LIFECYCLE_MUTEX,
    forget_reconcile_lock,
    lifecycle_then_reconcile,
)
from app.models.certificate import Certificate
from app.models.dns_record import DnsRecord
from app.models.job import Job
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler import reconcile_loop
from app.schemas.services import ServiceResponse, ServiceStatusResponse
from app.services.errors import (
    HostnameChangeError,
    HostnameInUse,
    HostnameSuffixInvalid,
    ServiceNotFound,
)
from app.timeutil import iso

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "service"


# Tailscale passes ts_hostname to `tailscale up --hostname=`, which is a single
# DNS label limited to 63 chars (RFC 1035 §3.1). Longer values are silently
# truncated/rejected, so the live MagicDNS hostname would diverge from the
# persisted ts_hostname — risking collisions and cert-hostname confusion.
# ts_hostname is f"edge-{slug}" (5-char prefix), so the slug must stay within
# 63 - len("edge-") = 58 chars.
_MAX_SLUG_LEN = 63 - len("edge-")  # 58
# Cap the *base* slug at a round 50 chars — comfortably below _MAX_SLUG_LEN and
# leaving 8 chars of headroom for the "-{n}" uniqueness suffix appended on
# collisions, so even suffixed slugs stay within the 58-char budget.
_MAX_BASE_SLUG_LEN = 50


def _unique_slug(db: Session, name: str) -> str:
    """Return a slug derived from *name* that doesn't collide with existing edge
    names, capped so the derived ts_hostname stays within the DNS-label limit."""
    base = _slugify(name)[:_MAX_BASE_SLUG_LEN].rstrip("-") or "service"
    slug = base
    suffix = 2
    while (
        db.query(Service)
        .filter(
            (Service.edge_container_name == f"edge_{slug}")
            | (Service.network_name == f"edge_net_{slug}")
            | (Service.ts_hostname == f"edge-{slug}")
        )
        .first()
    ):
        marker = f"-{suffix}"
        # Trim the base further so a multi-digit suffix can't overflow the budget.
        slug = f"{base[: _MAX_SLUG_LEN - len(marker)].rstrip('-')}{marker}"
        suffix += 1
    return slug


def to_response(
    svc: Service,
    status: ServiceStatus | None,
    cert: Certificate | None = None,
) -> ServiceResponse:
    """Shape a service (+ optional status/cert) into its API response model.

    Public service-layer API (AR4): the router reuses this for list / get so the
    N+1-batched shaping lives in one place.
    """
    status_resp = None
    if status:
        status_resp = ServiceStatusResponse(
            phase=status.phase,
            message=status.message,
            tailscale_ip=status.tailscale_ip,
            edge_container_id=status.edge_container_id,
            last_reconciled_at=iso(status.last_reconciled_at),
            health_checks=status.health_checks,
            cert_expires_at=iso(cert.expires_at if cert else None),
            probe_retry_at=iso(status.probe_retry_at),
            probe_retry_attempt=status.probe_retry_attempt,
            last_probe_at=iso(status.last_probe_at),
        )
    return ServiceResponse(
        id=svc.id,
        name=svc.name,
        enabled=svc.enabled,
        upstream_container_id=svc.upstream_container_id,
        upstream_container_name=svc.upstream_container_name,
        upstream_scheme=svc.upstream_scheme,
        upstream_port=svc.upstream_port,
        healthcheck_path=svc.healthcheck_path,
        hostname=svc.hostname,
        base_domain=svc.base_domain,
        edge_container_name=svc.edge_container_name,
        network_name=svc.network_name,
        ts_hostname=svc.ts_hostname,
        preserve_host_header=svc.preserve_host_header,
        custom_caddy_snippet=svc.custom_caddy_snippet,
        app_profile=svc.app_profile,
        status=status_resp,
        created_at=svc.created_at.isoformat(),
        updated_at=svc.updated_at.isoformat(),
    )


def _mark_status_disabled(status: ServiceStatus, message: str) -> None:
    status.phase = "disabled"
    status.message = message
    status.health_checks = None  # Stale checks are misleading while offline
    status.probe_retry_at = None
    status.probe_retry_attempt = None


def _reconcile_in_background(service_id: str, socket: str | None) -> None:
    """Run a one-off reconcile for *service_id* in a fresh session.

    Used as a fire-and-forget background task after create / enable /
    hostname-change so the service converges without waiting for the periodic
    sweep. The result is not consumed, and any failure — most realistically the
    service being deleted in the race window before this task runs, which makes
    reconcile_one raise ValueError — MUST NOT escape as an unhandled server
    exception (the HTTP response was already sent). Mirror reconcile_all's
    defensive logging.
    """
    try:
        reconcile_loop.spawn_reconcile(service_id, socket)
    except Exception:
        logger.error("Background reconcile failed for service %s", service_id, exc_info=True)


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

        slug = _unique_slug(db, body.name)
        svc = Service(
            name=body.name,
            enabled=body.enabled,
            upstream_container_id=body.upstream_container_id,
            upstream_container_name=body.upstream_container_name,
            upstream_scheme=body.upstream_scheme,
            upstream_port=body.upstream_port,
            healthcheck_path=body.healthcheck_path,
            hostname=body.hostname,
            base_domain=configured_domain,
            edge_container_name=f"edge_{slug}",
            network_name=f"edge_net_{slug}",
            ts_hostname=f"edge-{slug}",
            preserve_host_header=body.preserve_host_header,
            custom_caddy_snippet=body.custom_caddy_snippet,
            app_profile=body.app_profile,
        )
        db.add(svc)
        flush_with_lock(db)  # Generate ID

        status_phase = "pending" if svc.enabled else "disabled"
        status_message = (
            "Awaiting first reconciliation" if svc.enabled else "Service is disabled"
        )
        status = ServiceStatus(service_id=svc.id, phase=status_phase, message=status_message)
        db.add(status)
        emit_event(db, svc.id, "service_created", f"Service '{svc.name}' created for {svc.hostname}")
        if svc.custom_caddy_snippet:
            snippet = svc.custom_caddy_snippet
            emit_event(
                db,
                svc.id,
                "service_snippet_changed",
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
        background_tasks.add_task(_reconcile_in_background, svc.id, resolve_socket(db))

    return to_response(svc, status)


def _remove_lego_cert_artifacts(certs_dir: Path, hostname: str) -> None:
    """Best-effort removal of lego's per-hostname cert artifacts (SC2).

    lego publishes each cert under ``<certs_dir>/.lego/certificates/`` as
    ``<hostname>.crt``, ``<hostname>.key``, ``<hostname>.json`` and
    ``<hostname>.issuer.crt`` (see ``cert_manager.issue_cert``). Deleting a
    service or changing its hostname strands those files, so remove them
    alongside the served ``certs_dir/<hostname>`` tree.

    Best-effort by design: a failure here MUST NOT fail the delete/hostname
    change, and leaving stale files behind is safe because
    ``cert_manager.renew_cert`` falls back to a fresh issue whenever this lego
    state is missing.
    """
    lego_cert_dir = certs_dir / ".lego" / "certificates"
    for suffix in (".crt", ".key", ".json", ".issuer.crt"):
        artifact = lego_cert_dir / f"{hostname}{suffix}"
        try:
            artifact.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove lego cert artifact %s", artifact, exc_info=True)


def _apply_hostname_change(db: Session, svc: Service, body, service_id: str) -> dict:
    """Validate a hostname change, tear down the old hostname's DNS + cert state.

    Runs inside the lifecycle+reconcile lock but BEFORE the DB write section: all
    validation (``HostnameInUse``, ``HostnameSuffixInvalid``) happens before any
    destructive op, preserving the validate-before-teardown discipline. The
    Cloudflare DNS teardown and cert-dir cleanup only run once the request is
    known valid; a teardown failure raises ``HostnameChangeError`` and the caller
    aborts before persisting anything. Returns the ``hostname``/``base_domain``
    field changes to apply.
    """
    existing = db.query(Service).filter(
        Service.hostname == body.hostname, Service.id != service_id
    ).first()
    if existing:
        raise HostnameInUse(body.hostname)

    configured_domain = settings_store.get_setting(db, "base_domain")
    if not configured_domain or not body.hostname.endswith(f".{configured_domain}"):
        raise HostnameSuffixInvalid(body.hostname, configured_domain)

    old_hostname = svc.hostname

    cf_token = secrets.read_secret(secrets.CLOUDFLARE_TOKEN)
    zone_id = settings_store.get_setting(db, "cf_zone_id")
    existing_dns = db.get(DnsRecord, svc.id)
    has_live_record = existing_dns and existing_dns.record_id

    if cf_token and zone_id:
        result = dns_reconciler.cleanup_dns_record(db, svc, cf_token, zone_id)
        if result["error"]:
            raise HostnameChangeError(
                (
                    f"Cannot change hostname: failed to remove old DNS record "
                    f"from Cloudflare ({result['error']}). "
                    f"Retry or remove the record manually first."
                ),
                status_code=502,
            )
    elif has_live_record:
        raise HostnameChangeError(
            (
                "Cannot change hostname: a Cloudflare DNS record exists for "
                f"'{old_hostname}' but Cloudflare credentials are not configured. "
                "Configure cf_token and cf_zone_id, or manually delete the old "
                "DNS record first."
            ),
            status_code=422,
        )

    runtime = settings_store.get_runtime_paths(db)
    old_cert_dir = Path(runtime["certs_dir"]) / old_hostname
    if old_cert_dir.exists():
        shutil.rmtree(old_cert_dir, ignore_errors=True)
    # SC2: also drop the leftover .lego per-hostname cert artifacts for the old
    # hostname so the shared .lego store doesn't accumulate dead state.
    _remove_lego_cert_artifacts(Path(runtime["certs_dir"]), old_hostname)

    # base_domain always tracks the configured domain (validated above).
    return {"hostname": body.hostname, "base_domain": configured_domain}


def _apply_field_changes(body) -> dict:
    """Collect the non-hostname field edits present in the request body."""
    changes: dict = {}
    for field in (
        "name", "upstream_scheme", "upstream_port", "healthcheck_path",
        "enabled", "preserve_host_header", "custom_caddy_snippet", "app_profile",
    ):
        if field in body.model_fields_set:
            changes[field] = getattr(body, field)
    return changes


def _transition_status(
    status: ServiceStatus | None,
    *,
    disabling_service: bool,
    enabling_service: bool,
    changing_hostname: bool,
    enabled: bool,
) -> None:
    """Reflect the update in the service's status row (if present)."""
    if status is None:
        return
    if disabling_service:
        _mark_status_disabled(status, "Service disabled by user")
    elif enabling_service:
        status.phase = "pending"
        status.message = "Awaiting reconciliation after enable"
        status.health_checks = None
        status.probe_retry_at = None
        status.probe_retry_attempt = None
    elif changing_hostname and enabled:
        # A hostname change tears down the old DNS record + cert dir and forces
        # the edge container to be recreated below, so the service is being
        # re-provisioned. Reflect that rather than leaving a stale "healthy"
        # carrying checks/cert from the old hostname.
        status.phase = "pending"
        status.message = "Awaiting reconciliation after hostname change"
        status.health_checks = None
        status.probe_retry_at = None
        status.probe_retry_attempt = None


def _emit_update_events(
    db: Session,
    svc: Service,
    changes: dict,
    *,
    snippet_in_update: bool,
    old_snippet: str | None,
) -> None:
    """Emit the generic update event plus a dedicated snippet-delta audit event."""
    if changes:
        # The raw custom_caddy_snippet is an admin-injected Caddy-config / SSRF
        # tamper vector; the dedicated service_snippet_changed event below records
        # its delta as sha256+len rather than persisting the raw text into the
        # audit log. Mirror that here so the generic update event never duplicates
        # the raw snippet (which would also bloat the event log with a large
        # snippet on every edit that includes it).
        audit_changes = changes
        if "custom_caddy_snippet" in changes:
            audit_changes = {
                k: ("<redacted: see service_snippet_changed>" if k == "custom_caddy_snippet" else v)
                for k, v in changes.items()
            }
        emit_event(db, svc.id, "service_updated", f"Service '{svc.name}' updated", details=audit_changes)

    if snippet_in_update:
        new_snippet = svc.custom_caddy_snippet
        if (old_snippet or "") != (new_snippet or ""):
            if not (old_snippet or ""):
                action = "set"
            elif not (new_snippet or ""):
                action = "cleared"
            else:
                action = "changed"
            emit_event(
                db,
                svc.id,
                "service_snippet_changed",
                f"Custom Caddy snippet {action} for '{svc.name}'",
                level="warning",
                details={
                    "action": action,
                    "new_len": len(new_snippet or ""),
                    "new_sha256": (
                        hashlib.sha256(new_snippet.encode()).hexdigest()
                        if new_snippet
                        else None
                    ),
                },
            )


def _schedule_post_update_reconcile(
    db: Session,
    background_tasks: BackgroundTasks,
    service_id: str,
    *,
    disabling_service: bool,
    enabling_service: bool,
    changing_hostname: bool,
    config_changed: bool,
    enabled: bool,
) -> None:
    """Schedule an immediate reconcile when the update needs prompt convergence.

    A hostname change is destructive — it deleted the old Cloudflare DNS record
    and cert directory above — so without an immediate reconcile the service
    would stay unreachable (no DNS record, no cert for the new hostname) until
    the next periodic loop. Enabling a previously-disabled service likewise needs
    to converge promptly, and a config-affecting edit (port, scheme, preserve-
    host, snippet) must re-render the Caddyfile and reload Caddy — or, for
    healthcheck_path, re-run the health probe — now instead of waiting up to an
    hour. Skip while disabling (the edge was just stopped and there is nothing to
    bring up).
    """
    if not disabling_service and (
        enabling_service or ((changing_hostname or config_changed) and enabled)
    ):
        background_tasks.add_task(_reconcile_in_background, service_id, resolve_socket(db))


def update_service(
    db: Session,
    service_id: str,
    body,
    background_tasks: BackgroundTasks,
) -> ServiceResponse:
    """Apply a service update under the lifecycle/reconcile locks.

    Orchestrates the cohesive helpers above: validate + tear down a hostname
    change, collect field edits, persist everything inside the DB write section,
    then finish the post-commit edge/reconcile work OUTSIDE it. The caller
    (router) has already revalidated the upstream port *before* taking any lock,
    so the destructive DNS/cert teardown only runs after a valid request —
    preserving the validate-before-teardown discipline.
    """
    with lifecycle_then_reconcile(service_id):
        svc = db.get(Service, service_id, populate_existing=True)
        if not svc:
            raise ServiceNotFound()

        sent = body.model_fields_set
        was_enabled = svc.enabled
        changing_hostname = "hostname" in sent and body.hostname != svc.hostname

        changes: dict = {}
        if changing_hostname:
            changes.update(_apply_hostname_change(db, svc, body, service_id))
        changes.update(_apply_field_changes(body))

        disabling_service = "enabled" in changes and changes["enabled"] is False and was_enabled
        enabling_service = "enabled" in changes and changes["enabled"] is True and not was_enabled

        with db_write_section(db):
            svc = db.get(Service, service_id, populate_existing=True)
            if not svc:
                raise ServiceNotFound()

            if changing_hostname:
                existing = db.query(Service).filter(
                    Service.hostname == body.hostname, Service.id != service_id
                ).first()
                if existing:
                    raise HostnameInUse(body.hostname)

            # Capture the pre-change snippet so we can emit a dedicated,
            # high-visibility audit event on any snippet delta (tamper vector).
            snippet_in_update = "custom_caddy_snippet" in sent
            old_snippet = svc.custom_caddy_snippet if snippet_in_update else None

            # Detect whether any config-affecting field actually changed so we can
            # schedule an immediate reconcile below. Computed against the
            # pre-change svc values, so it MUST run before the setattr loop.
            config_changed = any(
                field in changes and changes[field] != getattr(svc, field)
                for field in (
                    "upstream_port", "upstream_scheme", "preserve_host_header",
                    "custom_caddy_snippet", "healthcheck_path",
                )
            )

            for field, val in changes.items():
                setattr(svc, field, val)

            if changing_hostname:
                cert = db.get(Certificate, svc.id)
                if cert:
                    cert.hostname = body.hostname
                    cert.expires_at = None
                    cert.last_renewed_at = None
                    cert.last_failure = None
                    cert.next_retry_at = None

                dns_record = db.get(DnsRecord, svc.id)
                if dns_record:
                    dns_record.hostname = body.hostname

            status = db.get(ServiceStatus, svc.id)
            _transition_status(
                status,
                disabling_service=disabling_service,
                enabling_service=enabling_service,
                changing_hostname=changing_hostname,
                enabled=svc.enabled,
            )

            _emit_update_events(
                db, svc, changes, snippet_in_update=snippet_in_update, old_snippet=old_snippet
            )

            commit_with_lock(db)

        db.refresh(svc)

        if disabling_service:
            with contextlib.suppress(Exception):
                container_manager.stop_edge(svc.id, svc.edge_container_name, resolve_socket(db))

        # A hostname change moves the per-hostname cert directory, but the edge
        # container's /certs bind mount (certs_dir/<hostname>) is fixed at create
        # time. The stale container would keep mounting the now-deleted old
        # hostname's cert dir and never see the new cert, so remove it; the
        # reconcile scheduled below (or the next enable) recreates it with the
        # correct mount. Best-effort: Docker may be unreachable.
        if changing_hostname:
            with contextlib.suppress(Exception):
                container_manager.remove_edge(svc.id, svc.edge_container_name, resolve_socket(db), delete_device=False)

        _schedule_post_update_reconcile(
            db,
            background_tasks,
            service_id,
            disabling_service=disabling_service,
            enabling_service=enabling_service,
            changing_hostname=changing_hostname,
            config_changed=config_changed,
            enabled=svc.enabled,
        )

        status = db.get(ServiceStatus, service_id)
        cert = db.get(Certificate, service_id)
        return to_response(svc, status, cert)


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
                _mark_status_disabled(status, "Service disabled by user")
            emit_event(db, svc.id, "service_disabled", f"Service '{svc.name}' disabled")
            commit_with_lock(db)

        # Best-effort: stop the edge container so it stops serving traffic
        with contextlib.suppress(Exception):
            container_manager.stop_edge(svc.id, svc.edge_container_name, socket)

        # Optionally clean up the DNS record (spec §7.4)
        if cleanup_dns:
            cf_token = secrets.read_secret(secrets.CLOUDFLARE_TOKEN)
            zone_id = settings_store.get_setting(db, "cf_zone_id")
            if cf_token and zone_id:
                dns_reconciler.cleanup_dns_record(db, svc, cf_token, zone_id)
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

    if cleanup_dns:
        cf_token = secrets.read_secret(secrets.CLOUDFLARE_TOKEN)
        zone_id = settings_store.get_setting(db, "cf_zone_id")
        if cf_token and zone_id:
            dns_reconciler.cleanup_dns_record(db, svc, cf_token, zone_id)

    runtime = settings_store.get_runtime_paths(db)
    for subdir in [
        Path(runtime["generated_dir"]) / svc.id,
        Path(runtime["certs_dir"]) / svc.hostname,
        Path(runtime["tailscale_state_dir"]) / svc.edge_container_name,
    ]:
        if subdir.exists():
            shutil.rmtree(subdir, ignore_errors=True)

    # SC2: drop lego's leftover per-hostname cert artifacts alongside the served
    # cert dir removed above, so the shared .lego store doesn't accumulate dead
    # state for deleted services.
    _remove_lego_cert_artifacts(Path(runtime["certs_dir"]), svc.hostname)

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
                db, svc.id, "dns_orphan_created",
                f"DNS cleanup job created for orphaned record '{surviving_dns.hostname}'",
                level="warning",
            )

        name = svc.name
        service_id = svc.id
        db.delete(svc)
        emit_event(db, None, "service_deleted", f"Service '{name}' ({service_id}) deleted")
        commit_with_lock(db)
