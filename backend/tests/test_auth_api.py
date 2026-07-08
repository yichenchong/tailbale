"""Tests for authentication API endpoints."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.config import settings
from app.database import Base, get_db


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter():
    """Clear brute-force limiter state around every test so accumulated failure
    counts never leak across the (mostly successful) login suite. The limiter is
    module-level state that otherwise persists for the whole session."""
    import app.routers.auth as auth_module

    auth_module.reset_login_rate_limiter()
    yield
    auth_module.reset_login_rate_limiter()


@pytest.fixture()
def auth_client(tmp_data_dir):
    """TestClient WITHOUT auth bypass — for testing the auth endpoints themselves."""
    import app.database as database_module

    original_engine = database_module.engine
    original_session_local = database_module.SessionLocal

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    database_module.engine = engine
    database_module.SessionLocal = sessionmaker(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def _override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    import app.main as main_module
    from app.main import app

    # Point the lifespan's create_all at this in-memory engine (main binds the
    # engine name at import) so startup never connects to the real file engine.
    original_main_engine = main_module.engine
    main_module.engine = engine

    app.dependency_overrides[get_db] = _override_get_db
    # Explicitly remove any auth bypass so real auth is used
    app.dependency_overrides.pop(get_current_user, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    database_module.engine = original_engine
    database_module.SessionLocal = original_session_local
    main_module.engine = original_main_engine
    engine.dispose()


def _setup_user(client, username="admin", password="securepassword123"):
    """Helper to create the initial user via the setup endpoint."""
    resp = client.post(
        "/api/auth/setup-user",
        json={"username": username, "password": password},
    )
    return resp


def _set_auth_cookie(client, cookie_value):
    """Set the access_token cookie on the client instance (avoids per-request deprecation)."""
    client.cookies.set("access_token", cookie_value)


def _configure_setup_prerequisites(client):
    client.put("/api/settings/general", json={
        "base_domain": "example.com",
        "acme_email": "admin@example.com",
    })
    client.put("/api/settings/cloudflare", json={
        "zone_id": "zone123",
        "token": "cf-token",
    })
    client.put("/api/settings/tailscale", json={
        "auth_key": "tskey-auth-abc123",
        "api_key": "tskey-api-abc123",
    })
    client.put("/api/settings/docker", json={
        "socket_path": "unix:///var/run/docker.sock",
    })


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
        import app.routers.auth as auth_router

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
        from app.database import SessionLocal
        from app.models.setting import Setting

        with SessionLocal() as db:
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
        from app.database import SessionLocal
        from app.models.user import User

        with SessionLocal() as db:
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
        import app.routers.auth as auth_module
        real = auth_module.dummy_verify_password
        monkeypatch.setattr(
            auth_module, "dummy_verify_password",
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


class TestLoginRateLimit:
    """Brute-force protection on POST /api/auth/login.

    Regression guard: pre-fix the endpoint had no rate-limiting, so the 429
    assertions below would all fail (a wrong password always returned 401).
    """

    def _wrong(self, client):
        return client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )

    def _right(self, client):
        return client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )

    def test_consecutive_failures_trigger_429_with_retry_after(self, auth_client):
        _setup_user(auth_client)
        auth_client.cookies.clear()
        # The first N-1 failures stay 401; the Nth (threshold) trips the lock.
        from app.config import settings

        threshold = settings.login_max_failures
        for _ in range(threshold - 1):
            assert self._wrong(auth_client).status_code == 401
        locked = self._wrong(auth_client)
        assert locked.status_code == 429
        assert int(locked.headers["Retry-After"]) > 0

    def test_attempt_during_cooldown_is_rejected_429(self, auth_client):
        _setup_user(auth_client)
        auth_client.cookies.clear()
        from app.config import settings

        for _ in range(settings.login_max_failures):
            self._wrong(auth_client)
        # Already locked: a further probe is refused before bcrypt runs.
        again = self._wrong(auth_client)
        assert again.status_code == 429
        assert int(again.headers["Retry-After"]) > 0
        # Even a correct password is refused while the cooldown is active.
        assert self._right(auth_client).status_code == 429

    def test_successful_login_resets_failure_counter(self, auth_client):
        _setup_user(auth_client)
        auth_client.cookies.clear()
        from app.config import settings

        threshold = settings.login_max_failures
        # Stop one short of the lock, then succeed to clear the streak.
        for _ in range(threshold - 1):
            assert self._wrong(auth_client).status_code == 401
        assert self._right(auth_client).status_code == 200
        auth_client.cookies.clear()
        # The next failure must be a fresh 401, not the carried-over Nth -> 429.
        for _ in range(threshold - 1):
            assert self._wrong(auth_client).status_code == 401
        assert self._wrong(auth_client).status_code == 429

    def test_setup_user_is_not_rate_limited(self, auth_client):
        # Drive the login limiter to a lockout, then confirm /setup-user still
        # works for the legitimate first-run flow (different endpoint/state).
        from app.config import settings

        for _ in range(settings.login_max_failures + 1):
            self._wrong(auth_client)
        resp = _setup_user(auth_client)
        assert resp.status_code == 200

    def test_unknown_usernames_from_one_client_trip_lockout(self, auth_client):
        """AS-T1: username spraying from a single client must still trip the
        per-IP lockout. The unknown-user branch runs a dummy bcrypt verify AND
        records the failure, so failures key on the client host (not the
        username) and accumulate across *distinct nonexistent* usernames.
        Guards against a refactor that skips ``_reject_failed_login`` on the
        unknown-user path, which would silently disable brute-force protection
        for username enumeration/spray."""

        # No user is set up: every attempt hits the missing-user dummy-verify
        # branch. Each uses a different nonexistent username.
        threshold = settings.login_max_failures
        for i in range(threshold - 1):
            resp = auth_client.post(
                "/api/auth/login",
                json={"username": f"ghost-{i}", "password": "whatever-pw"},
            )
            assert resp.status_code == 401
        locked = auth_client.post(
            "/api/auth/login",
            json={"username": f"ghost-{threshold}", "password": "whatever-pw"},
        )
        assert locked.status_code == 429
        assert int(locked.headers["Retry-After"]) > 0


class TestLoginRateLimiterEviction:
    """The memory backstop (``max_entries`` hard cap) must bound the table
    WITHOUT silently releasing an active lockout.

    A locked-out client's ``last_seen`` is frozen at the moment it was locked
    (probes during the cooldown are refused before any bookkeeping update), so
    it is the *oldest* entry. A pure last_seen LRU therefore evicts the locked
    attacker first when a flood of fresh client ids overflows the cap, resetting
    their cooldown. Regression: pre-fix this assertion fails (victim unlocked).
    """

    def test_hard_cap_does_not_release_active_lockout(self):
        from app.routers.auth import _LoginRateLimiter

        clock = {"t": 1000.0}
        limiter = _LoginRateLimiter(max_failures=3, cooldown_seconds=300, max_entries=3)
        limiter._now = lambda: clock["t"]  # deterministic, tie-free timestamps

        # Lock the victim at t=1000 (3 consecutive failures with max_failures=3).
        for _ in range(3):
            limiter.record_failure("victim")
        assert limiter.retry_after("victim") is not None  # locked

        # Flood with fresh, UNLOCKED clients, each more-recently-seen than the
        # victim, well past the hard cap.
        for i in range(20):
            clock["t"] += 1.0
            limiter.record_failure(f"filler-{i}")

        # The cap still bounds memory...
        assert len(limiter._entries) <= limiter._max_entries + 1
        # ...but the victim, still inside its 300s cooldown, MUST stay locked.
        assert limiter.retry_after("victim") is not None

    def test_all_locked_flood_stays_bounded_and_keeps_only_locked(self):
        # Re-audit invariant: even when EVERY tracked client is actively locked
        # (a distributed flood where each fresh id trips the lock immediately),
        # the table must still be hard-capped — and, because there are no
        # unlocked victims to sacrifice, it sheds the *oldest* (soonest-to-expire)
        # lockouts rather than growing without bound.
        from app.routers.auth import _LoginRateLimiter

        clock = {"t": 1000.0}
        limiter = _LoginRateLimiter(max_failures=1, cooldown_seconds=300, max_entries=3)
        limiter._now = lambda: clock["t"]  # deterministic, tie-free timestamps

        for i in range(10):
            clock["t"] += 1.0
            limiter.record_failure(f"k-{i}")  # max_failures=1 => instant lock

        # Memory stays bounded under an all-locked mix...
        assert len(limiter._entries) <= limiter._max_entries + 1
        # ...every surviving entry is genuinely still locked (no unlocked junk)...
        now = clock["t"]
        assert all(e.locked_until > now for e in limiter._entries.values())
        # ...and it is the most-recently-locked ids that survive (oldest shed).
        assert "k-9" in limiter._entries
        assert "k-0" not in limiter._entries


class TestLoginRateLimiterExpiry:
    """A lockout must expire on its own once the cooldown elapses — not only on a
    successful login — and the first failure afterward starts a fresh streak."""

    def test_lockout_clears_after_cooldown_and_next_failure_starts_fresh(self):
        from app.routers.auth import _LoginRateLimiter

        clock = {"t": 1000.0}
        limiter = _LoginRateLimiter(max_failures=3, cooldown_seconds=300)
        limiter._now = lambda: clock["t"]  # deterministic, tie-free timestamps

        for _ in range(3):
            limiter.record_failure("client")  # locked at t=1000
        assert limiter.retry_after("client") is not None

        # Advance past the cooldown: the lock must clear by itself...
        clock["t"] += 301.0
        assert limiter.retry_after("client") is None

        # ...and the next failure is a fresh streak (1 < 3), not a re-lock.
        assert limiter.record_failure("client") is None
        assert limiter.retry_after("client") is None


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

        from app.database import SessionLocal
        from app.models.user import User

        with SessionLocal() as db:
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
        import jwt

        from app.auth import decode_access_token
        from app.config import settings

        token = jwt.encode({"sub": "usr_no_exp"}, settings.jwt_secret, algorithm="HS256")

        assert decode_access_token(token) is None

    def test_token_without_subject_is_rejected(self):
        # options={"require": [..., "sub"]} makes a subject-less token fail to
        # decode rather than authenticate an empty subject.
        from datetime import UTC, datetime, timedelta

        import jwt

        from app.auth import decode_access_token
        from app.config import settings

        token = jwt.encode(
            {"exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        )

        assert decode_access_token(token) is None

    def test_token_with_empty_subject_is_rejected(self):
        # A present-but-empty "sub" passes the require check, so the
        # `subject if subject else None` guard is what stops decode from
        # returning "" (which would then be matched against User.id == "").
        from datetime import UTC, datetime, timedelta

        import jwt

        from app.auth import decode_access_token
        from app.config import settings

        token = jwt.encode(
            {"sub": "", "exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        )

        assert decode_access_token(token) is None

    def test_expired_token_is_rejected(self, auth_client):
        from datetime import UTC, datetime, timedelta

        import jwt

        from app.auth import decode_access_token
        from app.config import settings

        # exp one hour in the past triggers the ExpiredSignatureError branch.
        token = jwt.encode(
            {"sub": "usr_expired", "exp": datetime.now(UTC) - timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        )

        assert decode_access_token(token) is None

        _set_auth_cookie(auth_client, token)
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401


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
        from app.database import SessionLocal
        from app.models.user import User

        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "admin").first()
            user.is_active = False
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
        from app.secrets import CLOUDFLARE_TOKEN, write_secret

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
        from app.secrets import TAILSCALE_API_KEY, TAILSCALE_AUTH_KEY, write_secret

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


class TestPasswordSalt:
    """Verify that the salt is stored in settings and used consistently."""

    def test_salt_is_generated_on_first_user(self, auth_client):
        _setup_user(auth_client)
        # Login works, proving the salt was consistent between hash and verify
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        assert resp.status_code == 200

    def test_password_not_stored_as_plaintext(self, auth_client):
        """The stored password_hash must be a bcrypt hash, never the plaintext.

        Asserting only that a wrong password is rejected does NOT prove this: a
        plaintext-comparison backend would reject wrong passwords too. Read the
        persisted hash directly and confirm it neither equals nor contains the
        plaintext and carries a bcrypt identifier.
        """
        plaintext = "mysecretpass123"
        _setup_user(auth_client, password=plaintext)

        from app.database import SessionLocal
        from app.models.user import User

        with SessionLocal() as db:
            stored = db.query(User).filter(User.username == "admin").first().password_hash

        assert stored != plaintext
        assert plaintext not in stored
        assert stored.startswith("$2")  # bcrypt hash id ($2a$/$2b$/$2y$)



class TestCorsOptions:
    def _preflight(self, options, origin="https://evil.example"):
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware

        app = FastAPI()
        app.add_middleware(CORSMiddleware, **options)

        @app.get("/api/probe")
        async def probe():
            return {"ok": True}

        with TestClient(app) as client:
            return client.options(
                "/api/probe",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                },
            )

    def test_empty_cors_origins_disables_middleware(self):
        from app.main import _cors_middleware_options

        assert _cors_middleware_options("") is None

    def test_explicit_cors_origins_allow_credentials(self):
        from app.main import _cors_middleware_options

        options = _cors_middleware_options(" https://ui.example , https://admin.example ")
        assert options is not None
        assert options["allow_origins"] == ["https://ui.example", "https://admin.example"]
        assert options["allow_credentials"] is True

        resp = self._preflight(options, origin="https://ui.example")
        assert resp.headers["access-control-allow-origin"] == "https://ui.example"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_wildcard_cors_origin_disables_credentials_even_when_mixed(self):
        from app.main import _cors_middleware_options

        options = _cors_middleware_options("*, https://ui.example")
        assert options is not None
        assert options["allow_origins"] == ["*"]
        assert options["allow_credentials"] is False

        resp = self._preflight(options)
        assert resp.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in resp.headers


class TestJwtSecretLoading:
    """Regression: a persisted-but-empty/corrupt JWT secret file must never
    yield an empty HMAC key (an empty key makes every JWT trivially forgeable,
    a silent full auth bypass). It must heal by regenerating a fresh secret."""

    def test_whitespace_only_file_is_regenerated(self, tmp_path):
        from app.config import _load_or_create_jwt_secret

        secret_file = tmp_path / "secrets" / ".jwt_secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("   \n", encoding="utf-8")

        secret = _load_or_create_jwt_secret(tmp_path)
        assert secret, "must not return an empty/blank secret"
        # The corrupt file is healed in place with the returned secret.
        assert secret_file.read_text(encoding="utf-8").strip() == secret

    def test_zero_byte_file_is_regenerated(self, tmp_path):
        from app.config import _load_or_create_jwt_secret

        secret_file = tmp_path / "secrets" / ".jwt_secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("", encoding="utf-8")

        secret = _load_or_create_jwt_secret(tmp_path)
        assert secret
        assert secret_file.read_text(encoding="utf-8").strip() == secret

    def test_valid_secret_is_preserved(self, tmp_path):
        from app.config import _load_or_create_jwt_secret

        secret_file = tmp_path / "secrets" / ".jwt_secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("an-existing-real-secret", encoding="utf-8")

        assert _load_or_create_jwt_secret(tmp_path) == "an-existing-real-secret"

    def test_valid_secret_permissions_are_tightened(self, tmp_path):
        import os
        import stat

        from app.config import _load_or_create_jwt_secret

        secret_file = tmp_path / "secrets" / ".jwt_secret"
        secret_file.parent.mkdir(parents=True)
        secret_file.write_text("an-existing-real-secret", encoding="utf-8")
        os.chmod(secret_file, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

        assert _load_or_create_jwt_secret(tmp_path) == "an-existing-real-secret"
        assert stat.S_IMODE(secret_file.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR

    def test_missing_file_is_generated_and_persisted(self, tmp_path):
        from app.config import _load_or_create_jwt_secret

        secret = _load_or_create_jwt_secret(tmp_path)
        assert secret
        persisted = (tmp_path / "secrets" / ".jwt_secret").read_text(encoding="utf-8").strip()
        assert persisted == secret

# ---------------------------------------------------------------------------
# Deprecation warning regression check
# ---------------------------------------------------------------------------


class TestNoDeprecationWarnings:
    def test_no_per_request_cookies_pattern(self):
        """Verify test_auth_api.py doesn't use deprecated per-request cookies= param."""
        import ast
        from pathlib import Path

        test_file = Path(__file__)
        source = test_file.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "cookies":
                        raise AssertionError(f"Found cookies= keyword arg at line {kw.lineno}. " "Use client.cookies.set() instead.")


