"""JWT secret loading and healing behavior for configuration."""

import os
import stat

from app.config import _load_or_create_jwt_secret


class TestJwtSecretLoading:
    """Persisted JWT secrets must never load as an empty HMAC key."""

    def test_whitespace_only_file_is_regenerated(self, tmp_path):
        secret_file = tmp_path / "secrets" / ".jwt_secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("   \n", encoding="utf-8")

        secret = _load_or_create_jwt_secret(tmp_path)
        assert secret, "must not return an empty/blank secret"
        # The corrupt file is healed in place with the returned secret.
        assert secret_file.read_text(encoding="utf-8").strip() == secret

    def test_zero_byte_file_is_regenerated(self, tmp_path):
        secret_file = tmp_path / "secrets" / ".jwt_secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("", encoding="utf-8")

        secret = _load_or_create_jwt_secret(tmp_path)
        assert secret
        assert secret_file.read_text(encoding="utf-8").strip() == secret

    def test_valid_secret_is_preserved(self, tmp_path):
        secret_file = tmp_path / "secrets" / ".jwt_secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("an-existing-real-secret", encoding="utf-8")

        assert _load_or_create_jwt_secret(tmp_path) == "an-existing-real-secret"

    def test_valid_secret_permissions_are_tightened(self, tmp_path):
        secret_file = tmp_path / "secrets" / ".jwt_secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("an-existing-real-secret", encoding="utf-8")
        os.chmod(secret_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

        assert _load_or_create_jwt_secret(tmp_path) == "an-existing-real-secret"
        assert stat.S_IMODE(secret_file.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR

    def test_missing_file_is_generated_and_persisted(self, tmp_path):
        secret = _load_or_create_jwt_secret(tmp_path)
        assert secret
        persisted = (tmp_path / "secrets" / ".jwt_secret").read_text(encoding="utf-8").strip()
        assert persisted == secret
