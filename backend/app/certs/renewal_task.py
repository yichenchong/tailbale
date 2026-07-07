"""Background task for periodic certificate renewal.

Scans all enabled services, checks cert expiry against the renewal window,
and issues/renews as needed. Results are persisted to the certificates table.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app import locks, settings_store
from app.backoff import run_periodic
from app.certs.cert_manager import (
    cert_key_pair_matches,
    get_cert_expiry,
    issue_cert,
    renew_cert,
)
from app.database import (
    commit_with_lock,
    db_write_section,
    flush_with_lock,
    rollback_with_lock,
    session_scope,
)
from app.events.event_emitter import emit_event
from app.models.certificate import Certificate
from app.models.service import Service
from app.secrets import CLOUDFLARE_TOKEN, read_secret
from app.settings_store import get_positive_int_setting, get_setting
from app.timeutil import as_utc, days_from_now

logger = logging.getLogger(__name__)

# Fixed retry interval after a per-cert failure. In the app.backoff vocabulary
# this is a fixed cadence (``base == cap``, no exponential growth) — kept as a
# plain hours constant because it is stored as a timedelta on next_retry_at,
# not slept on directly.
RETRY_INTERVAL_HOURS = 6


def _get_certs_root(db: Session) -> Path:
    """Resolve the certs root directory from DB settings, falling back to config."""
    return Path(settings_store.get_runtime_paths(db)["certs_dir"])


def process_service_cert(db: Session, svc: Service, *, force: bool = False) -> None:
    """Check and issue/renew cert for a single service.

    Background callers (the renewal scan / reconcile loop) invoke this with
    ``force=False`` and get today's behavior: skip while inside the per-cert
    failure backoff, and noop when a healthy pair is far from expiry. With
    ``force=True`` (manual endpoint) the backoff and healthy-noop early returns
    are bypassed so a real issue/renew always happens.
    """
    # Certificate issuance/renewal reads service identity, performs filesystem
    # and lego/Cloudflare I/O, then persists Certificate/Event rows tied to the
    # service.  Serialize it (per service) with reconcile/delete so a concurrent delete cannot
    # cascade the service-owned rows while this function continues with stale
    # ORM objects after network I/O.
    with locks.service_reconcile_lock(svc.id):
        _process_service_cert_locked(db, svc, force=force)


def _process_service_cert_locked(db: Session, svc: Service, *, force: bool = False) -> None:
    fresh = db.get(Service, svc.id, populate_existing=True)
    if fresh is None:
        logger.info("Skipping cert processing for deleted service %s", svc.id)
        return
    if not fresh.enabled:
        logger.info("Skipping cert processing for disabled service %s", svc.id)
        return
    svc = fresh

    cf_token = read_secret(CLOUDFLARE_TOKEN)
    if not cf_token:
        logger.warning("Cloudflare token not configured, skipping cert for %s", svc.hostname)
        return

    acme_email = get_setting(db, "acme_email")
    renewal_window = get_positive_int_setting(db, "cert_renewal_window_days")

    certs_root = _get_certs_root(db)
    cert_dir = certs_root / svc.hostname
    lego_dir = certs_root / ".lego"
    fullchain_path = cert_dir / "current" / "fullchain.pem"
    privkey_path = cert_dir / "current" / "privkey.pem"

    cert_record = db.get(Certificate, svc.id)
    if not cert_record:
        with db_write_section(db):
            cert_record = db.get(Certificate, svc.id)
            if not cert_record:
                cert_record = Certificate(service_id=svc.id, hostname=svc.hostname)
                db.add(cert_record)
                flush_with_lock(db)
                commit_with_lock(db)
    # Check if we should skip due to recent failure. A manual force renew
    # bypasses the backoff so the operator isn't stuck waiting out next_retry_at.
    now = datetime.now(UTC)
    if not force and cert_record.next_retry_at:
        retry_at = as_utc(cert_record.next_retry_at)
        if now < retry_at:
            logger.info(
                "Skipping %s: next cert retry at %s", svc.hostname, cert_record.next_retry_at
            )
            return

    # Determine what action to take
    current_expiry = get_cert_expiry(fullchain_path)
    needs_issue = current_expiry is None
    needs_renew = False

    if current_expiry is not None:
        expiry_utc = as_utc(current_expiry)
        # force renews regardless of how far off expiry is; background callers
        # only renew once inside the renewal window. A window so large it
        # overflows the representable range (days_from_now -> None) means the
        # cutoff is effectively infinite, so any real expiry is within it: renew.
        window_cutoff = days_from_now(renewal_window)
        if force or window_cutoff is None or expiry_utc <= window_cutoff:
            needs_renew = True

    if not needs_issue and not needs_renew:
        if cert_key_pair_matches(fullchain_path, privkey_path):
            with db_write_section(db):
                cert_record.expires_at = current_expiry
                cert_record.last_failure = None
                # The cert is healthy: drop any stale pending-retry marker so the
                # success state matches the issue/renew path (which also clears it).
                cert_record.next_retry_at = None
                commit_with_lock(db)
            return
        # The cert is unexpired but its private key does not match it. The atomic
        # single-symlink swap in _atomic_copy_certs verifies the pair before
        # publishing, so current/ normally holds a matching pair; a mismatch here
        # means on-disk corruption or external tampering. Caddy would serve an
        # unusable pair and the expiry check alone never notices, so force a
        # fresh issue to heal it.
        logger.warning(
            "Cert/key pair mismatch for %s; reissuing to repair", svc.hostname
        )
        needs_issue = True

    try:
        if needs_issue:
            logger.info("Issuing cert for %s", svc.hostname)
            issue_cert(svc.hostname, acme_email, cf_token, cert_dir, lego_dir)
            event_kind = "cert_issued"
            event_message = f"Certificate issued for {svc.hostname}"
        else:
            logger.info("Renewing cert for %s", svc.hostname)
            _, fresh_issued = renew_cert(
                svc.hostname, acme_email, cf_token, cert_dir, lego_dir,
                days=renewal_window, force=force,
            )
            if fresh_issued:
                event_kind = "cert_issued"
                event_message = f"Certificate issued for {svc.hostname}"
            else:
                event_kind = "cert_renewed"
                event_message = f"Certificate renewed for {svc.hostname}"

        new_expiry = get_cert_expiry(fullchain_path)
        if new_expiry is None:
            raise RuntimeError(
                f"Certificate operation for {svc.hostname} completed but produced an unreadable certificate"
            )
        with db_write_section(db):
            cert_record.expires_at = new_expiry
            cert_record.last_renewed_at = now
            cert_record.last_failure = None
            cert_record.next_retry_at = None
            emit_event(db, svc.id, event_kind, event_message, level="info")
            commit_with_lock(db)

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error("Cert operation failed for %s: %s", svc.hostname, error_msg)

        with db_write_section(db):
            cert_record.last_failure = error_msg
            cert_record.next_retry_at = now + timedelta(hours=RETRY_INTERVAL_HOURS)

            emit_event(
                db,
                svc.id,
                "cert_failed",
                f"Certificate operation failed for {svc.hostname}: {error_msg}",
                level="error",
            )
            commit_with_lock(db)


def run_renewal_scan() -> int:
    """Scan all enabled services and issue/renew certs as needed.

    Returns the number of services processed.
    """
    with session_scope() as db:
        services = db.query(Service).filter(Service.enabled.is_(True)).all()
        # Snapshot each hostname now, while the session is fresh. A concurrent
        # service delete expires these ORM instances; a later attribute access
        # (e.g. svc.hostname in the failure-log path, after rollback_with_lock
        # expires the session) would otherwise lazy-load a vanished row, raise,
        # and abort the entire scan - skipping every remaining service until the
        # next run a day later.
        targets = [(svc, svc.hostname) for svc in services]
        processed = 0
        for svc, hostname in targets:
            try:
                process_service_cert(db, svc)
                processed += 1
            except Exception:
                rollback_with_lock(db)
                logger.exception("Unexpected error processing cert for %s", hostname)
        return processed


async def cert_renewal_loop() -> None:
    """Async background loop that runs the renewal scan periodically.

    Runs once on startup, then every 24 hours.
    """

    async def _work() -> None:
        logger.info("Starting cert renewal scan")
        processed = await asyncio.to_thread(run_renewal_scan)
        logger.info("Cert renewal scan complete, processed %d services", processed)

    # 24h fixed cadence between scans (cert renewal needn't be more frequent); a
    # scan error backs off the same interval. A fixed cadence in the app.backoff
    # vocabulary (``base == cap``).
    await run_periodic(
        name="Cert renewal loop",
        startup_delay=10,
        interval_fn=lambda: 86400,
        work=_work,
        logger=logger,
    )
