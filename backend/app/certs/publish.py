"""Atomic certificate publishing helpers."""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.certs.inspect import cert_key_pair_matches, get_cert_expiry
from app.fsutil import fsync_directory_strict, fsync_file

logger = logging.getLogger(__name__)


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
