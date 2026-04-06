"""Certificate management via lego ACME client.

Handles issuing, renewing, and inspecting TLS certificates using
DNS-01 challenge via Cloudflare. Cert files are written atomically
to per-service directories.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from cryptography import x509

logger = logging.getLogger(__name__)

# lego stores certs under .lego/certificates/ by default
LEGO_BINARY = "lego"


def _run_lego(
    args: list[str],
    cloudflare_token: str,
    lego_dir: Path,
) -> subprocess.CompletedProcess:
    """Run a lego command with Cloudflare DNS provider."""
    env = os.environ.copy()
    env["CF_DNS_API_TOKEN"] = cloudflare_token

    cmd = [
        LEGO_BINARY,
        "--path", str(lego_dir),
        "--dns", "cloudflare",
        *args,
    ]

    logger.info("Running lego: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )

    if result.returncode != 0:
        logger.error("lego failed (exit %d): %s", result.returncode, result.stderr)
        raise RuntimeError(f"lego failed: {result.stderr.strip()}")

    logger.info("lego succeeded: %s", result.stdout.strip()[:200])
    return result


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
        cert_dir: Target directory to write fullchain.pem + privkey.pem
        lego_dir: Working directory for lego (defaults to cert_dir parent / .lego)

    Returns:
        Path to the cert directory containing fullchain.pem and privkey.pem
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
) -> Path:
    """Renew an existing certificate.

    Args:
        hostname: FQDN to renew
        email: ACME account email
        cloudflare_token: Cloudflare API token
        cert_dir: Target directory for cert files
        lego_dir: lego working directory
        days: Renew if expiry is within this many days

    Returns:
        Path to the cert directory
    """
    if lego_dir is None:
        lego_dir = cert_dir.parent / ".lego"

    _run_lego(
        [
            "--domains", hostname,
            "--email", email,
            "--accept-tos",
            "renew",
            "--days", str(days),
        ],
        cloudflare_token=cloudflare_token,
        lego_dir=lego_dir,
    )

    lego_cert_dir = lego_dir / "certificates"
    lego_cert = lego_cert_dir / f"{hostname}.crt"
    lego_key = lego_cert_dir / f"{hostname}.key"

    if not lego_cert.exists() or not lego_key.exists():
        raise RuntimeError("lego renew did not produce expected cert files")

    _atomic_copy_certs(lego_cert, lego_key, cert_dir)

    return cert_dir


def _atomic_copy_certs(
    src_cert: Path,
    src_key: Path,
    dest_dir: Path,
) -> None:
    """Atomically copy cert and key files to the destination directory.

    Writes to temp files first, then renames both only after both writes succeed.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    fullchain_dest = dest_dir / "fullchain.pem"
    privkey_dest = dest_dir / "privkey.pem"
    fullchain_tmp = dest_dir / "fullchain.pem.tmp"
    privkey_tmp = dest_dir / "privkey.pem.tmp"

    try:
        # Write to temp files
        shutil.copy2(src_cert, fullchain_tmp)
        shutil.copy2(src_key, privkey_tmp)

        # Rename both atomically (as atomic as the OS allows)
        fullchain_tmp.replace(fullchain_dest)
        privkey_tmp.replace(privkey_dest)

        logger.info("Cert files written atomically to %s", dest_dir)
    except Exception:
        # Clean up temp files on failure
        fullchain_tmp.unlink(missing_ok=True)
        privkey_tmp.unlink(missing_ok=True)
        raise


def get_cert_expiry(cert_path: Path) -> datetime | None:
    """Parse a PEM certificate file and return its expiry datetime.

    Returns None if the file doesn't exist or can't be parsed.
    """
    if not cert_path.exists():
        return None

    try:
        cert_data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_data)
        return cert.not_valid_after_utc
    except Exception:
        logger.warning("Failed to parse cert at %s", cert_path, exc_info=True)
        return None
