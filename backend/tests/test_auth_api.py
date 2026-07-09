"""Tests for authentication API endpoints."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

import app.auth as auth_module
import app.database as database_module
import app.routers.auth as auth_router
from app.config import settings
from app.models.setting import Setting
from app.models.user import User
from app.secrets import CLOUDFLARE_TOKEN, TAILSCALE_API_KEY, TAILSCALE_AUTH_KEY, write_secret
from tests import auth_helpers
from tests.auth_helpers import (
    configure_setup_prerequisites as _configure_setup_prerequisites,
)
from tests.auth_helpers import (
    set_auth_cookie as _set_auth_cookie,
)
from tests.auth_helpers import (
    setup_user as _setup_user,
)

auth_client = auth_helpers.auth_client
_reset_login_rate_limiter = auth_helpers._reset_login_rate_limiter



class TestSetupUser:
    def test_create_first_user(self, auth_client):
        resp = _setup_user(auth_client)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "admin"
        assert "id" in data["user"]
        # Should set access_token cookie
        assert "access_token" in resp.cookies


    def test_setup_user_uses_locked_flush_helper(self, auth_client, monkeypatch):
        calls = 0
        original_flush = auth_router.flush_with_lock

        def counting_flush(db):
            nonlocal calls
            calls += 1
            return original_flush(db)

        monkeypatch.setattr(auth_router, "flush_with_lock", counting_flush)

        resp = _setup_user(auth_client)

        assert resp.status_code == 200
        assert calls == 1

    def test_cannot_create_second_user(self, auth_client):
        _setup_user(auth_client)
        resp = _setup_user(auth_client, username="another")
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_stale_setup_claim_without_user_does_not_block_bootstrap(self, auth_client):
        with database_module.SessionLocal() as db:
            db.add(Setting(key="setup_user_claimed", value="true"))
            db.commit()

        resp = _setup_user(auth_client)

        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "admin"

    def test_hash_failure_does_not_claim_bootstrap(self, auth_client, monkeypatch):
        def fail_hash(_password, _db):
            raise RuntimeError("hash failed")
        monkeypatch.setattr("app.routers.auth.hash_password", fail_hash)

        with pytest.raises(RuntimeError, match="hash failed"):
            auth_client.post(
                "/api/auth/setup-user",
                json={"username": "admin", "password": "securepassword123"},
            )

        progress = auth_client.get("/api/auth/setup-progress").json()
        assert progress["user_exists"] is False

        monkeypatch.undo()
        resp = _setup_user(auth_client)
        assert resp.status_code == 200

    def test_empty_username_rejected(self, auth_client):
        resp = auth_client.post(
            "/api/auth/setup-user",
            json={"username": "", "password": "securepassword123"},
        )
        assert resp.status_code == 422

    def test_whitespace_username_rejected(self, auth_client):
        resp = auth_client.post(
            "/api/auth/setup-user",
            json={"username": "   ", "password": "securepassword123"},
        )
        assert resp.status_code == 422

    def test_username_is_trimmed(self, auth_client):
        resp = _setup_user(auth_client, username="  admin  ")
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "admin"

    def test_short_password_rejected(self, auth_client):
        resp = auth_client.post(
            "/api/auth/setup-user",
            json={"username": "admin", "password": "short"},
        )
        assert resp.status_code == 422

    def test_existing_user_check_skips_hashing(self, auth_client, monkeypatch):
        """Once a user exists, setup-user must 409 BEFORE the expensive bcrypt
        hash (prevents unauthenticated CPU amplification)."""
        _setup_user(auth_client)

        def boom(*_a, **_k):
            raise AssertionError("hash_password must not run once a user exists")

        monkeypatch.setattr("app.routers.auth.hash_password", boom)
        resp = _setup_user(auth_client, username="another")
        assert resp.status_code == 409


class TestLogin:
    def test_successful_login(self, auth_client):
        _setup_user(auth_client)
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "admin"
        assert "access_token" in resp.cookies

    def test_login_username_is_trimmed(self, auth_client):
        _setup_user(auth_client)
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "  admin  ", "password": "securepassword123"},
        )
        assert resp.status_code == 200

    def test_wrong_password(self, auth_client):
        _setup_user(auth_client)
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

    def test_corrupt_password_hash_is_rejected(self, auth_client):
        with database_module.SessionLocal() as db:
            db.add(User(username="admin", password_hash="not-a-bcrypt-hash", role="admin"))
            db.commit()

        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "whatever"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

    def test_nonexistent_user(self, auth_client):
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "whatever"},
        )
        assert resp.status_code == 401

    def test_nonexistent_user_runs_dummy_verify(self, auth_client, monkeypatch):
        """Login on an unknown username must still run a bcrypt verification so
        its timing matches a real check (prevents username enumeration)."""
        calls = []
        real = auth_router.dummy_verify_password
        monkeypatch.setattr(
            auth_router, "dummy_verify_password",
            lambda plain, db: calls.append(plain) or real(plain, db),
        )
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "whatever"},
        )
        assert resp.status_code == 401
        assert calls == ["whatever"]

    def test_missing_fields(self, auth_client):
        resp = auth_client.post("/api/auth/login", json={})
        assert resp.status_code == 422

    def test_login_sets_secure_cookie_behind_https_proxy(self, auth_client):
        """A login arriving over HTTPS (X-Forwarded-Proto) must mark the cookie
        Secure even when cookie_secure is left at its plain-HTTP default."""
        _setup_user(auth_client)
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert resp.status_code == 200
        set_cookie = "; ".join(resp.headers.get_list("set-cookie"))
        assert "secure" in set_cookie.lower()

    def test_login_omits_secure_cookie_over_plain_http(self, auth_client):
        """Over plain HTTP with the default config, the cookie must NOT be
        Secure (otherwise the browser drops it and login breaks on HTTP)."""
        _setup_user(auth_client)
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        assert resp.status_code == 200
        set_cookie = "; ".join(resp.headers.get_list("set-cookie"))
        assert "secure" not in set_cookie.lower()

    def test_login_secure_cookie_follows_first_forwarded_proto_hop(self, auth_client):
        """X-Forwarded-Proto may carry the whole proxy chain (client-facing hop
        first). The Secure flag must follow the FIRST hop — the protocol the
        browser actually used — not any later internal hop. A regression that
        compared the entire header string (rather than splitting on ',') would
        misclassify both of these."""
        _setup_user(auth_client)
        # Client reached the edge over HTTPS; a later internal hop was plain HTTP.
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
            headers={"X-Forwarded-Proto": "https, http"},
        )
        assert resp.status_code == 200
        assert "secure" in "; ".join(resp.headers.get_list("set-cookie")).lower()

        # Client reached the edge over plain HTTP; a later hop was HTTPS. The
        # cookie must NOT be Secure, or the browser drops it on the HTTP client.
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
            headers={"X-Forwarded-Proto": "http, https"},
        )
        assert resp.status_code == 200
        assert "secure" not in "; ".join(resp.headers.get_list("set-cookie")).lower()

    def test_login_cookie_is_httponly_lax_and_api_scoped(self, auth_client):
        """The session cookie must be HttpOnly (no JS access -> blunts XSS theft),
        SameSite=Lax (blunts cross-site CSRF on the cookie), and scoped to /api so
        it is never sent on requests for the static SPA assets."""
        _setup_user(auth_client)
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        assert resp.status_code == 200
        set_cookie = "; ".join(resp.headers.get_list("set-cookie")).lower()
        assert "access_token=" in set_cookie
        assert "httponly" in set_cookie
        assert "samesite=lax" in set_cookie
        assert "path=/api" in set_cookie


class TestLogout:
    def test_logout_clears_cookie(self, auth_client):
        _setup_user(auth_client)
        # Login first to get a cookie
        auth_client.cookies.clear()
        login_resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        _set_auth_cookie(auth_client, login_resp.cookies["access_token"])

        # Logout
        resp = auth_client.post("/api/auth/logout")
        assert resp.status_code == 200

    def test_logout_without_auth_returns_401(self, auth_client):
        resp = auth_client.post("/api/auth/logout")
        assert resp.status_code == 401


class TestMe:
    def test_me_with_valid_cookie(self, auth_client):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])

        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"
        assert resp.json()["role"] == "admin"

    def test_me_without_cookie(self, auth_client):
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token(self, auth_client):
        _set_auth_cookie(auth_client, "invalid-token")
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_with_valid_token_for_deactivated_user_is_rejected(self, auth_client):
        """A still-valid JWT whose user was deactivated (or deleted) must be
        rejected: get_current_user filters on is_active, so the protected
        endpoint 401s 'User not found' instead of authenticating a disabled
        admin. /status has its own manual check; this pins the dependency path."""
        setup_resp = _setup_user(auth_client)
        token = setup_resp.cookies["access_token"]

        with database_module.SessionLocal() as db:
            user = db.query(User).filter(User.username == "admin").first()
            user.is_active = False
            db.commit()

        auth_client.cookies.clear()
        _set_auth_cookie(auth_client, token)
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "User not found"


class TestJwtTokens:
    def test_token_without_expiration_is_rejected(self):
        token = jwt.encode({"sub": "usr_no_exp"}, settings.jwt_secret, algorithm="HS256")

        assert auth_module.decode_access_token(token) is None

    def test_token_without_subject_is_rejected(self):
        # options={"require": [..., "sub"]} makes a subject-less token fail to
        # decode rather than authenticate an empty subject.
        token = jwt.encode(
            {"exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        )

        assert auth_module.decode_access_token(token) is None

    def test_token_with_empty_subject_is_rejected(self):
        # A present-but-empty "sub" passes the require check, so the
        # `subject if subject else None` guard is what stops decode from
        # returning "" (which would then be matched against User.id == "").
        token = jwt.encode(
            {"sub": "", "exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        )

        assert auth_module.decode_access_token(token) is None

    def test_expired_token_is_rejected(self, auth_client):
        # exp one hour in the past triggers the ExpiredSignatureError branch.
        token = jwt.encode(
            {"sub": "usr_expired", "exp": datetime.now(UTC) - timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        )

        assert auth_module.decode_access_token(token) is None

        _set_auth_cookie(auth_client, token)
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_token_signed_with_wrong_secret_is_rejected(self):
        """A structurally valid JWT signed with a DIFFERENT key must fail
        signature verification. Guards the core JWT property against a regression
        that weakened the decode (e.g. dropped the secret / disabled
        verify_signature): forging a session must require the server's signing
        secret, not merely a well-formed token."""
        token = jwt.encode(
            {"sub": "usr_forged", "exp": datetime.now(UTC) + timedelta(hours=1)},
            "a-different-secret-than-the-app-uses",
            algorithm="HS256",
        )

        assert auth_module.decode_access_token(token) is None


class TestAuthStatus:
    def test_before_setup(self, auth_client):
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["setup_complete"] is False
        assert data["authenticated"] is False

    def test_after_user_setup_with_cookie(self, auth_client):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])

        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["setup_complete"] is False  # setup_complete not yet set
        assert data["authenticated"] is True

    def test_after_setup_complete_not_logged_in(self, auth_client):
        _setup_user(auth_client)
        # Login to get a cookie
        auth_client.cookies.clear()
        login_resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        _set_auth_cookie(auth_client, login_resp.cookies["access_token"])
        _configure_setup_prerequisites(auth_client)

        resp = auth_client.put("/api/settings/setup-complete", json={})
        assert resp.status_code == 200

        # Clear cookies, then check status
        auth_client.cookies.clear()
        resp = auth_client.get("/api/auth/status")
        data = resp.json()
        assert data["setup_complete"] is True
        assert data["authenticated"] is False

    def test_invalid_cookie_reports_not_authenticated(self, auth_client):
        _setup_user(auth_client)
        auth_client.cookies.clear()
        _set_auth_cookie(auth_client, "not-a-valid-jwt")
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False

    def test_inactive_user_reports_not_authenticated(self, auth_client):
        setup_resp = _setup_user(auth_client)
        token = setup_resp.cookies["access_token"]
        # Deactivate the user while holding a still-valid token: status must not
        # report the disabled account as authenticated (mirrors get_current_user).
        with database_module.SessionLocal() as db:
            user = db.query(User).filter(User.username == "admin").first()
            user.is_active = False
            db.commit()

        auth_client.cookies.clear()
        _set_auth_cookie(auth_client, token)
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False

    def test_stale_token_version_reports_not_authenticated(self, auth_client):
        """A token whose ``ver`` claim predates a token_version bump (e.g. a
        password change on another device) must report authenticated=False,
        matching get_current_user. Pre-fix /status used decode_access_token (sub
        only, NO ``ver`` check), so a revoked session was wrongly reported as
        authenticated while every protected endpoint 401'd it."""
        setup_resp = _setup_user(auth_client)
        token = setup_resp.cookies["access_token"]  # minted at token_version=0

        with database_module.SessionLocal() as db:
            user = db.query(User).filter(User.username == "admin").first()
            user.token_version += 1  # mirrors a credential change elsewhere
            db.commit()

        auth_client.cookies.clear()
        _set_auth_cookie(auth_client, token)
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200
        assert resp.json()["authenticated"] is False


