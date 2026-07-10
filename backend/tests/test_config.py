"""Tests for application configuration helpers."""

import contextlib
import os
import stat
from pathlib import Path as _Path

import pytest
from pydantic import ValidationError

from app import config as config_module
from app.config import Settings, _load_or_create_jwt_secret


def test_generated_jwt_secret_is_owner_only(tmp_path):
    old_umask = os.umask(0)
    try:
        secret = _load_or_create_jwt_secret(tmp_path)
    finally:
        os.umask(old_umask)

    secret_path = tmp_path / "secrets" / ".jwt_secret"
    assert secret_path.read_text(encoding="utf-8") == secret
    assert stat.S_IMODE(secret_path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR


def test_generated_jwt_secret_fsyncs_parent_directory(tmp_path, monkeypatch):
    """The first-run secret write must fsync the secrets directory, not just the
    temp file, so the publishing rename survives a crash."""

    synced_inodes: list[int] = []

    def _spy_fsync(fd):
        synced_inodes.append(os.fstat(fd).st_ino)

    monkeypatch.setattr(config_module.os, "fsync", _spy_fsync)
    _load_or_create_jwt_secret(tmp_path)

    parent_inode = (tmp_path / "secrets").stat().st_ino
    assert parent_inode in synced_inodes, (
        "secrets directory must be fsynced so the atomic rename is durable"
    )


def test_ensure_dirs_locks_down_only_secrets_dir(tmp_path):
    """ensure_dirs must tighten secrets_dir to 0700 (so a local host user can't
    enumerate which secret files exist) while leaving the other data dirs at
    their umask-derived default (they may be mounted broader for Caddy)."""
    # umask 0 so mkdir would otherwise leave every dir world-listable (0777);
    # this proves the chmod is what locks secrets_dir down.
    old_umask = os.umask(0)
    try:
        settings = Settings(data_dir=tmp_path / "data")
        settings.ensure_dirs()
    finally:
        os.umask(old_umask)

    # Probe an independent dir to learn whether this FS honors chmod at all.
    # Only then is a skip warranted — otherwise a missing chmod in ensure_dirs
    # must fail this test, not silently skip it.
    probe = tmp_path / "chmod_probe"
    probe.mkdir()
    with contextlib.suppress(OSError):
        os.chmod(probe, 0o700)
    if stat.S_IMODE(probe.stat().st_mode) != 0o700:
        pytest.skip("filesystem does not honor chmod")

    secrets_mode = stat.S_IMODE(settings.secrets_dir.stat().st_mode)
    assert secrets_mode == 0o700

    # Other data dirs keep broader perms (group/other bits set) — untouched.
    for other in (
        settings.db_path.parent,
        settings.generated_dir,
        settings.certs_dir,
        settings.tailscale_state_dir,
    ):
        other_mode = stat.S_IMODE(other.stat().st_mode)
        assert other_mode & 0o077, (
            f"{other} should retain broader perms, got {other_mode:o}"
        )


def test_settings_tolerates_legacy_env_vars(monkeypatch):
    """Several legacy fields (base_domain/acme_email/reconcile_interval_seconds/
    cert_renewal_window_days/docker_socket) were removed from Settings (they are
    configured via the DB settings store). Existing .env files / environments may
    still set them; Settings() must ignore them rather than raise on startup."""
    for name, value in (
        ("BASE_DOMAIN", "legacy.example.com"),
        ("ACME_EMAIL", "legacy@example.com"),
        ("RECONCILE_INTERVAL_SECONDS", "120"),
        ("CERT_RENEWAL_WINDOW_DAYS", "45"),
        ("DOCKER_SOCKET", "unix:///legacy.sock"),
    ):
        monkeypatch.setenv(name, value)

    s = Settings()  # must not raise

    # The fields are gone and the legacy env vars are silently ignored.
    for removed in (
        "base_domain",
        "acme_email",
        "reconcile_interval_seconds",
        "cert_renewal_window_days",
        "docker_socket",
    ):
        assert not hasattr(s, removed)


class TestHostDataDirBlankIsUnset:
    """A blank HOST_DATA_DIR must resolve to None (unset), never Path('.').

    A bare ``HOST_DATA_DIR=`` in .env or a docker-compose ``${VAR:-}`` expansion
    of an unset variable both arrive as "". Pydantic would coerce that to
    ``Path('.')`` (non-None), which makes settings_store._host_path remap every
    Docker bind-mount source to a bogus RELATIVE path (e.g. "generated"), which
    the daemon treats as a named volume — silently breaking edge containers.
    """

    def test_empty_string_becomes_none(self, monkeypatch):
        monkeypatch.setenv("HOST_DATA_DIR", "")
        assert Settings().host_data_dir is None

    def test_whitespace_only_becomes_none(self, monkeypatch):
        monkeypatch.setenv("HOST_DATA_DIR", "   ")
        assert Settings().host_data_dir is None

    def test_real_path_is_preserved(self, monkeypatch):
        monkeypatch.setenv("HOST_DATA_DIR", "/mnt/host/data")
        assert Settings().host_data_dir == _Path("/mnt/host/data")

    def test_unset_defaults_to_none(self, monkeypatch):
        monkeypatch.delenv("HOST_DATA_DIR", raising=False)
        assert Settings().host_data_dir is None


def test_empty_persisted_jwt_secret_is_regenerated(tmp_path):
    """A persisted-but-empty/whitespace .jwt_secret (e.g. a truncated backup or
    a 0-byte volume mount) must be treated as missing and regenerated, never
    used as an empty HMAC key."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True)
    secret_path = secrets_dir / ".jwt_secret"
    secret_path.write_text("   \n\t  ", encoding="utf-8")

    secret = _load_or_create_jwt_secret(tmp_path)

    assert secret.strip(), "a fresh non-empty secret must be returned"
    assert secret_path.read_text(encoding="utf-8").strip() == secret


def test_existing_jwt_secret_is_returned(tmp_path):
    """An existing non-empty .jwt_secret must be read and returned as-is (the
    read-and-catch-FileNotFoundError path), not regenerated."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True)
    secret_path = secrets_dir / ".jwt_secret"
    secret_path.write_text("pre-existing-secret-value", encoding="utf-8")

    assert _load_or_create_jwt_secret(tmp_path) == "pre-existing-secret-value"


def test_existing_world_readable_jwt_secret_is_retightened_to_0600(tmp_path):
    """CI2: an existing .jwt_secret left world-readable by an older release (or a
    lax volume mount) must be re-tightened to owner-only 0600 on every startup,
    not merely read. The HMAC key must never stay readable to other host users."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True)
    secret_path = secrets_dir / ".jwt_secret"
    secret_path.write_text("pre-existing-secret-value", encoding="utf-8")
    os.chmod(secret_path, 0o644)  # legacy world-readable mode

    assert _load_or_create_jwt_secret(tmp_path) == "pre-existing-secret-value"
    assert stat.S_IMODE(secret_path.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR


def test_missing_jwt_secret_file_generates_one(tmp_path):
    """When no .jwt_secret exists yet, the missing-file read must fall through to
    generate a fresh non-empty secret rather than raising FileNotFoundError."""
    secret_path = tmp_path / "secrets" / ".jwt_secret"
    assert not secret_path.exists()

    secret = _load_or_create_jwt_secret(tmp_path)

    assert secret.strip()
    assert secret_path.read_text(encoding="utf-8").strip() == secret


def test_secrets_dir_locked_down_after_generating_jwt_secret(tmp_path):
    """_load_or_create_jwt_secret must chmod the secrets dir to 0700 the instant it
    creates it, so it is never world-listable in the window before ensure_dirs()."""
    old_umask = os.umask(0)
    try:
        _load_or_create_jwt_secret(tmp_path)
    finally:
        os.umask(old_umask)

    # Probe an independent dir to learn whether this FS honors chmod at all; only
    # then is a skip warranted -- otherwise a missing chmod must fail this test.
    probe = tmp_path / "chmod_probe"
    probe.mkdir()
    with contextlib.suppress(OSError):
        os.chmod(probe, 0o700)
    if stat.S_IMODE(probe.stat().st_mode) != 0o700:
        pytest.skip("filesystem does not honor chmod")

    secrets_dir = tmp_path / "secrets"
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700


def test_jwt_secret_read_toctou_falls_through_to_generate(tmp_path, monkeypatch):
    """If the secret file vanishes between a would-be existence check and the read
    (TOCTOU vs. a concurrent first-run writer/deleter), the read must catch
    FileNotFoundError and fall through to generate, never propagate it."""

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True)
    secret_path = secrets_dir / ".jwt_secret"
    secret_path.write_text("will-vanish", encoding="utf-8")

    real_read_text = _Path.read_text
    state = {"raised": False}

    def _vanishing_read_text(self, *args, **kwargs):
        if self == secret_path and not state["raised"]:
            state["raised"] = True
            raise FileNotFoundError(secret_path)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(_Path, "read_text", _vanishing_read_text)

    secret = _load_or_create_jwt_secret(tmp_path)

    assert state["raised"], "the simulated vanish must have been hit"
    assert secret.strip()
    assert secret_path.read_text(encoding="utf-8").strip() == secret


class TestJwtExpiryValidation:
    """``jwt_expiry_hours`` must be a positive integer.

    A 0/negative expiry makes ``create_access_token`` set ``exp == now`` (or in
    the past), so every issued token is already expired when ``decode_access_token``
    (which requires ``exp`` with zero leeway) validates it: login "succeeds" but
    hands back a dead cookie — a silent, total auth lockout. The knob feeds the
    cookie ``max_age`` too. Unlike the rate-limit knobs it has NO downstream
    ``max(1, ...)`` clamp, so the only guard is rejecting it at config load.
    """

    def test_zero_expiry_is_rejected(self, monkeypatch):
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "0")
        with pytest.raises(ValidationError):
            Settings()

    def test_negative_expiry_is_rejected(self, monkeypatch):
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "-3")
        with pytest.raises(ValidationError):
            Settings()

    def test_positive_expiry_is_accepted(self, monkeypatch):
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "12")
        assert Settings().jwt_expiry_hours == 12

    def test_default_expiry_is_positive(self, monkeypatch):
        monkeypatch.delenv("JWT_EXPIRY_HOURS", raising=False)
        assert Settings().jwt_expiry_hours == 24
