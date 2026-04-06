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
