"""Tests for file-based secret storage."""
import os
import stat

import pytest

from app.secrets import (
    delete_secret,
    read_secret,
    write_secret,
)


class TestSecretStorage:
    def test_write_and_read(self, tmp_data_dir):
        write_secret("test_key", "test_value")
        assert read_secret("test_key") == "test_value"

    def test_read_nonexistent(self, tmp_data_dir):
        assert read_secret("nonexistent") is None

    def test_delete_secret(self, tmp_data_dir):
        write_secret("test_key", "value")
        assert delete_secret("test_key") is True
        assert read_secret("test_key") is None

    def test_delete_nonexistent(self, tmp_data_dir):
        assert delete_secret("nonexistent") is False

    def test_delete_secret_race_returns_false(self, tmp_data_dir, monkeypatch):
        """If the file vanishes between callers (TOCTOU / multi-process delete),
        unlink raises FileNotFoundError; delete_secret must swallow it and
        return False rather than propagate the exception."""
        write_secret("test_key", "value")

        def _raise_gone(self, *args, **kwargs):
            raise FileNotFoundError

        monkeypatch.setattr("pathlib.Path.unlink", _raise_gone)
        assert delete_secret("test_key") is False

    def test_read_secret_race_returns_none(self, tmp_data_dir, monkeypatch):
        """If the file vanishes after the existence check (TOCTOU / concurrent
        delete), read_secret must swallow FileNotFoundError and return None
        rather than propagate it to the caller (mirrors delete_secret)."""
        write_secret("test_key", "value")

        def _raise_gone(self, *args, **kwargs):
            raise FileNotFoundError

        monkeypatch.setattr("pathlib.Path.read_text", _raise_gone)
        assert read_secret("test_key") is None

    def test_overwrite_secret(self, tmp_data_dir):
        write_secret("test_key", "old_value")
        write_secret("test_key", "new_value")
        assert read_secret("test_key") == "new_value"

    def test_strips_whitespace(self, tmp_data_dir):
        write_secret("test_key", "  value_with_spaces  ")
        # read_secret strips
        assert read_secret("test_key") == "value_with_spaces"

    def test_atomic_write(self, tmp_data_dir):
        """Verify no .tmp files are left behind after write."""
        write_secret("test_key", "value")
        from app.config import settings
        tmp_files = list(settings.secrets_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_write_fsyncs_parent_directory(self, tmp_data_dir, monkeypatch):
        """A durable atomic write must fsync the parent directory, not just the
        temp file — otherwise the publishing rename can be lost on a crash."""
        synced_inodes: list[int] = []

        def _spy_fsync(fd):
            synced_inodes.append(os.fstat(fd).st_ino)

        # write_secret now delegates the atomic swap to fsutil, which calls the
        # same os.fsync singleton; patch it directly.
        monkeypatch.setattr(os, "fsync", _spy_fsync)
        write_secret("test_key", "value")

        from app.config import settings
        parent_inode = settings.secrets_dir.stat().st_ino
        assert parent_inode in synced_inodes, (
            "parent directory must be fsynced so the atomic rename is durable"
        )

    def test_write_uses_owner_only_permissions_even_with_permissive_umask(self, tmp_data_dir):
        old_umask = os.umask(0)
        try:
            write_secret("test_key", "value")
        finally:
            os.umask(old_umask)

        from app.config import settings

        secret_path = settings.secrets_dir / "test_key"
        assert stat.S_IMODE(secret_path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR

    def test_rejects_secret_names_that_escape_secrets_dir(self, tmp_data_dir):
        for bad_name in ("../escape", "..", "/tmp/escape", "nested/secret", r"nested\secret"):
            with pytest.raises(ValueError):
                write_secret(bad_name, "value")
            with pytest.raises(ValueError):
                read_secret(bad_name)
            with pytest.raises(ValueError):
                delete_secret(bad_name)

        assert not (tmp_data_dir / "escape").exists()

    def test_rejects_hidden_dotfile_names(self, tmp_data_dir):
        """Hidden/dotfile names must be rejected so the secrets API can never
        read co-located private files such as the JWT signing key (.jwt_secret)
        or the atomic-write .tmp scratch files."""
        for bad_name in (".jwt_secret", ".hidden", ".tmp"):
            with pytest.raises(ValueError):
                read_secret(bad_name)
            with pytest.raises(ValueError):
                write_secret(bad_name, "value")
            with pytest.raises(ValueError):
                delete_secret(bad_name)
