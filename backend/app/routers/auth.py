"""Authentication router: login, logout, session check, and initial user setup."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import login_ratelimit, settings_store
from app.auth import (
    COOKIE_NAME,
    create_access_token,
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


_LoginAttempts = login_ratelimit._LoginAttempts
_LoginRateLimiter = login_ratelimit._LoginRateLimiter
_login_rate_limiter = login_ratelimit._login_rate_limiter


def reset_login_rate_limiter() -> None:
    """Compatibility hook so tests can clear brute-force state between cases."""
    _login_rate_limiter.reset()


def _client_host(request: Request) -> str:
    return login_ratelimit.client_host(request)


def _too_many_attempts(retry_after: int) -> HTTPException:
    return login_ratelimit.too_many_attempts(retry_after)


def _reject_failed_login(client: str) -> None:
    login_ratelimit.reject_failed_login(client, _login_rate_limiter)


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

    # Mirror the full get_current_user validation (signature/expiry, active user,
    # AND the token_version revocation check) so a stale-version token — one
    # issued before a password change on another device — is never reported as
    # authenticated while every protected endpoint 401s it.
    authenticated = False
    if request.cookies.get(COOKIE_NAME):
        try:
            get_current_user(request, db)
            authenticated = True
        except HTTPException:
            pass

    return AuthStatusResponse(setup_complete=setup_complete, authenticated=authenticated)
