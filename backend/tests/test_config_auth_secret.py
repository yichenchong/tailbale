"""JWT secret loading and healing behavior for configuration."""

import os
import stat
import subprocess
import sys
from pathlib import Path

from app.config import Settings, _load_or_create_jwt_secret


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


class TestConfigImportIsSideEffectFree:
    """AR12: constructing settings (what ``import app.config`` does) must perform
    NO filesystem writes; the JWT secret is created only by the explicit
    ``ensure_jwt_secret`` bootstrap invoked at app startup / in the test harness."""

    def test_settings_construction_writes_nothing(self, tmp_path):
        settings = Settings(data_dir=tmp_path, jwt_secret="")
        # No secret file and no secrets dir may be created just by building settings.
        assert not (tmp_path / "secrets" / ".jwt_secret").exists()
        assert not (tmp_path / "secrets").exists()
        assert settings.jwt_secret == ""

    def test_importing_app_config_creates_no_secret_until_bootstrap(self, tmp_path):
        # Import app.config in a FRESH interpreter with DATA_DIR at a temp path:
        # the import itself must write nothing, and only the explicit
        # ensure_jwt_secret() bootstrap may create the secret file + set the key.
        data_dir = tmp_path / "data"
        backend_dir = Path(__file__).resolve().parents[1]
        code = (
            "import os\n"
            "from pathlib import Path\n"
            "import app.config as c\n"
            "d = Path(os.environ['DATA_DIR'])\n"
            "assert not (d / 'secrets' / '.jwt_secret').exists(), 'import wrote secret file'\n"
            "assert not (d / 'secrets').exists(), 'import created secrets dir'\n"
            "assert c.settings.jwt_secret == '', 'import populated jwt_secret'\n"
            "c.ensure_jwt_secret()\n"
            "assert (d / 'secrets' / '.jwt_secret').exists(), 'bootstrap did not create secret'\n"
            "assert c.settings.jwt_secret, 'bootstrap did not set secret'\n"
            "print('OK')\n"
        )
        env = {**os.environ, "DATA_DIR": str(data_dir), "JWT_SECRET": ""}
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(backend_dir),
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "OK" in result.stdout
