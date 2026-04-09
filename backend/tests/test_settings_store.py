"""Tests for the settings key-value store."""

from app.models.setting import Setting
from app.settings_store import DEFAULTS, get_all_settings, get_setting, set_setting


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
        assert result["reconcile_interval_seconds"] == "60"
        assert result["setup_complete"] == "false"

    def test_get_all_settings_with_overrides(self, db_session):
        set_setting(db_session, "base_domain", "custom.com")
        set_setting(db_session, "setup_complete", "true")
        result = get_all_settings(db_session)
        assert result["base_domain"] == "custom.com"
        assert result["setup_complete"] == "true"
        # Non-overridden keys still have defaults
        assert result["acme_email"] == DEFAULTS["acme_email"]


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
        assert "docker_socket" in paths
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
        from pathlib import Path, PurePosixPath

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
