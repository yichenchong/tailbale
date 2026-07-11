"""Certificate management via lego ACME client.

Handles issuing, renewing, and inspecting TLS certificates using
DNS-01 challenge via Cloudflare. Each issuance is published into a fresh
per-service generation directory and made live by atomically swapping a
single ``current`` symlink, so the served fullchain/privkey pair always
comes from one issuance.

Error-signaling convention
--------------------------
HARD failures a caller must not silently continue past PROPAGATE as exceptions:
``issue_cert`` and the lego subprocess raise ``RuntimeError`` (lego exited
non-zero, or produced no cert/key files), so a caller never publishes a
missing/partial certificate.

DATA/decisions use return values by design: ``renew_cert`` returns
``(cert_dir, fresh_issued)`` where the bool is True when the renewal fell
back to a fresh issue (a normal outcome, not a failure) and False on a
successful in-place renewal, and ``get_cert_expiry``
returns ``Optional`` (``None`` == "couldn't determine expiry"). The
renew→fresh-issue fallback is deliberate best-effort recovery from lost lego
state, not error suppression.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.certs import lego_runner, publish
from app.certs.inspect import cert_key_pair_matches, get_cert_expiry
from app.certs.lego_runner import LEGO_FORCE_RENEW_DAYS

logger = logging.getLogger(__name__)

__all__ = [
    "LEGO_FORCE_RENEW_DAYS",
    "cert_key_pair_matches",
    "get_cert_expiry",
    "issue_cert",
    "renew_cert",
]


def issue_cert(
    hostname: str,
    email: str,
    cloudflare_token: str,
    cert_dir: Path,
    lego_dir: Path | None = None,
) -> Path:
    """Issue a new certificate for hostname via DNS-01 challenge.

    Args:
        hostname: FQDN to issue cert for
        email: ACME account email
        cloudflare_token: Cloudflare API token for DNS challenge
        cert_dir: Service cert directory; the pair is published under cert_dir/current/
        lego_dir: Working directory for lego (defaults to cert_dir parent / .lego)

    Returns:
        cert_dir, whose ``current`` symlink resolves to the published
        fullchain.pem and privkey.pem (see ``publish._atomic_copy_certs``).
    """
    if lego_dir is None:
        lego_dir = cert_dir.parent / ".lego"
    lego_dir.mkdir(parents=True, exist_ok=True)

    lego_runner._run_lego(
        [
            "--domains", hostname,
            "--email", email,
            "--accept-tos",
            "run",
        ],
        cloudflare_token=cloudflare_token,
        lego_dir=lego_dir,
    )

    # lego outputs certs under <lego_dir>/certificates/
    lego_cert_dir = lego_dir / "certificates"
    lego_cert = lego_cert_dir / f"{hostname}.crt"
    lego_key = lego_cert_dir / f"{hostname}.key"

    if not lego_cert.exists() or not lego_key.exists():
        raise RuntimeError(
            f"lego did not produce expected cert files: "
            f"cert={lego_cert.exists()}, key={lego_key.exists()}"
        )

    # Atomic write to service cert dir
    publish._atomic_copy_certs(lego_cert, lego_key, cert_dir)

    return cert_dir


def renew_cert(
    hostname: str,
    email: str,
    cloudflare_token: str,
    cert_dir: Path,
    lego_dir: Path | None = None,
    days: int = 30,
    *,
    force: bool = False,
) -> tuple[Path, bool]:
    """Renew an existing certificate.

    Args:
        hostname: FQDN to renew
        email: ACME account email
        cloudflare_token: Cloudflare API token
        cert_dir: Target directory for cert files
        lego_dir: lego working directory
        days: Renew if expiry is within this many days
        force: Force an actual re-issue regardless of expiry (manual renew).
            Background scans pass False and renew only within ``days`` of expiry.

    Returns:
        Tuple of (cert_dir, fresh_issued). fresh_issued is True when the renewal
        fell back to a fresh issue (missing lego state or a failed renew), and
        False on a successful in-place renewal.
    """
    if lego_dir is None:
        lego_dir = cert_dir.parent / ".lego"

    lego_cert_dir = lego_dir / "certificates"
    lego_cert = lego_cert_dir / f"{hostname}.crt"
    lego_key = lego_cert_dir / f"{hostname}.key"

    # lego's `renew` needs its own prior state — the ACME account and the
    # previously-issued cert under lego_dir. If that state is gone (e.g. the
    # .lego directory was wiped) while the served cert dir survived, `renew`
    # can never succeed: it fails every scan and the caller retries on a fixed
    # backoff forever, never recovering. Fall back to a fresh issue, which
    # recreates the account+cert and breaks the loop.
    if not lego_cert.exists() or not lego_key.exists():
        logger.warning(
            "lego state missing for %s; issuing a fresh certificate instead of renewing",
            hostname,
        )
        return issue_cert(hostname, email, cloudflare_token, cert_dir, lego_dir), True

    # A forced renewal must happen regardless of expiry. lego's `renew` only
    # acts when the cert expires within `--days`, so force passes a value far
    # larger than any cert lifetime to guarantee an actual re-issue; otherwise
    # `lego renew` silently no-ops and the "forced" renewal would republish the
    # same cert with an unchanged expiry.
    renew_days = LEGO_FORCE_RENEW_DAYS if force else days

    try:
        lego_runner._run_lego(
            [
                "--domains", hostname,
                "--email", email,
                "--accept-tos",
                "renew",
                "--days", str(renew_days),
            ],
            cloudflare_token=cloudflare_token,
            lego_dir=lego_dir,
        )
    except RuntimeError:
        # The cert files exist but `lego renew` still failed — most often the
        # ACME account state under lego_dir is gone (a partial .lego wipe), so
        # the file check above never fired yet every renew fails the same way
        # and the caller loops on its fixed backoff, never recovering. Fall
        # back to a fresh issue, which re-registers the account and re-issues.
        # A merely transient failure (DNS/rate limit) makes the issue fail the
        # same way and is retried on the normal backoff, so this is always safe.
        logger.warning(
            "lego renew failed for %s; falling back to a fresh issue", hostname
        )
        return issue_cert(hostname, email, cloudflare_token, cert_dir, lego_dir), True

    if not lego_cert.exists() or not lego_key.exists():
        raise RuntimeError("lego renew did not produce expected cert files")

    publish._atomic_copy_certs(lego_cert, lego_key, cert_dir)

    return cert_dir, False
