"""Authentication router: login, logout, session check, and initial user setup."""

import threading
import time
from dataclasses import dataclass
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import settings_store
from app.auth import (
    COOKIE_NAME,
    create_access_token,
    decode_access_token,
    dummy_verify_password,
    get_current_user,
    hash_password,
    verify_password,
)
from app.config import settings
from app.database import commit_with_lock, db_write_section, flush_with_lock, get_db
from app.models.setting import Setting
from app.models.user import User
from app.schemas.auth import (
    AuthStatusResponse,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    SetupUserRequest,
    UserResponse,
)
from app.setup_state import compute_setup_progress

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
        """Register a failed login. Returns the lockout seconds if this failure
        tripped (or is within) the threshold, else ``None``."""
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


def _client_host(request: Request) -> str:
    """Best-effort client identity. Behind Caddy/Tailscale this may be a proxy
    IP, which is acceptable for a single-admin tool."""
    return request.client.host if request.client else "unknown"


def _too_many_attempts(retry_after: int) -> HTTPException:
    """Build the shared 429 lockout response so the detail message and the
    ``Retry-After`` header stay identical across the pre-check (``retry_after``)
    and the failure-recording (``record_failure``) paths."""
    return HTTPException(
        status_code=429,
        detail="Too many failed login attempts. Try again later.",
        headers={"Retry-After": str(retry_after)},
    )


def _reject_failed_login(client: str) -> None:
    """Record a failed attempt and raise 401, or 429 once the client is locked."""
    retry_after = _login_rate_limiter.record_failure(client)
    if retry_after is not None:
        raise _too_many_attempts(retry_after)
    raise HTTPException(status_code=401, detail="Invalid credentials")

def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )

def _normalize_username(username: str) -> str:
    return username.strip()


def _is_secure_request(request: Request) -> bool:
    """Whether the inbound request arrived over HTTPS, directly or via a proxy."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def _set_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure or _is_secure_request(request),
        max_age=settings.jwt_expiry_hours * 3600,
        path="/api",
    )


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    client = _client_host(request)
    # Checked before any password comparison so a locked-out client can't keep
    # probing the (deliberately slow) bcrypt path.
    retry_after = _login_rate_limiter.retry_after(client)
    if retry_after is not None:
        raise _too_many_attempts(retry_after)

    user = db.query(User).filter(User.username == _normalize_username(body.username)).first()
    if not user or not user.is_active:
        # Run a dummy bcrypt check so unknown/inactive usernames take the same
        # time as a real verification, preventing username enumeration.
        dummy_verify_password(body.password, db)
        _reject_failed_login(client)
    if not verify_password(body.password, user.password_hash, db):
        _reject_failed_login(client)

    # Successful login: clear any accumulated failure streak so the suite's
    # (mostly successful) logins never trip the limiter.
    _login_rate_limiter.record_success(client)
    token = create_access_token(user.id, user.token_version)
    _set_cookie(response, token, request)
    return LoginResponse(user=_user_response(user))


@router.post("/logout")
def logout(response: Response, _user: User = Depends(get_current_user)):
    response.delete_cookie(key=COOKIE_NAME, path="/api")
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return _user_response(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the current user's password. Requires the current password.

    Bumping ``token_version`` invalidates every JWT issued under the old version;
    a fresh cookie is re-issued for the acting session so the admin performing
    the change stays logged in while all other outstanding tokens are rejected.
    """
    if not verify_password(body.current_password, user.password_hash, db):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    with db_write_section(db):
        user.password_hash = hash_password(body.new_password, db)
        user.token_version += 1
        commit_with_lock(db)
    token = create_access_token(user.id, user.token_version)
    _set_cookie(response, token, request)
    return {"ok": True}


@router.post("/setup-user", response_model=LoginResponse)
def setup_user(
    body: SetupUserRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    """Create the initial admin user. Only works when no users exist yet."""
    username = _normalize_username(body.username)
    if not username:
        raise HTTPException(status_code=422, detail="Username must not be empty")

    # Cheap pre-check: once an admin exists this endpoint is a no-op, so reject
    # before the expensive bcrypt hash to avoid unauthenticated CPU amplification.
    if db.query(User.id).first() is not None:
        raise HTTPException(status_code=409, detail="A user already exists")

    password_hash = hash_password(body.password, db)
    with db_write_section(db):
        existing = db.query(User).first()
        if existing:
            raise HTTPException(status_code=409, detail="A user already exists")

        if db.get(Setting, "setup_user_claimed") is None:
            db.add(Setting(key="setup_user_claimed", value="true"))
            try:
                flush_with_lock(db)
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="A user already exists") from exc

        user = User(
            username=username,
            password_hash=password_hash,
            role="admin",
        )
        db.add(user)
        commit_with_lock(db)

    token = create_access_token(user.id, user.token_version)
    _set_cookie(response, token, request)
    return LoginResponse(user=_user_response(user))


@router.get("/setup-progress")
def setup_progress(request: Request, db: Session = Depends(get_db)):
    """Report which setup steps have already been completed.

    Public before setup completes so the setup wizard can resume from the first
    incomplete step. After setup is complete, require a valid session because
    the progress payload discloses installation/configuration state.
    """
    if settings_store.get_setting(db, "setup_complete") == "true":
        get_current_user(request, db)
    return compute_setup_progress(db)


@router.get("/status", response_model=AuthStatusResponse)
def auth_status(request: Request, db: Session = Depends(get_db)):
    """Check setup and authentication status. Always accessible (no auth required)."""
    setup_complete = settings_store.get_setting(db, "setup_complete") == "true"

    authenticated = False
    token = request.cookies.get(COOKIE_NAME)
    if token:
        user_id = decode_access_token(token)
        if user_id:
            user = (
                db.query(User)
                .filter(User.id == user_id, User.is_active.is_(True))
                .first()
            )
            authenticated = user is not None

    return AuthStatusResponse(
        setup_complete=setup_complete, authenticated=authenticated
    )
