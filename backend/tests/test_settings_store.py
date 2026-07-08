"""Tests for the settings key-value store."""

import pytest

from app.models.setting import Setting
from app.settings_store import (
    DEFAULTS,
    get_all_settings,
    get_positive_int_setting,
    get_setting,
    set_setting,
)


class TestSettingsStore:
    def test_get_default(self, db_session):
        assert get_setting(db_session, "base_domain") == "example.com"

    def test_get_unknown_key_returns_empty(self, db_session):
        assert get_setting(db_session, "totally_unknown") == ""

    def test_set_and_get(self, db_session):
        set_setting(db_session, "base_domain", "mysite.com")
        assert get_setting(db_session, "base_domain") == "mysite.com"

    def test_set_overwrites(self, db_session):
        set_setting(db_session, "base_domain", "first.com")
        set_setting(db_session, "base_domain", "second.com")
        assert get_setting(db_session, "base_domain") == "second.com"

        # Should still be one row, not two
        rows = db_session.query(Setting).filter_by(key="base_domain").all()
        assert len(rows) == 1

    def test_get_all_settings_defaults(self, db_session):
        result = get_all_settings(db_session)
        assert result["base_domain"] == "example.com"
        assert result["reconcile_interval_seconds"] == "3600"
        assert result["setup_complete"] == "false"

    def test_get_all_settings_with_overrides(self, db_session):
        set_setting(db_session, "base_domain", "custom.com")
        set_setting(db_session, "setup_complete", "true")
        result = get_all_settings(db_session)
        assert result["base_domain"] == "custom.com"
        assert result["setup_complete"] == "true"
        # Non-overridden keys still have defaults
        assert result["acme_email"] == DEFAULTS["acme_email"]

    def test_get_all_settings_excludes_internal_and_secret_keys(self, db_session):
        # get_all_settings must return only DEFAULTS (public) keys. Internal
        # rows stored in the same Setting table -- notably the password_salt
        # bcrypt pepper and the setup_user_claimed bootstrap marker -- must
        # never leak into the dict a caller might iterate/serialize.
        set_setting(db_session, "password_salt", "super-secret-pepper")
        set_setting(db_session, "setup_user_claimed", "true")
        set_setting(db_session, "base_domain", "custom.com")
        result = get_all_settings(db_session)
        assert "password_salt" not in result
        assert "setup_user_claimed" not in result
        assert set(result) == set(DEFAULTS)
        # Public keys still resolve (stored override + untouched default).
        assert result["base_domain"] == "custom.com"
        assert result["acme_email"] == DEFAULTS["acme_email"]

    def test_get_positive_int_setting_uses_stored_positive_value(self, db_session):
        set_setting(db_session, "reconcile_interval_seconds", "120")
        assert get_positive_int_setting(db_session, "reconcile_interval_seconds") == 120

    def test_get_positive_int_setting_returns_stored_digits(self, db_session):
        # A clean digit string is returned verbatim: writes are validated ge=1.
        set_setting(db_session, "event_retention_days", "7")
        assert get_positive_int_setting(db_session, "event_retention_days") == 7

    def test_get_positive_int_setting_unset_key_returns_default(self, db_session):
        # An unset key resolves to its DEFAULTS entry (all valid positive ints).
        assert get_positive_int_setting(db_session, "reconcile_interval_seconds") == 3600
        assert get_positive_int_setting(db_session, "health_check_interval_seconds") == 60
        assert get_positive_int_setting(db_session, "event_retention_days") == 30

    def test_get_positive_int_setting_raises_on_stored_zero(self, db_session):
        # Writes enforce ge=1, so a stored "0" is corruption: fail loud rather
        # than silently masking a non-positive interval with the default.
        set_setting(db_session, "reconcile_interval_seconds", "0")
        with pytest.raises(ValueError):
            get_positive_int_setting(db_session, "reconcile_interval_seconds")

    def test_get_positive_int_setting_raises_on_negative_value(self, db_session):
        set_setting(db_session, "reconcile_interval_seconds", "-5")
        with pytest.raises(ValueError):
            get_positive_int_setting(db_session, "reconcile_interval_seconds")

    def test_get_positive_int_setting_raises_on_non_integer(self, db_session):
        # A non-integer string could never have passed write validation.
        set_setting(db_session, "reconcile_interval_seconds", "not-an-int")
        with pytest.raises(ValueError):
            get_positive_int_setting(db_session, "reconcile_interval_seconds")

    def test_get_positive_int_setting_raises_on_non_ascii_digit(self, db_session):
        # str.isdigit() accepts superscript digits ("\u00b2") that int() rejects;
        # such corruption must raise, not silently fall back to the default.
        set_setting(db_session, "reconcile_interval_seconds", "\u00b2")
        with pytest.raises(ValueError):
            get_positive_int_setting(db_session, "reconcile_interval_seconds")


# ---------------------------------------------------------------------------
# DB-backed runtime paths
# ---------------------------------------------------------------------------


