"""Shared helpers for service lifecycle mutations.

The create/update/delete operations all need the same status transition,
post-response reconcile trigger, and destructive hostname teardown helpers. Keep
these non-route helpers here so each lifecycle module can stay cohesive without
reimplementing lock-sensitive behavior.
"""

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app import settings_store
from app.adapters import dns_reconciler
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler import reconcile_loop
from app.secrets import cloudflare_credentials

logger = logging.getLogger(__name__)

def mark_status_disabled(status: ServiceStatus, message: str) -> None:
    status.phase = "disabled"
    status.message = message
    status.health_checks = None  # Stale checks are misleading while offline
    status.probe_retry_at = None
    status.probe_retry_attempt = None


def reconcile_in_background(service_id: str, socket: str | None) -> None:
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


def teardown_hostname_resources(
    db: Session,
    svc: Service,
    hostname: str,
    *,
    cleanup_dns: bool,
    remove_cert_state: bool = True,
) -> None:
    """Best-effort destructive teardown of one hostname's external + on-disk state (AR5).

    Composes the teardown steps that ``_apply_hostname_change``,
    ``disable_service`` and ``_delete_service_record_locked`` each reassemble a
    subset of:

    1. Remove the Cloudflare DNS record (only when *cleanup_dns* and CF
       credentials are configured).
    2. Remove the served ``certs_dir/<hostname>`` tree.
    3. Remove lego's leftover per-hostname artifacts (SC2).

    Steps 2-3 (the on-disk cert state) run only when *remove_cert_state* is set.
    ``disable_service`` keeps a disabled service's cert so a later re-enable
    doesn't force a re-issue, so it passes ``remove_cert_state=False`` and uses
    only the DNS step.
    """
    if cleanup_dns:
        cf_token, zone_id = cloudflare_credentials(db)
        if cf_token and zone_id:
            dns_reconciler.cleanup_dns_record(db, svc, cf_token, zone_id)
    if remove_cert_state:
        certs_dir = Path(settings_store.get_runtime_paths(db)["certs_dir"])
        cert_dir = certs_dir / hostname
        if cert_dir.exists():
            shutil.rmtree(cert_dir, ignore_errors=True)
        _remove_lego_cert_artifacts(certs_dir, hostname)
