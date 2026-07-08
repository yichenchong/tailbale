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

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app.fsutil import fsync_directory_strict, fsync_file

logger = logging.getLogger(__name__)

# Name of the lego ACME client binary, resolved on PATH.
LEGO_BINARY = "lego"

# DNS-01 challenges require DNS propagation (lego polls for ~60s by default)
# plus ACME round-trips, so the subprocess wall-clock timeout must comfortably
# exceed that.
LEGO_TIMEOUT_SECONDS = 300
# Throttle ACME requests to 1/second. lego's own default is 18/s; we keep it
# low so a burst of renewals cannot trip Let's Encrypt's overall rate limit.
# The long LEGO_TIMEOUT_SECONDS leaves a single issuance ample room at this rate.
LEGO_OVERALL_REQUEST_LIMIT = 1

# A forced renewal must re-issue regardless of how far the cert is from expiry.
# lego's `renew` only acts when the cert expires within `--days` days, so a
# forced renewal passes a value larger than any cert lifetime to guarantee lego
# actually re-issues instead of silently no-opping. See renew_cert(force=...).
LEGO_FORCE_RENEW_DAYS = 36500  # ~100 years

# Every lego invocation shares one ACME account + cert store under the certs
# root (.lego/). Serialize them process-wide so concurrent per-service
# issuances cannot clobber the shared account file; see _run_lego.
_LEGO_MUTEX = threading.Lock()


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