class TestSetupProgress:
    """The setup-progress endpoint reports which setup steps are already done."""

    def test_fresh_install_all_false(self, auth_client):
        resp = auth_client.get("/api/auth/setup-progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_exists"] is False
        assert data["base_domain_set"] is False
        assert data["cloudflare_configured"] is False
        assert data["acme_email_set"] is False
        assert data["tailscale_configured"] is False
        assert data["docker_configured"] is False

    def test_after_user_created(self, auth_client):
        _setup_user(auth_client)
        resp = auth_client.get("/api/auth/setup-progress")
        data = resp.json()
        assert data["user_exists"] is True
        assert data["base_domain_set"] is False

    def test_after_settings_configured(self, auth_client):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])

        # Set base domain
        auth_client.put("/api/settings/general", json={"base_domain": "test.com"})

        resp = auth_client.get("/api/auth/setup-progress")
        data = resp.json()
        assert data["user_exists"] is True
        assert data["base_domain_set"] is True
        assert data["acme_email_set"] is False

    def test_after_acme_email_set(self, auth_client):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])

        auth_client.put("/api/settings/general", json={"acme_email": "me@test.com"})

        resp = auth_client.get("/api/auth/setup-progress")
        data = resp.json()
        assert data["acme_email_set"] is True

    def test_cloudflare_requires_zone_and_token(self, auth_client, tmp_data_dir):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])

        auth_client.put("/api/settings/cloudflare", json={"zone_id": "zone123"})
        resp = auth_client.get("/api/auth/setup-progress")
        assert resp.json()["cloudflare_token_set"] is False
        assert resp.json()["cloudflare_configured"] is False

        write_secret(CLOUDFLARE_TOKEN, "cf-token")
        resp = auth_client.get("/api/auth/setup-progress")
        assert resp.json()["cloudflare_token_set"] is True
        assert resp.json()["cloudflare_configured"] is True


    def test_tailscale_requires_auth_and_api_key(self, auth_client, tmp_data_dir):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])

        write_secret(TAILSCALE_AUTH_KEY, "tskey-auth-abc123")
        resp = auth_client.get("/api/auth/setup-progress")
        assert resp.json()["tailscale_configured"] is False

        write_secret(TAILSCALE_API_KEY, "tskey-api-abc123")
        resp = auth_client.get("/api/auth/setup-progress")
        assert resp.json()["tailscale_configured"] is True

    def test_is_publicly_accessible(self, auth_client):
        """setup-progress must work without auth (it's used before login)."""
        auth_client.cookies.clear()
        resp = auth_client.get("/api/auth/setup-progress")
        assert resp.status_code == 200


    def test_requires_auth_after_setup_complete(self, auth_client):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])
        _configure_setup_prerequisites(auth_client)
        complete_resp = auth_client.put("/api/settings/setup-complete", json={})
        assert complete_resp.status_code == 200

        auth_client.cookies.clear()
        resp = auth_client.get("/api/auth/setup-progress")
        assert resp.status_code == 401

    def test_authenticated_after_setup_complete(self, auth_client):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])
        _configure_setup_prerequisites(auth_client)
        complete_resp = auth_client.put("/api/settings/setup-complete", json={})
        assert complete_resp.status_code == 200

        resp = auth_client.get("/api/auth/setup-progress")
        assert resp.status_code == 200
        assert resp.json()["user_exists"] is True


