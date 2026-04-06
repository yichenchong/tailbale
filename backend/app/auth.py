"""Authentication utilities: password hashing, JWT tokens, and FastAPI dependency."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User

COOKIE_NAME = "access_token"
SALT_SETTING_KEY = "password_salt"


def _get_or_create_salt(db: Session) -> str:
    """Get the password salt from the settings store, creating one if absent."""
    from app.settings_store import get_setting, set_setting

    salt = get_setting(db, SALT_SETTING_KEY)
    if not salt:
        salt = secrets.token_urlsafe(32)
        set_setting(db, SALT_SETTING_KEY, salt)
    return salt


def _prehash(salt: str, plain: str) -> bytes:
    """SHA-256 the salt+password to produce a fixed-length input for bcrypt."""
    return hashlib.sha256((salt + plain).encode()).hexdigest().encode()


def hash_password(plain: str, db: Session) -> str:
    """Hash a password with bcrypt, using a salted SHA-256 pre-hash."""
    salt = _get_or_create_salt(db)
    return bcrypt.hashpw(_prehash(salt, plain), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str, db: Session) -> bool:
    """Verify a password against a bcrypt hash."""
    salt = _get_or_create_salt(db)
    return bcrypt.checkpw(_prehash(salt, plain), hashed.encode())


def create_access_token(user_id: str) -> str:
    """Create a JWT token with the user ID as subject."""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> str | None:
    """Decode a JWT token and return the user ID, or None if invalid."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload.get("sub")
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency that extracts and validates the session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user
