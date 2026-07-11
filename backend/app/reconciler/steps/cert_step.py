"""Ensuring-cert step: (re)issue the cert when missing, expiring, or mismatched."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app import settings_store
from app.certs import cert_manager, renewal_task
from app.models.service import Service
from app.reconciler.status import _update_phase
from app.timeutil import as_utc, days_from_now


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
