"""Tests for application configuration helpers."""

import os
import stat

from app.config import _load_or_create_jwt_secret


def test_generated_jwt_secret_is_owner_only(tmp_path):
    old_umask = os.umask(0)
    try:
        secret = _load_or_create_jwt_secret(tmp_path)
    finally:
        os.umask(old_umask)

    secret_path = tmp_path / "secrets" / ".jwt_secret"
    assert secret_path.read_text(encoding="utf-8") == secret
    assert stat.S_IMODE(secret_path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR
