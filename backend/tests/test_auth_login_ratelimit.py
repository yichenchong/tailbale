"""Login rate-limiter endpoint and policy behavior."""

from app.config import settings
from app.login_ratelimit import _LoginRateLimiter
from tests import auth_helpers
from tests.auth_helpers import setup_user

auth_client = auth_helpers.auth_client
_reset_login_rate_limiter = auth_helpers._reset_login_rate_limiter



class TestLoginRateLimit:
    """Brute-force protection on POST /api/auth/login."""

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
        setup_user(auth_client)
        auth_client.cookies.clear()
        threshold = settings.login_max_failures
        for _ in range(threshold - 1):
            assert self._wrong(auth_client).status_code == 401
        locked = self._wrong(auth_client)
        assert locked.status_code == 429
        assert int(locked.headers["Retry-After"]) > 0

    def test_attempt_during_cooldown_is_rejected_429(self, auth_client):
        setup_user(auth_client)
        auth_client.cookies.clear()
        for _ in range(settings.login_max_failures):
            self._wrong(auth_client)
        # Already locked: a further probe is refused before bcrypt runs.
        again = self._wrong(auth_client)
        assert again.status_code == 429
        assert int(again.headers["Retry-After"]) > 0
        # Even a correct password is refused while the cooldown is active.
        assert self._right(auth_client).status_code == 429

    def test_successful_login_resets_failure_counter(self, auth_client):
        setup_user(auth_client)
        auth_client.cookies.clear()
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
        for _ in range(settings.login_max_failures + 1):
            self._wrong(auth_client)
        resp = setup_user(auth_client)
        assert resp.status_code == 200

    def test_unknown_usernames_from_one_client_trip_lockout(self, auth_client):
        """Username spraying from a single client must still trip per-IP lockout."""
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
    """The memory backstop must bound the table without releasing active lockouts."""

    def test_hard_cap_does_not_release_active_lockout(self):
        clock = {"t": 1000.0}
        limiter = _LoginRateLimiter(max_failures=3, cooldown_seconds=300, max_entries=3)
        limiter._now = lambda: clock["t"]  # deterministic, tie-free timestamps

        # Lock the victim at t=1000 (3 consecutive failures with max_failures=3).
        for _ in range(3):
            limiter.record_failure("victim")
        assert limiter.retry_after("victim") is not None

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
        # Re-audit invariant: even when EVERY tracked client is actively locked,
        # the table must stay hard-capped and shed the oldest lockouts.
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
    """A lockout must expire on its own once the cooldown elapses."""

    def test_lockout_clears_after_cooldown_and_next_failure_starts_fresh(self):
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