class TestChangePassword:
    """Tests for the POST /api/auth/change-password endpoint."""

    def _login(self, client, password="securepassword123"):
        """Helper: create user, clear cookies, login, set cookie."""
        _setup_user(client, password=password)
        client.cookies.clear()
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        _set_auth_cookie(client, login_resp.cookies["access_token"])
        return login_resp

    def test_change_password_success(self, auth_client):
        self._login(auth_client)
        resp = auth_client.post(
            "/api/auth/change-password",
            json={"current_password": "securepassword123", "new_password": "newpassword456"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify old password no longer works
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        assert resp.status_code == 401

        # Verify new password works
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "newpassword456"},
        )
        assert resp.status_code == 200

    def test_wrong_current_password(self, auth_client):
        self._login(auth_client)
        resp = auth_client.post(
            "/api/auth/change-password",
            json={"current_password": "wrongpassword", "new_password": "newpassword456"},
        )
        assert resp.status_code == 401
        assert "incorrect" in resp.json()["detail"].lower()

    def test_new_password_too_short(self, auth_client):
        self._login(auth_client)
        resp = auth_client.post(
            "/api/auth/change-password",
            json={"current_password": "securepassword123", "new_password": "short"},
        )
        assert resp.status_code == 422

    def test_requires_auth(self, auth_client):
        _setup_user(auth_client)
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/change-password",
            json={"current_password": "securepassword123", "new_password": "newpassword456"},
        )
        assert resp.status_code == 401

    def test_missing_fields(self, auth_client):
        self._login(auth_client)
        resp = auth_client.post("/api/auth/change-password", json={})
        assert resp.status_code == 422


