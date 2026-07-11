"""Tests for filesystem durability helpers in app.fsutil.

The module exposes three primitives with two distinct error contracts:

* ``fsync_directory``        — best-effort, swallows OSError.
* ``fsync_directory_strict`` — strict, propagates OSError.
* ``fsync_file``             — strict, propagates OSError.

These tests pin the *semantic difference* (swallow vs. raise), which is the
whole reason both variants coexist.
"""

import os
import stat

import pytest

from app import fsutil


class TestFsyncFile:
    def test_syncs_real_file(self, tmp_path):
        target = tmp_path / "data.txt"
        target.write_text("contents")
        # Happy path: a real file must sync without raising.
        fsutil.fsync_file(target)

    def test_propagates_oserror_from_fsync(self, tmp_path, monkeypatch):
        target = tmp_path / "data.txt"
        target.write_text("contents")

        def boom(_fd):
            raise OSError("EIO")

        monkeypatch.setattr(fsutil.os, "fsync", boom)
        with pytest.raises(OSError, match="EIO"):
            fsutil.fsync_file(target)

    def test_propagates_open_error_for_missing_file(self, tmp_path):
        # Strict: opening a nonexistent file raises rather than silently no-op.
        with pytest.raises(OSError):
            fsutil.fsync_file(tmp_path / "does_not_exist.txt")


class TestFsyncDirectoryStrict:
    def test_syncs_real_directory(self, tmp_path):
        fsutil.fsync_directory_strict(tmp_path)

    def test_propagates_oserror_from_fsync(self, tmp_path, monkeypatch):
        def boom(_fd):
            raise OSError("EIO syncing dir")

        monkeypatch.setattr(fsutil.os, "fsync", boom)
        with pytest.raises(OSError, match="EIO syncing dir"):
            fsutil.fsync_directory_strict(tmp_path)

    def test_closes_fd_even_when_fsync_raises(self, tmp_path, monkeypatch):
        closed = []
        real_close = os.close

        def tracking_close(fd):
            closed.append(fd)
            real_close(fd)

        def boom(_fd):
            raise OSError("EIO")

        monkeypatch.setattr(fsutil.os, "fsync", boom)
        monkeypatch.setattr(fsutil.os, "close", tracking_close)
        with pytest.raises(OSError):
            fsutil.fsync_directory_strict(tmp_path)
        # The finally block must still close the descriptor it opened.
        assert closed, "directory fd was not closed after a failed fsync"


class TestFsyncDirectoryBestEffort:
    def test_syncs_real_directory(self, tmp_path):
        fsutil.fsync_directory(tmp_path)

    def test_swallows_oserror_from_fsync(self, tmp_path, monkeypatch):
        def boom(_fd):
            raise OSError("EIO syncing dir")

        monkeypatch.setattr(fsutil.os, "fsync", boom)
        # Best-effort: a rejected directory fsync must NOT propagate.
        fsutil.fsync_directory(tmp_path)

    def test_swallows_open_error_for_missing_directory(self, tmp_path):
        # Best-effort: an unopenable path is a harmless no-op, not an error.
        fsutil.fsync_directory(tmp_path / "does_not_exist")

    def test_closes_fd_after_successful_sync(self, tmp_path, monkeypatch):
        closed = []
        real_close = os.close

        def tracking_close(fd):
            closed.append(fd)
            real_close(fd)

        monkeypatch.setattr(fsutil.os, "close", tracking_close)
        fsutil.fsync_directory(tmp_path)
        assert closed, "directory fd was not closed after a successful fsync"


class TestAtomicWriteBytes:
    def test_publishes_contents(self, tmp_path):
        target = tmp_path / "data.bin"
        fsutil.atomic_write_bytes(target, b"payload")
        assert target.read_bytes() == b"payload"

    def test_overwrites_existing_atomically(self, tmp_path):
        target = tmp_path / "data.bin"
        target.write_bytes(b"old")
        fsutil.atomic_write_bytes(target, b"new")
        assert target.read_bytes() == b"new"

    def test_default_mode_is_owner_only_even_with_permissive_umask(self, tmp_path):
        target = tmp_path / "data.bin"
        old_umask = os.umask(0)
        try:
            fsutil.atomic_write_bytes(target, b"secret")
        finally:
            os.umask(old_umask)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_leaves_no_temp_file_behind(self, tmp_path):
        target = tmp_path / "data.bin"
        fsutil.atomic_write_bytes(target, b"x")
        leftovers = [p for p in tmp_path.iterdir() if p.name != "data.bin"]
        assert leftovers == []

    def test_fsyncs_parent_directory_by_default(self, tmp_path, monkeypatch):
        target = tmp_path / "data.bin"
        synced_inodes: list[int] = []
        monkeypatch.setattr(
            fsutil.os, "fsync", lambda fd: synced_inodes.append(os.fstat(fd).st_ino)
        )
        fsutil.atomic_write_bytes(target, b"x")
        assert tmp_path.stat().st_ino in synced_inodes, (
            "parent dir must be fsynced so the rename is durable"
        )

    def test_fsync_dir_false_skips_directory_sync(self, tmp_path, monkeypatch):
        target = tmp_path / "data.bin"
        synced_inodes: list[int] = []
        monkeypatch.setattr(
            fsutil.os, "fsync", lambda fd: synced_inodes.append(os.fstat(fd).st_ino)
        )
        fsutil.atomic_write_bytes(target, b"x", fsync_dir=False)
        assert tmp_path.stat().st_ino not in synced_inodes

    def test_cleans_up_temp_and_reraises_on_write_failure(self, tmp_path, monkeypatch):
        target = tmp_path / "data.bin"

        def boom(_fd):
            raise OSError("EIO during fsync")

        monkeypatch.setattr(fsutil.os, "fsync", boom)
        with pytest.raises(OSError):
            fsutil.atomic_write_bytes(target, b"x")
        # The failed write leaves neither the target nor a stray temp behind.
        assert not target.exists()
        assert list(tmp_path.iterdir()) == []

    def test_chmod_failure_still_publishes_the_write(self, tmp_path, monkeypatch):
        # chmod is best-effort: an exotic/overlay FS that rejects it must not
        # defeat the write. mkstemp already created the temp at 0o600, so the
        # published file stays owner-only even though the requested (broader)
        # mode was never applied.
        target = tmp_path / "data.bin"

        def _reject_chmod(*_a, **_k):
            raise OSError("EPERM: filesystem rejects chmod")

        monkeypatch.setattr(fsutil.os, "chmod", _reject_chmod)
        fsutil.atomic_write_bytes(target, b"payload", mode=0o644)
        assert target.read_bytes() == b"payload"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


class TestAtomicWriteText:
    def test_round_trips_utf8(self, tmp_path):
        target = tmp_path / "note.txt"
        fsutil.atomic_write_text(target, "héllo")
        assert target.read_text(encoding="utf-8") == "héllo"

    def test_honours_custom_mode(self, tmp_path):
        target = tmp_path / "note.txt"
        old_umask = os.umask(0)
        try:
            fsutil.atomic_write_text(target, "x", mode=stat.S_IRUSR | stat.S_IWUSR)
        finally:
            os.umask(old_umask)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
