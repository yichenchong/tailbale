"""Authentication router: login, logout, session check, and initial user setup."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
from app.database import commit_with_lock, db_write_section, get_db
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
from app.settings_store import get_setting
from app.setup_state import compute_setup_progress

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )

def _normalize_username(username: str) -> str:
    return username.strip()


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expiry_hours * 3600,
        path="/api",
    )


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == _normalize_username(body.username)).first()
    if not user or not user.is_active:
        # Run a dummy bcrypt check so unknown/inactive usernames take the same
        # time as a real verification, preventing username enumeration.
        dummy_verify_password(body.password, db)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(body.password, user.password_hash, db):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(user.id)
    _set_cookie(response, token)
    return LoginResponse(user=_user_response(user))


@router.post("/logout")
async def logout(response: Response, _user: User = Depends(get_current_user)):
    response.delete_cookie(key=COOKIE_NAME, path="/api")
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return _user_response(user)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the current user's password. Requires the current password."""
    if not verify_password(body.current_password, user.password_hash, db):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    with db_write_section(db):
        user.password_hash = hash_password(body.new_password, db)
        commit_with_lock(db)
    return {"ok": True}


@router.post("/setup-user", response_model=LoginResponse)
async def setup_user(
    body: SetupUserRequest, response: Response, db: Session = Depends(get_db)
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

        db.add(Setting(key="setup_user_claimed", value="true"))
        try:
            db.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A user already exists") from exc

        user = User(
            username=username,
            password_hash=password_hash,
            role="admin",
        )
        db.add(user)
        commit_with_lock(db)

    token = create_access_token(user.id)
    _set_cookie(response, token)
    return LoginResponse(user=_user_response(user))


@router.get("/setup-progress")
async def setup_progress(request: Request, db: Session = Depends(get_db)):
    """Report which setup steps have already been completed.

    Public before setup completes so the setup wizard can resume from the first
    incomplete step. After setup is complete, require a valid session because
    the progress payload discloses installation/configuration state.
    """
    if get_setting(db, "setup_complete") == "true":
        get_current_user(request, db)
    return compute_setup_progress(db)


@router.get("/status", response_model=AuthStatusResponse)
async def auth_status(request: Request, db: Session = Depends(get_db)):
    """Check setup and authentication status. Always accessible (no auth required)."""
    setup_complete = get_setting(db, "setup_complete") == "true"

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
