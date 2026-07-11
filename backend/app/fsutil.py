"""Filesystem durability helpers shared across the backend."""

import contextlib
import os
import stat
import tempfile
from pathlib import Path


def fsync_directory(path: Path) -> None:
    """Best-effort flush of a directory entry so an atomic rename survives a crash.

    fsyncing the temp file durably stores its *contents*, but the rename that
    publishes it is only durable once the parent directory is itself synced.
    Best-effort: some filesystems reject directory fsync, which is harmless here.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        with contextlib.suppress(OSError):
            os.fsync(fd)
    finally:
        os.close(fd)


def fsync_file(path: Path) -> None:
    """Flush a file's contents to disk, propagating any OSError.

    Strict counterpart to :func:`fsync_directory`: durability-critical callers
    (atomic cert/config publishing) must learn if the sync fails rather than
    silently proceeding, so an OSError is allowed to propagate.
    """
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def fsync_directory_strict(path: Path) -> None:
    """Flush a directory entry, propagating any OSError.

    Strict variant of :func:`fsync_directory`: where the best-effort version
    swallows OSError (some filesystems reject directory fsync), this one raises
    so durability-critical publish paths can react to a failed sync.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(
    path: Path, data: bytes, *, mode: int = 0o600, fsync_dir: bool = True
) -> None:
    """Durably publish ``data`` to ``path`` via a temp-file swap.

    Owns the full atomic-write recipe every durability-critical caller would
    otherwise re-derive: create a temp file *in the target directory* (so the
    final rename never crosses a filesystem boundary), write + flush +
    ``fsync`` the contents, ``chmod`` to ``mode``, ``os.replace`` over the
    target (atomic), then ``fsync`` the parent directory so the rename itself
    is durable. On any failure the temp file is unlinked and the error is
    re-raised — a partial or leaked temp is never left behind.

    ``mode`` defaults to ``0o600`` (owner read/write) — the right default for
    secrets and internal state markers. The ``chmod`` is best-effort: an
    overlay/exotic FS that rejects it must not defeat the write, and
    ``mkstemp`` already creates the temp at ``0o600``. ``fsync_dir=False``
    skips the parent-directory sync for callers that don't need rename
    durability.
    """
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp_path, stat.S_IMODE(mode))
        os.replace(tmp_path, path)
        if fsync_dir:
            fsync_directory(path.parent)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int = 0o600,
    fsync_dir: bool = True,
) -> None:
    """Text wrapper over :func:`atomic_write_bytes` — encode, then publish.

    Same durability/permission contract as :func:`atomic_write_bytes`; the text
    is encoded with ``encoding`` (UTF-8 by default) before the atomic swap.
    """
    atomic_write_bytes(path, text.encode(encoding), mode=mode, fsync_dir=fsync_dir)
