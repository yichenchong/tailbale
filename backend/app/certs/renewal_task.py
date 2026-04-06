"""Background task for periodic certificate renewal.

Scans all enabled services, checks cert expiry against the renewal window,
and issues/renews as needed. Results are persisted to the certificates table.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.certs.cert_manager import get_cert_expiry, issue_cert, renew_cert
from app.config import settings
from app.database import SessionLocal
from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.secrets import CLOUDFLARE_TOKEN, read_secret
from app.settings_store import get_setting

logger = logging.getLogger(__name__)

# Minimum retry interval after a failure (hours)
RETRY_INTERVAL_HOURS = 6


def _emit_event(
    db: Session,
    service_id: str | None,
    kind: str,
    level: str,
    message: str,
    details: dict | None = None,
) -> None:
    event = Event(
        service_id=service_id,
        kind=kind,
        level=level,
        message=message,
        details=json.dumps(details) if details else None,
    )
    db.add(event)


def _get_lego_dir() -> Path:
    return settings.certs_dir / ".lego"


def _get_cert_dir(hostname: str) -> Path:
    return settings.certs_dir / hostname


def process_service_cert(db: Session, svc: Service) -> None:
    """Check and issue/renew cert for a single service."""
    cf_token = read_secret(CLOUDFLARE_TOKEN)
    if not cf_token:
        logger.warning("Cloudflare token not configured, skipping cert for %s", svc.hostname)
        return

    acme_email = get_setting(db, "acme_email")
    renewal_window = int(get_setting(db, "cert_renewal_window_days") or "30")

    cert_dir = _get_cert_dir(svc.hostname)
    lego_dir = _get_lego_dir()
    fullchain_path = cert_dir / "fullchain.pem"

    # Get or create cert record
    cert_record = db.get(Certificate, svc.id)
    if not cert_record:
        cert_record = Certificate(service_id=svc.id, hostname=svc.hostname)
        db.add(cert_record)
        db.flush()

    # Check if we should skip due to recent failure
    now = datetime.now(timezone.utc)
    if cert_record.next_retry_at:
        retry_at = cert_record.next_retry_at
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if now < retry_at:
            logger.debug(
                "Skipping %s: next retry at %s", svc.hostname, cert_record.next_retry_at
            )
            return

    # Determine what action to take
    current_expiry = get_cert_expiry(fullchain_path)
    needs_issue = current_expiry is None
    needs_renew = False

    if current_expiry is not None:
        expiry_utc = current_expiry if current_expiry.tzinfo else current_expiry.replace(tzinfo=timezone.utc)
        if expiry_utc - now <= timedelta(days=renewal_window):
            needs_renew = True

    if not needs_issue and not needs_renew:
        # Cert exists and not expiring soon — update record and return
        cert_record.expires_at = current_expiry
        cert_record.last_failure = None
        return

    try:
        if needs_issue:
            logger.info("Issuing cert for %s", svc.hostname)
            issue_cert(svc.hostname, acme_email, cf_token, cert_dir, lego_dir)
            _emit_event(
                db, svc.id, "cert_issued", "info",
                f"Certificate issued for {svc.hostname}",
            )
        else:
            logger.info("Renewing cert for %s", svc.hostname)
            renew_cert(
                svc.hostname, acme_email, cf_token, cert_dir, lego_dir,
                days=renewal_window,
            )
            _emit_event(
                db, svc.id, "cert_renewed", "info",
                f"Certificate renewed for {svc.hostname}",
            )

        # Update cert record on success
        new_expiry = get_cert_expiry(cert_dir / "fullchain.pem")
        cert_record.expires_at = new_expiry
        cert_record.last_renewed_at = now
        cert_record.last_failure = None
        cert_record.next_retry_at = None

    except Exception as e:
        error_msg = str(e)[:500]
        logger.error("Cert operation failed for %s: %s", svc.hostname, error_msg)

        cert_record.last_failure = error_msg
        cert_record.next_retry_at = now + timedelta(hours=RETRY_INTERVAL_HOURS)

        _emit_event(
            db, svc.id, "cert_failed", "error",
            f"Certificate operation failed for {svc.hostname}: {error_msg}",
        )


def run_renewal_scan() -> int:
    """Scan all enabled services and issue/renew certs as needed.

    Returns the number of services processed.
    """
    db = SessionLocal()
    try:
        services = db.query(Service).filter(Service.enabled.is_(True)).all()
        processed = 0
        for svc in services:
            try:
                process_service_cert(db, svc)
                db.commit()
                processed += 1
            except Exception:
                db.rollback()
                logger.exception("Unexpected error processing cert for %s", svc.hostname)
        return processed
    finally:
        db.close()


async def cert_renewal_loop() -> None:
    """Async background loop that runs the renewal scan periodically.

    Runs once on startup, then every 24 hours (or as configured).
    """
    # Short initial delay to let the app start up
    await asyncio.sleep(10)

    while True:
        try:
            logger.info("Starting cert renewal scan")
            processed = await asyncio.to_thread(run_renewal_scan)
            logger.info("Cert renewal scan complete, processed %d services", processed)
        except Exception:
            logger.exception("Cert renewal scan failed")

        # Sleep for 24 hours (cert renewal doesn't need to be more frequent)
        await asyncio.sleep(86400)
