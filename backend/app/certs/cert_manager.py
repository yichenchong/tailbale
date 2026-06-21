"""Certificate management via lego ACME client.

Handles issuing, renewing, and inspecting TLS certificates using
DNS-01 challenge via Cloudflare. Cert files are written atomically
to per-service directories.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path

from cryptography import x509

logger = logging.getLogger(__name__)

# lego stores certs under .lego/certificates/ by default
LEGO_BINARY = "lego"

# DNS-01 challenges require DNS propagation (lego polls for ~60s by default)
# plus ACME round-trips, so the subprocess wall-clock timeout must comfortably
# exceed that.
LEGO_TIMEOUT_SECONDS = 300
# Throttle ACME requests to 1/second. lego's own default is 18/s; we keep it
# low so a burst of renewals cannot trip Let's Encrypt's overall rate limit.
# The long LEGO_TIMEOUT_SECONDS leaves a single issuance ample room at this rate.
LEGO_OVERALL_REQUEST_LIMIT = 1


def _format_lego_output(output: str | None, limit: int = 500) -> str:
    if not output:
        return ""
    collapsed = " ".join(output.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """SIGKILL the lego process and its entire process group.

    ``proc.kill()`` only signals the immediate child, so a descendant that
    inherited the stdout pipe would keep it open and block the output reader
    (and this call) far past ``LEGO_TIMEOUT_SECONDS``.  The group is created
    via ``start_new_session=True`` on the Popen below; killing it closes the
    pipe promptly so the wall-clock timeout is actually honored.
    """
    # Only signal a real OS process group. A non-int pid (a test double, or a
    # process that never started) must never reach os.killpg: os.getpgid would
    # coerce it to a small integer and os.killpg would then SIGKILL an
    # unrelated group — possibly this process's own group, killing the caller.
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0:
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.kill()


def _run_lego(
    args: list[str],
    cloudflare_token: str,
    lego_dir: Path,
) -> subprocess.CompletedProcess:
    """Run a lego command with the Cloudflare DNS provider.

    lego's output is streamed to the log line-by-line so a long-running
    issuance (DNS propagation alone can take a minute or more) produces live
    feedback for each ACME request instead of going silent until it finishes
    or times out.
    """
    env = os.environ.copy()
    env["CF_DNS_API_TOKEN"] = cloudflare_token

    cmd = [
        LEGO_BINARY,
        "--path", str(lego_dir),
        "--dns", "cloudflare",
        "--overall-request-limit", str(LEGO_OVERALL_REQUEST_LIMIT),
        *args,
    ]

    logger.info("Running lego: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        # Tolerate any non-UTF-8 bytes so a stray byte can't raise inside the
        # reader thread, kill the drainer, and deadlock lego on a full pipe.
        encoding="utf-8",
        errors="replace",
        env=env,
        # Own process group so a timeout can kill any descendant lego spawns.
        start_new_session=True,
    )

    collected: list[str] = []

    def _pump() -> None:
        # lego writes progress (each ACME request, DNS propagation polling) to
        # stderr, which is folded into stdout above; log each line as it lands.
        if proc.stdout is None:
            return
        for line in proc.stdout:
            collected.append(line)
            logger.info("[lego] %s", line.rstrip())

    reader = threading.Thread(target=_pump, name="lego-output", daemon=True)
    reader.start()

    try:
        proc.wait(timeout=LEGO_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(proc)
        # Bound the post-kill cleanup: a stray child that inherited the stdout
        # pipe could otherwise keep the reader blocked, defeating the timeout.
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        reader.join(timeout=10)
        # Snapshot defensively in case the reader did not fully drain within
        # the join window (slice copy is atomic under the GIL).
        output = _format_lego_output("".join(collected[:]))
        logger.error("lego timed out after %ss (output=%r)", LEGO_TIMEOUT_SECONDS, output)
        raise RuntimeError(
            f"lego timed out after {LEGO_TIMEOUT_SECONDS}s"
            + (f": {output}" if output else "")
        ) from exc

    reader.join()
    output = "".join(collected)

    if proc.returncode != 0:
        formatted = _format_lego_output(output)
        logger.error("lego failed (exit %d, output=%r)", proc.returncode, formatted)
        raise RuntimeError(f"lego failed: {formatted or 'unknown error'}")

    logger.info("lego succeeded")
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=output, stderr="")


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
    suffix = f".{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    fullchain_tmp = dest_dir / f"fullchain.pem{suffix}"
    privkey_tmp = dest_dir / f"privkey.pem{suffix}"
    fullchain_backup = dest_dir / f"fullchain.pem{suffix}.bak"
    privkey_backup = dest_dir / f"privkey.pem{suffix}.bak"
    fullchain_replaced = False
    privkey_replaced = False

    try:
        shutil.copy2(src_cert, fullchain_tmp)
        shutil.copy2(src_key, privkey_tmp)
        if fullchain_dest.exists():
            shutil.copy2(fullchain_dest, fullchain_backup)
        if privkey_dest.exists():
            shutil.copy2(privkey_dest, privkey_backup)

        fullchain_tmp.replace(fullchain_dest)
        fullchain_replaced = True
        privkey_tmp.replace(privkey_dest)
        privkey_replaced = True

        logger.info("Cert files written atomically to %s", dest_dir)
    except Exception:
        if fullchain_replaced:
            if fullchain_backup.exists():
                with contextlib.suppress(Exception):
                    fullchain_backup.replace(fullchain_dest)
            else:
                with contextlib.suppress(Exception):
                    fullchain_dest.unlink(missing_ok=True)
        if privkey_replaced:
            if privkey_backup.exists():
                with contextlib.suppress(Exception):
                    privkey_backup.replace(privkey_dest)
            else:
                with contextlib.suppress(Exception):
                    privkey_dest.unlink(missing_ok=True)
        raise
    finally:
        for path in (fullchain_tmp, privkey_tmp, fullchain_backup, privkey_backup):
            with contextlib.suppress(Exception):
                path.unlink(missing_ok=True)


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
