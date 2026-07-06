"""Certificate renewal decision for a single service.

Split out of the former ``service_ops`` god-module (AR1). Owns the operator-facing
"issue / renew / refuse" policy: skip disabled services, refuse a healthy far-from-
expiry cert unless forced, otherwise drive ``renewal_task.process_service_cert``.
Exceptions propagate to the router, which maps them to a generic 500.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app import secrets, settings_store
from app.certs import renewal_task
from app.models.certificate import Certificate
from app.models.service import Service
from app.timeutil import as_utc


def renew_cert(db: Session, svc: Service, *, force: bool) -> dict:
    """Decide whether to issue/renew *svc*'s certificate and act on it.

    A DISABLED service is offline and its cert is not served, and
    ``process_service_cert`` skips disabled services outright — so a renewal is
    reported as not performed rather than silently no-opping while claiming
    success. The same honesty applies when the Cloudflare API token is unset:
    ``process_service_cert`` cannot run the DNS-01 challenge and skips, so a
    renewal is reported as not performed. When the cert is healthy and far from
    expiry, renewing is refused (``needs_force``) unless *force* is set.
    Exceptions propagate to the router, which maps them to a generic 500 (logged
    on ``app.routers.services``).
    """
    # process_service_cert skips a disabled service entirely (renewal_task.py),
    # so issuing a renewal here would silently no-op while still reporting that a
    # cert was processed. Report honestly: there is nothing to renew until the
    # service is enabled.
    if not svc.enabled:
        return {
            "success": True,
            "performed": False,
            "needs_force": False,
            "message": (
                f"Service for {svc.hostname} is disabled; certificate renewal is "
                f"skipped. Enable the service first."
            ),
        }

    cert = db.get(Certificate, svc.id)
    far_healthy = False
    if cert is not None and cert.expires_at is not None and cert.last_failure is None:
        expires_at = cert.expires_at
        expires_utc = as_utc(expires_at)
        window_days = settings_store.get_positive_int_setting(db, "cert_renewal_window_days")
        # Schema bounds cert_renewal_window_days at write, but a legacy/directly-set
        # huge value would overflow `timedelta(days=window_days)` and 500 this
        # manual-renew endpoint. An unbounded window means "renew eagerly", so an
        # overflow is correctly NOT far_healthy (fall through to a real renewal).
        try:
            far_healthy = expires_utc - datetime.now(UTC) > timedelta(days=window_days)
        except OverflowError:
            far_healthy = False

    if far_healthy and not force:
        return {
            "success": True,
            "performed": False,
            "needs_force": True,
            "message": (
                f"Certificate for {svc.hostname} is healthy and not near expiry "
                f"(expires {cert.expires_at.date().isoformat()}); forcing a renewal "
                f"now contacts Let's Encrypt and may hit rate limits."
            ),
        }

    # A real issue/renew is about to run. process_service_cert skips outright when
    # the Cloudflare API token is unset — it cannot run the DNS-01 challenge, so it
    # logs a warning and returns without issuing anything (renewal_task.py). Calling
    # it anyway would no-op while this function still reports `performed: True` /
    # "Certificate processed", exactly the dishonesty the disabled-service guard
    # above avoids. Report the missing prerequisite so the operator knows why
    # nothing happened. (The healthy/needs_force paths above never touch lego, so
    # they stay honest without a token.)
    if not secrets.read_secret(secrets.CLOUDFLARE_TOKEN):
        return {
            "success": True,
            "performed": False,
            "needs_force": False,
            "message": (
                f"Cloudflare API token is not configured; certificate renewal for "
                f"{svc.hostname} is skipped. Configure the Cloudflare token first."
            ),
        }

    renewal_task.process_service_cert(db, svc, force=True)
    cert = db.get(Certificate, svc.id)
    return {
        "success": True,
        "performed": True,
        "needs_force": False,
        "message": f"Certificate processed for {svc.hostname}",
        "expires_at": cert.expires_at.isoformat() if cert and cert.expires_at else None,
        "last_failure": cert.last_failure if cert else None,
    }
