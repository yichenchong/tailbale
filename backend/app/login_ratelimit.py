"""Login brute-force rate limiting helpers."""

import threading
import time
from dataclasses import dataclass
from math import ceil

from fastapi import HTTPException, Request

from app.config import settings


@dataclass
class _LoginAttempts:
    """Per-client brute-force bookkeeping. Times use ``time.monotonic``."""

    failures: int = 0
    locked_until: float = 0.0
    last_seen: float = 0.0


class _LoginRateLimiter:
    """In-process, thread-safe brute-force limiter for the admin login.

    Sync endpoints run in Starlette's threadpool, so the shared dict is guarded
    by a ``threading.Lock``. State is keyed on the client host. A successful
    login clears the client's entry; ``max_failures`` consecutive failures lock
    the client out for ``cooldown_seconds``. Stale entries are evicted so an
    attacker rotating source IPs cannot grow the dict without bound; a hard
    ``max_entries`` cap backstops a flood that outpaces TTL eviction.
    """

    def __init__(
        self,
        max_failures: int,
        cooldown_seconds: int,
        *,
        max_entries: int = 4096,
    ) -> None:
        self._max_failures = max(1, max_failures)
        self._cooldown = max(1, cooldown_seconds)
        # An idle entry is forgotten once it is unlocked and untouched for a
        # full cooldown window: long enough to keep "consecutive" meaningful,
        # short enough to free memory promptly.
        self._idle_ttl = self._cooldown
        self._max_entries = max(1, max_entries)
        self._lock = threading.Lock()
        self._entries: dict[str, _LoginAttempts] = {}

    def _now(self) -> float:
        return time.monotonic()

    def _evict(self, now: float) -> None:
        """Drop unlocked, idle entries; hard-cap total size (LRU by last_seen)."""
        stale = [
            host
            for host, e in self._entries.items()
            if e.locked_until <= now and now - e.last_seen >= self._idle_ttl
        ]
        for host in stale:
            del self._entries[host]
        if len(self._entries) > self._max_entries:
            overflow = len(self._entries) - self._max_entries
            # Evict the least-recently-seen entries, but never sacrifice an
            # ACTIVE lockout ahead of an unlocked one: a locked entry's
            # last_seen is frozen at lock time (probes during the cooldown are
            # refused before any update), so a pure last_seen LRU would evict
            # the locked-out attacker first and reset their cooldown. Unlocked
            # entries (sort key False) go first; locked ones only if the cap
            # still demands it (more concurrent lockouts than max_entries).
            victims = sorted(
                self._entries,
                key=lambda h: (
                    self._entries[h].locked_until > now,
                    self._entries[h].last_seen,
                ),
            )[:overflow]
            for host in victims:
                del self._entries[host]

    def retry_after(self, client: str) -> int | None:
        """Seconds remaining if ``client`` is locked out, else ``None``.

        Called before any password comparison so a locked-out client cannot keep
        probing the bcrypt path.
        """
        now = self._now()
        with self._lock:
            self._evict(now)
            entry = self._entries.get(client)
            if entry and entry.locked_until > now:
                return ceil(entry.locked_until - now)
            return None

    def record_failure(self, client: str) -> int | None:
        """Register a failed login.

        Returns the lockout seconds if this failure tripped (or is within) the
        threshold, else ``None``.
        """
        now = self._now()
        with self._lock:
            self._evict(now)
            entry = self._entries.get(client)
            if entry is None:
                entry = _LoginAttempts()
                self._entries[client] = entry
            # A failure after the previous lockout expired starts a fresh streak.
            if entry.locked_until and entry.locked_until <= now:
                entry.failures = 0
                entry.locked_until = 0.0
            entry.failures += 1
            entry.last_seen = now
            if entry.failures >= self._max_failures:
                entry.locked_until = now + self._cooldown
                return ceil(self._cooldown)
            return None

    def record_success(self, client: str) -> None:
        """Clear a client's failure streak after a successful login."""
        with self._lock:
            self._entries.pop(client, None)

    def reset(self) -> None:
        """Drop all state. For deterministic tests."""
        with self._lock:
            self._entries.clear()


_login_rate_limiter = _LoginRateLimiter(
    max_failures=settings.login_max_failures,
    cooldown_seconds=settings.login_lockout_seconds,
)


def reset_login_rate_limiter() -> None:
    """Module-level hook so tests can clear brute-force state between cases."""
    _login_rate_limiter.reset()


def client_host(request: Request) -> str:
    """Best-effort client identity.

    Behind Caddy/Tailscale this may be a proxy IP, which is acceptable for a
    single-admin tool.
    """
    return request.client.host if request.client else "unknown"


def too_many_attempts(retry_after: int) -> HTTPException:
    """Build the shared 429 lockout response."""
    return HTTPException(
        status_code=429,
        detail="Too many failed login attempts. Try again later.",
        headers={"Retry-After": str(retry_after)},
    )


def reject_failed_login(
    client: str,
    limiter: _LoginRateLimiter | None = None,
) -> None:
    """Record a failed attempt and raise 401, or 429 once the client is locked."""
    active_limiter = _login_rate_limiter if limiter is None else limiter
    retry_after = active_limiter.record_failure(client)
    if retry_after is not None:
        raise too_many_attempts(retry_after)
    raise HTTPException(status_code=401, detail="Invalid credentials")
