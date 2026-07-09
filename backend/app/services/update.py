"""Service update lifecycle operation."""

import contextlib
import hashlib

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app import settings_store
from app.adapters import dns_reconciler
from app.database import commit_with_lock, db_write_section
from app.edge import container_manager
from app.edge.docker_client import resolve_socket
from app.events.event_emitter import emit_event
from app.locks import lifecycle_then_reconcile
from app.models.certificate import Certificate
from app.models.dns_record import DnsRecord
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.schemas.services import ServiceResponse
from app.secrets import cloudflare_credentials
from app.services.errors import (
    HostnameChangeError,
    HostnameInUse,
    HostnameSuffixInvalid,
    ServiceNotFound,
)
from app.services.lifecycle import (
    _mark_status_disabled,
    _reconcile_in_background,
    _teardown_hostname_resources,
)
from app.services.mapping import to_response


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

    cf_token, zone_id = cloudflare_credentials(db)
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

    # Drop the old hostname's on-disk cert state (served dir + SC2 lego
    # artifacts). The DNS teardown above stays inline: a hostname change must
    # ABORT on a Cloudflare failure, unlike the best-effort DNS cleanup the
    # helper performs for disable/delete.
    _teardown_hostname_resources(db, svc, old_hostname, cleanup_dns=False)

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