class TestSessionInvalidationOnPasswordChange:
    """AS3: change-password bumps the user's token_version, so every JWT minted
    with the previous version is rejected, while the acting session is handed a
    fresh cookie carrying the new version (so the admin is not logged out)."""

    def _login(self, client, password="securepassword123"):
        _setup_user(client, password=password)
        client.cookies.clear()
        login_resp = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        _set_auth_cookie(client, login_resp.cookies["access_token"])
        return login_resp

    def test_login_token_authenticates(self, auth_client):
        # A freshly issued login token carries the current version and works.
        login_resp = self._login(auth_client)
        _set_auth_cookie(auth_client, login_resp.cookies["access_token"])
        assert auth_client.get("/api/auth/me").status_code == 200

    def test_old_token_rejected_after_password_change(self, auth_client):
        login_resp = self._login(auth_client)
        old_token = login_resp.cookies["access_token"]

        # Sanity: the token authenticates before the password change.
        _set_auth_cookie(auth_client, old_token)
        assert auth_client.get("/api/auth/me").status_code == 200

        change = auth_client.post(
            "/api/auth/change-password",
            json={"current_password": "securepassword123", "new_password": "newpassword456"},
        )
        assert change.status_code == 200

        # The token minted before the change now carries a stale version and is
        # rejected, even though it has not expired.
        _set_auth_cookie(auth_client, old_token)
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_change_password_reissues_valid_cookie(self, auth_client):
        self._login(auth_client)
        change = auth_client.post(
            "/api/auth/change-password",
            json={"current_password": "securepassword123", "new_password": "newpassword456"},
        )
        assert change.status_code == 200
        # A fresh access_token cookie is set on the response ...
        new_token = change.cookies["access_token"]
        assert new_token
        # ... and it authenticates the current session under the bumped version.
        _set_auth_cookie(auth_client, new_token)
        assert auth_client.get("/api/auth/me").status_code == 200

    def test_legacy_token_without_version_claim_is_rejected(self, auth_client):
        """A JWT minted before the ``ver`` claim existed carries no ``ver``. Such
        a legacy token must be rejected by get_current_user: payload.get('ver')
        is None while the user's token_version is 0, so the revocation check 401s
        rather than authenticating a pre-versioning token."""
        _setup_user(auth_client)
        with database_module.SessionLocal() as db:
            user_id = db.query(User).filter(User.username == "admin").first().id

        legacy = jwt.encode(
            {"sub": user_id, "exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        )
        auth_client.cookies.clear()
        _set_auth_cookie(auth_client, legacy)
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401


class TestProtectedEndpoints:
    """Verify that protected routers return 401 without auth."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/settings"),
            ("GET", "/api/services"),
            ("GET", "/api/events"),
            ("GET", "/api/dashboard/summary"),
            ("GET", "/api/profiles"),
            ("GET", "/api/jobs"),
            ("GET", "/api/discovery/containers"),
        ],
    )
    def test_protected_routes_require_auth(self, auth_client, method, path):
        resp = auth_client.request(method, path)
        assert resp.status_code == 401

    def test_health_endpoint_is_public(self, auth_client):
        resp = auth_client.get("/api/health")
        assert resp.status_code == 200

    def test_auth_status_is_public(self, auth_client):
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200

    def test_protected_route_works_with_auth(self, auth_client):
        setup_resp = _setup_user(auth_client)
        _set_auth_cookie(auth_client, setup_resp.cookies["access_token"])

        resp = auth_client.get("/api/settings")
        assert resp.status_code == 200