class TestRuntimePaths:
    def test_defaults_fall_back_to_config(self, db_session):
        from app.settings_store import get_runtime_paths

        paths = get_runtime_paths(db_session)
        assert "generated_dir" in paths
        assert "certs_dir" in paths
        assert "tailscale_state_dir" in paths
        for v in paths.values():
            assert v

    def test_db_overrides_config(self, db_session):
        from app.settings_store import get_runtime_paths, set_setting

        set_setting(db_session, "generated_root", "/custom/generated")
        set_setting(db_session, "cert_root", "/custom/certs")
        db_session.flush()

        paths = get_runtime_paths(db_session)
        assert paths["generated_dir"] == "/custom/generated"
        assert paths["certs_dir"] == "/custom/certs"

    def test_host_paths_same_as_internal_when_no_host_data_dir(self, db_session):
        """Without HOST_DATA_DIR, host paths equal internal paths."""
        from app.config import settings as app_settings
        from app.settings_store import get_runtime_paths

        original = app_settings.host_data_dir
        app_settings.host_data_dir = None
        try:
            paths = get_runtime_paths(db_session)
            assert paths["host_generated_dir"] == paths["generated_dir"]
            assert paths["host_certs_dir"] == paths["certs_dir"]
            assert paths["host_tailscale_state_dir"] == paths["tailscale_state_dir"]
        finally:
            app_settings.host_data_dir = original

    def test_host_paths_translated_when_host_data_dir_set(self, db_session):
        """With HOST_DATA_DIR, host paths replace data_dir prefix."""
        from pathlib import Path

        from app.config import settings as app_settings
        from app.settings_store import get_runtime_paths

        original_host = app_settings.host_data_dir
        original_data = app_settings.data_dir
        app_settings.data_dir = Path("/data")
        app_settings.host_data_dir = Path("/home/user/tailbale/data")
        try:
            paths = get_runtime_paths(db_session)
            data_str = str(Path("/data"))
            host_str = str(Path("/home/user/tailbale/data"))
            # Internal paths use data_dir
            assert paths["generated_dir"].startswith(data_str)
            # Host paths use the host_data_dir
            assert paths["host_generated_dir"].startswith(host_str)
            assert paths["host_certs_dir"].startswith(host_str)
            assert paths["host_tailscale_state_dir"].startswith(host_str)
            # The sub-path portion is preserved
            assert paths["host_generated_dir"].endswith("generated")
            assert paths["host_certs_dir"].endswith("certs")
            assert paths["host_tailscale_state_dir"].endswith("tailscale")
        finally:
            app_settings.data_dir = original_data
            app_settings.host_data_dir = original_host

    def test_host_paths_do_not_rewrite_paths_outside_data_dir(self, db_session):
        """Custom roots outside DATA_DIR are not remapped with string replacement."""
        from pathlib import Path

        from app.config import settings as app_settings
        from app.settings_store import get_runtime_paths, set_setting

        original_host = app_settings.host_data_dir
        original_data = app_settings.data_dir
        app_settings.data_dir = Path("/data")
        app_settings.host_data_dir = Path("/host/data")
        set_setting(db_session, "generated_root", "/srv/data/generated")
        set_setting(db_session, "cert_root", "/data-extra/certs")
        set_setting(db_session, "tailscale_state_root", "/data/tailscale")
        db_session.flush()
        try:
            paths = get_runtime_paths(db_session)
            assert paths["host_generated_dir"] == "/srv/data/generated"
            assert paths["host_certs_dir"] == "/data-extra/certs"
            assert paths["host_tailscale_state_dir"] == "/host/data/tailscale"
        finally:
            app_settings.data_dir = original_data
            app_settings.host_data_dir = original_host

    def test_host_paths_equal_internal_for_noncanonical_roots_without_host_data_dir(self, db_session):
        """Without HOST_DATA_DIR the host path must be byte-for-byte equal to the
        internal path even when the configured root is non-canonical (contains
        '..'). resolve()-ing only the host side would silently diverge them."""
        from app.config import settings as app_settings
        from app.settings_store import get_runtime_paths, set_setting

        original_host = app_settings.host_data_dir
        app_settings.host_data_dir = None
        set_setting(db_session, "generated_root", "/data/sub/../generated")
        db_session.flush()
        try:
            paths = get_runtime_paths(db_session)
            assert paths["generated_dir"] == "/data/sub/../generated"
            assert paths["host_generated_dir"] == paths["generated_dir"]
        finally:
            app_settings.host_data_dir = original_host


class TestResolveSocket:
    def test_unset_returns_default(self, db_session):
        from app.edge.docker_client import resolve_socket

        # Unset falls back to the default unix socket (non-None).
        assert resolve_socket(db_session) == "unix:///var/run/docker.sock"

    def test_empty_returns_none(self, db_session):
        from app.edge.docker_client import resolve_socket
        from app.settings_store import set_setting

        set_setting(db_session, "docker_socket_path", "")
        db_session.commit()
        assert resolve_socket(db_session) is None

    def test_whitespace_returns_none(self, db_session):
        from app.edge.docker_client import resolve_socket
        from app.settings_store import set_setting

        set_setting(db_session, "docker_socket_path", "   ")
        db_session.commit()
        assert resolve_socket(db_session) is None

    def test_configured_value_is_returned(self, db_session):
        from app.edge.docker_client import resolve_socket
        from app.settings_store import set_setting

        set_setting(db_session, "docker_socket_path", "tcp://10.0.0.1:2375")
        db_session.commit()
        assert resolve_socket(db_session) == "tcp://10.0.0.1:2375"