def _exec_lego(
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

    # Bound the join like the timeout path above: if a descendant inherited the
    # stdout pipe and outlived lego, the reader's `for line in stdout` loop would
    # never see EOF and an unbounded join would hang the caller (the renewal
    # thread) forever. The process has already exited, so a clean drain is near
    # instant; the bound only matters in that pathological orphaned-pipe case.
    reader.join(timeout=10)
    output = "".join(collected[:])

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


def _atomic_copy_certs(
    src_cert: Path,
    src_key: Path,
    dest_dir: Path,
) -> None:
    """Publish a cert/key pair into *dest_dir* atomically via a generation swap.

    Each issuance is written into a fresh generation directory
    ``dest_dir/gen-<UTC-timestamp>-<uuid4hex>/{fullchain.pem,privkey.pem}`` and
    only published by atomically swapping a single relative ``current`` symlink
    to point at it. Because publication is one ``os.replace`` of one symlink,
    Caddy always reads ``current/fullchain.pem`` and ``current/privkey.pem``
    from the SAME generation: a crash (power loss/SIGKILL) can leave an extra,
    unreferenced generation dir behind but can NEVER expose a cert from one
    issuance beside a key from another. A failure BEFORE the swap discards the
    new generation and leaves the existing ``current`` symlink (if any)
    untouched; once the swap commits, the new generation is live and a failure
    in a trailing best-effort step never tears it back down.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    gen_name = (
        f"gen-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{uuid.uuid4().hex}"
    )
    gen_dir = dest_dir / gen_name
    gen_dir.mkdir()

    fullchain_dest = gen_dir / "fullchain.pem"
    privkey_dest = gen_dir / "privkey.pem"
    current_link = dest_dir / "current"
    tmp_link = dest_dir / (
        f".current.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )

    published = False
    try:
        shutil.copy2(src_cert, fullchain_dest)
        shutil.copy2(src_key, privkey_dest)
        # The private key must never be group/world readable regardless of the
        # mode lego (the copy source) happened to write or the active umask.
        os.chmod(privkey_dest, 0o600)
        fsync_file(fullchain_dest)
        fsync_file(privkey_dest)
        fsync_directory_strict(gen_dir)

        # Refuse to publish a corrupt or mismatched pair. Discarding the new
        # generation leaves any existing ``current`` symlink pointing at the last
        # good pair, so a corrupt copy never replaces a working cert with a
        # broken one. ``cert_key_pair_matches`` treats an UNPARSEABLE cert as
        # "nothing to verify" (it returns True so a genuinely absent/unreadable
        # served cert drives a re-issue instead of a redundant mismatch report);
        # that lenient contract is wrong at publish time, where an unparseable
        # fullchain is exactly the corruption this guard must reject. If it were
        # published, the swap would point ``current`` at a cert Caddy cannot load
        # AND the success-path prune would delete the last-good generation,
        # taking TLS down with no fallback. So verify the cert actually parses
        # (expiry readable) AND the key matches before committing the swap.
        if get_cert_expiry(fullchain_dest) is None:
            raise RuntimeError(
                f"Refusing to publish unparseable certificate in {gen_dir}"
            )
        if not cert_key_pair_matches(fullchain_dest, privkey_dest):
            raise RuntimeError(
                f"Refusing to publish mismatched cert/key pair in {gen_dir}"
            )

        # Publish atomically. Create the new symlink under a unique temp name
        # then os.replace it onto ``current`` — atomic on POSIX whether or not
        # ``current`` already exists. The target is the BARE generation name so
        # the link is RELATIVE and resolves inside the edge container's
        # read-only /certs bind mount (read_only blocks writes, not traversal).
        os.symlink(gen_name, tmp_link)
        os.replace(tmp_link, current_link)
        # os.replace is the commit point: ``current`` now resolves to this new
        # generation and the previous one (if any) is unreferenced. Everything
        # past here is best-effort durability/logging that must NEVER tear the
        # freshly-published pair back down.
        published = True
        fsync_directory_strict(dest_dir)

        logger.info("Cert generation %s published via %s", gen_name, current_link)
    except Exception:
        if published:
            # The swap already succeeded, so the new generation is LIVE. A
            # failure in the trailing best-effort fsync/logging must not delete
            # the served pair - rmtree-ing it here would leave ``current``
            # dangling and take TLS down. The publish stands.
            #
            # Crucially we ALSO skip the prune below: the failing step is the
            # dest-dir fsync that makes the ``current`` rename durable, so the
            # rename may still be only in page cache. Reaping the previous
            # generation now would let a crash that reverts the un-synced rename
            # land on a deleted ``gen-*`` -> dangling ``current`` -> TLS down.
            # Leaving the old generation in place means a revert lands on the
            # last good matching pair instead (seamless, no re-issue); the
            # leftover is reaped by the next successful publish.
            logger.warning(
                "Cert generation %s published but a post-publish step failed; "
                "leaving prior generations in place",
                gen_name,
                exc_info=True,
            )
            return
        else:
            # Pre-publish failure: discard the half-built generation; the old
            # ``current`` symlink (if any) still resolves to the last good pair.
            with contextlib.suppress(Exception):
                os.unlink(tmp_link)
            with contextlib.suppress(Exception):
                shutil.rmtree(gen_dir)
            raise

    # Best-effort: reclaim superseded generations and any stale staging symlinks.
    # Reached only on a fully durable publish (the dest-dir fsync above succeeded),
    # so the ``current`` rename is on disk before any old generation is reaped.
    _prune_old_generations(dest_dir, keep=gen_name)


def _prune_old_generations(dest_dir: Path, keep: str) -> None:
    """Reclaim superseded artifacts in *dest_dir* (best-effort).

    Runs only after a successful symlink swap, while the per-service reconcile
    lock serializes every write to this directory, so nothing here can race a
    live publish. Removes two kinds of leftovers:

    * sibling ``gen-*`` generation directories other than *keep* (the one the
      freshly-swapped ``current`` symlink now points at), and
    * stale ``.current.*.tmp`` staging symlinks that a hard crash (power
      loss/SIGKILL) could have left between the ``os.symlink``/``os.replace``
      pair in ``_atomic_copy_certs`` - otherwise they accumulate forever.

    The live ``current`` symlink, the kept generation, and any unrelated
    entries are never touched; every error is suppressed.
    """
    try:
        entries = list(dest_dir.iterdir())
    except OSError:
        return
    for entry in entries:
        name = entry.name
        if name == keep:
            continue
        if name.startswith("gen-"):
            with contextlib.suppress(Exception):
                shutil.rmtree(entry)
        elif name.startswith(".current.") and name.endswith(".tmp"):
            # A staging symlink (never a directory); unlink removes the link
            # itself without following it.
            with contextlib.suppress(Exception):
                entry.unlink()


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


def cert_key_pair_matches(cert_path: Path, key_path: Path) -> bool:
    """Return True if *cert_path*'s public key matches *key_path*'s private key.

    Defense-in-depth against a mismatched fullchain/privkey pair on disk.
    ``_atomic_copy_certs`` verifies the pair and publishes it via a single
    atomic ``current`` symlink swap, so a served pair is normally consistent;
    a mismatch detected here therefore signals on-disk corruption or external
    tampering. Caddy would otherwise serve a certificate whose key does not
    match and every TLS handshake would fail, with the expiry-based checks
    noticing nothing.

    Returns ``True`` when the pair matches OR when there is nothing to verify: the
    cert file is absent, or it is unreadable (in which case ``get_cert_expiry``
    already returns None and drives a re-issue, so reporting a mismatch too would
    be redundant). Returns ``False`` only when the cert is present and readable
    but the key is missing, unreadable, or carries a different public key.
    """
    if not cert_path.exists():
        return True
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        cert_pub = cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        return True
    if not key_path.exists():
        return False
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        key_pub = key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        return False
    return cert_pub == key_pub
