"""Certificate subchecks: on-disk presence and renewal-window expiry (AR18).

``settings_store`` and ``cert_manager`` are imported as modules (not their
symbols) so tests — here and in the reconciler suite, which drives the health
path through ``reconcile_service`` — can patch ``get_positive_int_setting`` /
``get_cert_expiry`` at the source module and have the patch take effect: the
attribute is resolved at call time rather than bound once at import.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app import settings_store
from app.certs import cert_manager
from app.timeutil import days_from_now

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.models.service import Service

logger = logging.getLogger(__name__)


def _check_cert_present(service: Service, certs_dir: str | Path) -> bool:
    d = Path(certs_dir) / service.hostname / "current"
    return (d / "fullchain.pem").exists() and (d / "privkey.pem").exists()


def _check_cert_not_expiring(
    service: Service, certs_dir: str | Path, renewal_window_days: int
) -> bool:
    cert_path = Path(certs_dir) / service.hostname / "current" / "fullchain.pem"
    if not cert_path.exists():
        return False
    try:
        expiry = cert_manager.get_cert_expiry(cert_path)
        if expiry is None:
            return False
        threshold = days_from_now(renewal_window_days)
        if threshold is None:
            # Window so large the threshold overflows the representable range;
            # no expiry can exceed it, so the cert reads as expiring — matching
            # the prior behavior where the OverflowError was caught as False.
            return False
        return expiry > threshold
    except Exception:
        return False


def _cert_not_expiring_subcheck(db: Session, service: Service, certs_dir: str | Path) -> bool:
    """``cert_not_expiring`` subcheck, resilient to a corrupt renewal-window setting.

    The renewal window comes from ``get_positive_int_setting``, which fails loud
    (raises ``ValueError``) on a corrupt stored value. That fail-loud must stay
    isolated to this one subcheck: a single corrupt *global* setting otherwise
    crashes ``run_health_checks`` outright, staling health for every service in
    the sweep. On a corrupt window we report the subcheck as failing — consistent
    with ``_check_cert_not_expiring`` returning ``False`` on any internal error.
    """
    try:
        window = settings_store.get_positive_int_setting(db, "cert_renewal_window_days")
    except ValueError:
        logger.warning(
            "cert_renewal_window_days is corrupt; reporting cert_not_expiring as "
            "failing until it is fixed",
            exc_info=True,
        )
        return False
    return _check_cert_not_expiring(service, certs_dir, window)
