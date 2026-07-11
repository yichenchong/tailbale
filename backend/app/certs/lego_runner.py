"""lego ACME subprocess execution helpers."""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import threading
from pathlib import Path

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
# issuances cannot clobber the shared account file; see ``_run_lego`` below.
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
