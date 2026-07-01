"""Tests for setup-progress derivation (the setup wizard step flags).

compute_setup_progress reads settings rows directly (NOT via get_setting, so
unset keys are "not configured" even though DEFAULTS holds placeholders) and
file-backed secrets, then derives one boolean per wizard step. These tests pin
every flag, every multi-input combination, and idempotency.
"""

from app.models.setting import Setting
from app.models.user import User
from app.secrets import (
    CLOUDFLARE_TOKEN,
    TAILSCALE_API_KEY,
    TAILSCALE_AUTH_KEY,
    write_secret,
)
from app.settings_store import set_setting
from app.setup_state import compute_setup_progress, missing_setup_requirements

ALL_FLAGS = {
    "user_exists",
    "base_domain_set",
    "cloudflare_configured",
    "cloudflare_token_set",
    "acme_email_set",
    "tailscale_configured",
    "docker_configured",
}


def _add_user(db):
    db.add(User(id="usr_setup0001", username="admin", password_hash="x", role="admin"))
    db.flush()


def _fully_configure(db):
    _add_user(db)
    set_setting(db, "base_domain", "example.org")
    set_setting(db, "cf_zone_id", "zone123")
    set_setting(db, "acme_email", "ops@example.org")
    set_setting(db, "docker_socket_path", "unix:///var/run/docker.sock")
    db.flush()
    write_secret(CLOUDFLARE_TOKEN, "cf-token")
    write_secret(TAILSCALE_AUTH_KEY, "tskey-auth-abc")
    write_secret(TAILSCALE_API_KEY, "tskey-api-xyz")


class TestComputeSetupProgress:
    def test_empty_db_all_false(self, db_session):
        progress = compute_setup_progress(db_session)
        assert set(progress) == ALL_FLAGS
        assert all(v is False for v in progress.values())

    def test_user_flag(self, db_session):
        _add_user(db_session)
        assert compute_setup_progress(db_session)["user_exists"] is True

    def test_base_domain_blank_value_is_not_set(self, db_session):
        # A row with an empty value must not count as configured.
        set_setting(db_session, "base_domain", "")
        db_session.flush()
        assert compute_setup_progress(db_session)["base_domain_set"] is False

    def test_base_domain_set(self, db_session):
        set_setting(db_session, "base_domain", "example.org")
        db_session.flush()
        assert compute_setup_progress(db_session)["base_domain_set"] is True

    def test_acme_email_set(self, db_session):
        set_setting(db_session, "acme_email", "ops@example.org")
        db_session.flush()
        assert compute_setup_progress(db_session)["acme_email_set"] is True

    def test_docker_configured(self, db_session):
        set_setting(db_session, "docker_socket_path", "unix:///var/run/docker.sock")
        db_session.flush()
        assert compute_setup_progress(db_session)["docker_configured"] is True

    # --- cloudflare: needs BOTH a zone-id setting AND the token secret ---

    def test_cloudflare_zone_only_is_incomplete(self, db_session):
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.flush()
        p = compute_setup_progress(db_session)
        assert p["cloudflare_token_set"] is False
        assert p["cloudflare_configured"] is False

    def test_cloudflare_token_only_is_incomplete(self, db_session):
        write_secret(CLOUDFLARE_TOKEN, "cf-token")
        p = compute_setup_progress(db_session)
        assert p["cloudflare_token_set"] is True
        assert p["cloudflare_configured"] is False  # no zone

    def test_cloudflare_zone_and_token_complete(self, db_session):
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.flush()
        write_secret(CLOUDFLARE_TOKEN, "cf-token")
        p = compute_setup_progress(db_session)
        assert p["cloudflare_token_set"] is True
        assert p["cloudflare_configured"] is True

    # --- tailscale: needs BOTH a valid auth key AND a valid api key ---

    def test_tailscale_auth_only_is_incomplete(self, db_session):
        write_secret(TAILSCALE_AUTH_KEY, "tskey-auth-abc")
        assert compute_setup_progress(db_session)["tailscale_configured"] is False

    def test_tailscale_both_valid_is_complete(self, db_session):
        write_secret(TAILSCALE_AUTH_KEY, "tskey-auth-abc")
        write_secret(TAILSCALE_API_KEY, "tskey-api-xyz")
        assert compute_setup_progress(db_session)["tailscale_configured"] is True

    def test_tailscale_reusable_prefix_rejected(self, db_session):
        # 'tskey-reusable-' is not a real Tailscale prefix — reusable keys also
        # use 'tskey-auth-'. The dead alternative must now be rejected.
        write_secret(TAILSCALE_AUTH_KEY, "tskey-reusable-abc")
        write_secret(TAILSCALE_API_KEY, "tskey-api-xyz")
        assert compute_setup_progress(db_session)["tailscale_configured"] is False

    def test_tailscale_rejects_bad_auth_prefix(self, db_session):
        write_secret(TAILSCALE_AUTH_KEY, "not-a-real-key")
        write_secret(TAILSCALE_API_KEY, "tskey-api-xyz")
        assert compute_setup_progress(db_session)["tailscale_configured"] is False

    def test_tailscale_api_slot_must_not_hold_an_auth_key(self, db_session):
        # An auth-prefixed value in the API slot is the wrong key type.
        write_secret(TAILSCALE_AUTH_KEY, "tskey-auth-abc")
        write_secret(TAILSCALE_API_KEY, "tskey-auth-abc")
        assert compute_setup_progress(db_session)["tailscale_configured"] is False

    def test_all_configured_all_true(self, db_session):
        _fully_configure(db_session)
        progress = compute_setup_progress(db_session)
        assert all(v is True for v in progress.values())

    def test_idempotent_and_read_only(self, db_session):
        _add_user(db_session)
        set_setting(db_session, "base_domain", "example.org")
        db_session.flush()
        first = compute_setup_progress(db_session)
        second = compute_setup_progress(db_session)
        assert first == second
        # The read must not write any settings rows of its own.
        assert {r.key for r in db_session.query(Setting).all()} == {"base_domain"}


class TestMissingSetupRequirements:
    def test_all_missing_in_wizard_order(self, db_session):
        assert missing_setup_requirements(db_session) == [
            "user",
            "base domain",
            "Cloudflare zone and token",
            "ACME email",
            "Tailscale auth key and API key",
            "Docker socket",
        ]

    def test_none_missing_when_fully_configured(self, db_session):
        _fully_configure(db_session)
        assert missing_setup_requirements(db_session) == []

    def test_partial_reports_only_incomplete_steps(self, db_session):
        _add_user(db_session)
        set_setting(db_session, "base_domain", "example.org")
        set_setting(db_session, "acme_email", "ops@example.org")
        set_setting(db_session, "docker_socket_path", "unix:///var/run/docker.sock")
        db_session.flush()
        # Cloudflare + Tailscale still unconfigured.
        assert missing_setup_requirements(db_session) == [
            "Cloudflare zone and token",
            "Tailscale auth key and API key",
        ]
