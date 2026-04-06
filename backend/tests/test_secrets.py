"""Tests for file-based secret storage."""

from app.secrets import (
    CLOUDFLARE_TOKEN,
    TAILSCALE_AUTH_KEY,
    delete_secret,
    get_secret_presence,
    read_secret,
    secret_exists,
    write_secret,
)


class TestSecretStorage:
    def test_write_and_read(self, tmp_data_dir):
        write_secret("test_key", "test_value")
        assert read_secret("test_key") == "test_value"

    def test_read_nonexistent(self, tmp_data_dir):
        assert read_secret("nonexistent") is None

    def test_secret_exists(self, tmp_data_dir):
        assert not secret_exists("test_key")
        write_secret("test_key", "value")
        assert secret_exists("test_key")

    def test_delete_secret(self, tmp_data_dir):
        write_secret("test_key", "value")
        assert delete_secret("test_key") is True
        assert not secret_exists("test_key")
        assert read_secret("test_key") is None

    def test_delete_nonexistent(self, tmp_data_dir):
        assert delete_secret("nonexistent") is False

    def test_overwrite_secret(self, tmp_data_dir):
        write_secret("test_key", "old_value")
        write_secret("test_key", "new_value")
        assert read_secret("test_key") == "new_value"

    def test_strips_whitespace(self, tmp_data_dir):
        write_secret("test_key", "  value_with_spaces  ")
        # read_secret strips
        assert read_secret("test_key") == "value_with_spaces"

    def test_get_secret_presence(self, tmp_data_dir):
        presence = get_secret_presence()
        assert CLOUDFLARE_TOKEN in presence
        assert TAILSCALE_AUTH_KEY in presence
        assert all(v is False for v in presence.values())

        write_secret(CLOUDFLARE_TOKEN, "cf_token_123")
        presence = get_secret_presence()
        assert presence[CLOUDFLARE_TOKEN] is True
        assert presence[TAILSCALE_AUTH_KEY] is False

    def test_atomic_write(self, tmp_data_dir):
        """Verify no .tmp files are left behind after write."""
        write_secret("test_key", "value")
        from app.config import settings
        tmp_files = list(settings.secrets_dir.glob("*.tmp"))
        assert len(tmp_files) == 0
