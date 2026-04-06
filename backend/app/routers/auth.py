"""Authentication router: login, logout, session check, and initial user setup."""

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import (
    AuthStatusResponse,
    LoginRequest,
    LoginResponse,
    SetupUserRequest,
    UserResponse,
)
from app.settings_store import get_setting

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


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
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not user.is_active:
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


@router.post("/setup-user", response_model=LoginResponse)
async def setup_user(
    body: SetupUserRequest, response: Response, db: Session = Depends(get_db)
):
    """Create the initial admin user. Only works when no users exist yet."""
    existing = db.query(User).first()
    if existing:
        raise HTTPException(status_code=409, detail="A user already exists")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password, db),
        role="admin",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id)
    _set_cookie(response, token)
    return LoginResponse(user=_user_response(user))


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
