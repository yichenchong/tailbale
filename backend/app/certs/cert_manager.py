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

import contextlib as contextlib
import logging
import os as os
import shutil as shutil
import subprocess as subprocess
from pathlib import Path

from app.certs import lego_runner as _lego_runner
from app.certs import publish as _publish
from app.certs.inspect import cert_key_pair_matches, get_cert_expiry
from app.certs.lego_runner import (
    _LEGO_MUTEX,
    LEGO_BINARY,
    LEGO_FORCE_RENEW_DAYS,
    LEGO_OVERALL_REQUEST_LIMIT,
    LEGO_TIMEOUT_SECONDS,
    _format_lego_output,
    _kill_process_tree,
)
from app.fsutil import fsync_directory_strict, fsync_file

logger = logging.getLogger(__name__)

__all__ = [
    "LEGO_BINARY",
    "LEGO_FORCE_RENEW_DAYS",
    "LEGO_OVERALL_REQUEST_LIMIT",
    "LEGO_TIMEOUT_SECONDS",
    "_LEGO_MUTEX",
    "_atomic_copy_certs",
    "_exec_lego",
    "_format_lego_output",
    "_kill_process_tree",
    "_prune_old_generations",
    "_run_lego",
    "cert_key_pair_matches",
    "get_cert_expiry",
    "issue_cert",
    "renew_cert",
]


# Keep legacy cert_manager patch targets live while the implementation lives in
# focused modules. Tests and callers historically patch symbols such as
# app.certs.cert_manager._exec_lego, app.certs.cert_manager.subprocess.Popen,
# app.certs.cert_manager.os.replace, and app.certs.cert_manager.fsync_file.


def _exec_lego(
    args: list[str],
    cloudflare_token: str,
    lego_dir: Path,
) -> subprocess.CompletedProcess:
    _lego_runner.logger = logger
    _lego_runner.os = os
    _lego_runner.subprocess = subprocess
    _lego_runner.LEGO_BINARY = LEGO_BINARY
    _lego_runner.LEGO_TIMEOUT_SECONDS = LEGO_TIMEOUT_SECONDS
    _lego_runner.LEGO_OVERALL_REQUEST_LIMIT = LEGO_OVERALL_REQUEST_LIMIT
    _lego_runner._format_lego_output = _format_lego_output
    _lego_runner._kill_process_tree = _kill_process_tree
    return _lego_runner._exec_lego(args, cloudflare_token, lego_dir)


def _run_lego(
    args: list[str],
    cloudflare_token: str,
    lego_dir: Path,
) -> subprocess.CompletedProcess:
    """Serialize every lego invocation across the whole process.

    All services share one ACME account + cert store under ``lego_dir`` (the
    ``.lego/`` tree). Since reconcile locking went per-service, two services can
    issue/renew at once; concurrent first-issuance would have each lego register
    its own account key and clobber the shared account file. The mutex is held
    ONLY around the subprocess, so a fast steady-state reconcile (no lego call)
    never waits on it - only simultaneous issuances serialize, which is required
    for ``.lego`` safety regardless. Innermost lock: nothing acquires a
    per-service/lifecycle lock while holding it, so the order graph stays acyclic.
    """
    with _LEGO_MUTEX:
        return _exec_lego(args, cloudflare_token, lego_dir)


def _sync_publish_patch_targets() -> None:
    _publish.logger = logger
    _publish.contextlib = contextlib
    _publish.os = os
    _publish.shutil = shutil
    _publish.fsync_file = fsync_file
    _publish.fsync_directory_strict = fsync_directory_strict
    _publish.get_cert_expiry = get_cert_expiry
    _publish.cert_key_pair_matches = cert_key_pair_matches


def _atomic_copy_certs(
    src_cert: Path,
    src_key: Path,
    dest_dir: Path,
) -> None:
    _sync_publish_patch_targets()
    _publish._atomic_copy_certs(src_cert, src_key, dest_dir)


def _prune_old_generations(dest_dir: Path, keep: str) -> None:
    _sync_publish_patch_targets()
    _publish._prune_old_generations(dest_dir, keep=keep)


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
        fullchain.pem and privkey.pem (see ``_atomic_copy_certs``).
    """
    if lego_dir is None:
        lego_dir = cert_dir.parent / ".lego"
    lego_dir.mkdir(parents=True, exist_ok=True)

    _run_lego(
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
    _atomic_copy_certs(lego_cert, lego_key, cert_dir)

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
        _run_lego(
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

    _atomic_copy_certs(lego_cert, lego_key, cert_dir)

    return cert_dir, False