class TestSaltConcurrentCreationRace:
    def test_get_or_create_salt_recovers_from_duplicate_insert(self, db_session, monkeypatch):
        """Under SQLite WAL snapshot isolation the double-checked re-read can still
        see no salt while a concurrent transaction commits one, so our INSERT then
        collides with IntegrityError. _get_or_create_salt must roll back and return
        the salt from a fresh re-read rather than propagating the error."""
        import app.auth as auth_module
        from app import settings_store

        concurrent_salt = "winner-committed-salt"
        real_commit = auth_module.commit_with_lock
        state = {"n": 0}

        def _racy_commit(db):
            state["n"] += 1
            if state["n"] == 1:
                # Our duplicate INSERT is pending; simulate the concurrent winner
                # by discarding it, committing the winner's salt, then raising the
                # IntegrityError our colliding INSERT would have produced.
                db.rollback()
                settings_store.set_setting(db, auth_module.SALT_SETTING_KEY, concurrent_salt)
                real_commit(db)
                raise IntegrityError(
                    "INSERT INTO settings", {}, Exception("UNIQUE constraint failed: settings.key")
                )
            real_commit(db)

        monkeypatch.setattr(auth_module, "commit_with_lock", _racy_commit)

        result = auth_module._get_or_create_salt(db_session)

        assert result == concurrent_salt
        assert settings_store.get_setting(db_session, auth_module.SALT_SETTING_KEY) == concurrent_salt
        assert state["n"] == 1  # racy commit exercised; recovery re-read did not re-commit
