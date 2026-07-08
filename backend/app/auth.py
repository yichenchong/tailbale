"""Authentication utilities: password hashing, JWT tokens, and FastAPI dependency."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import settings_store
from app.config import settings
from app.database import commit_with_lock, db_write_section, get_db, rollback_with_lock
from app.models.user import User

COOKIE_NAME = "access_token"
SALT_SETTING_KEY = "password_salt"


def _get_or_create_salt(db: Session) -> str:
    """Get the password salt from the settings store, creating one if absent."""
    salt = settings_store.get_setting(db, SALT_SETTING_KEY)
    if salt:
        return salt

    with db_write_section(db):
        salt = settings_store.get_setting(db, SALT_SETTING_KEY)
        if not salt:
            salt = secrets.token_urlsafe(32)
            try:
                settings_store.set_setting(db, SALT_SETTING_KEY, salt)
                commit_with_lock(db)
            except IntegrityError:
                # Under SQLite WAL snapshot isolation our re-read above can still
                # see no salt while a concurrent transaction commits one, so this
                # INSERT collides. Roll back the aborted transaction and re-read on
                # a fresh snapshot, which now sees the committed salt.
                rollback_with_lock(db)
                salt = settings_store.get_setting(db, SALT_SETTING_KEY)
    return salt


def _prehash(salt: str, plain: str) -> bytes:
    """SHA-256 the salt+password to produce a fixed-length input for bcrypt."""
    return hashlib.sha256((salt + plain).encode()).hexdigest().encode()


def hash_password(plain: str, db: Session) -> str:
    """Hash a password with bcrypt, using a salted SHA-256 pre-hash."""
    salt = _get_or_create_salt(db)
    return bcrypt.hashpw(_prehash(salt, plain), bcrypt.gensalt()).decode()


# Computed once at import: a real bcrypt hash to verify against on the
# user-missing login path. Doing this at module load (rather than lazily on the
# first call) avoids both a first-call timing skew and an unguarded cross-thread
# write to a module global.
_DUMMY_BCRYPT_HASH = bcrypt.hashpw(b"timing-equalizer", bcrypt.gensalt())


def dummy_verify_password(plain: str, db: Session) -> None:
    """Run a bcrypt verification against a throwaway hash, discarding the result.

    Called on the login path when the target user is missing/inactive so the
    response takes a comparable amount of time to a real password check,
    preventing username enumeration via the bcrypt timing gap.
    """
    salt = _get_or_create_salt(db)
    bcrypt.checkpw(_prehash(salt, plain), _DUMMY_BCRYPT_HASH)


def verify_password(plain: str, hashed: str, db: Session) -> bool:
    """Verify a password against a bcrypt hash."""
    salt = _get_or_create_salt(db)
    try:
        return bcrypt.checkpw(_prehash(salt, plain), hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: str, token_version: int) -> str:
    """Create a JWT with the user ID as subject and a version claim.

    ``token_version`` is embedded as ``ver`` so bumping the user's counter (e.g.
    on password change) invalidates every previously issued token.
    """
    expire = datetime.now(UTC) + timedelta(hours=settings.jwt_expiry_hours)
    payload = {"sub": user_id, "ver": token_version, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _decode_token(token: str) -> dict | None:
    """Decode and validate a JWT, returning its claims payload or None."""
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def decode_access_token(token: str) -> str | None:
    """Decode a JWT token and return the user ID, or None if invalid."""
    payload = _decode_token(token)
    if payload is None:
        return None
    subject = payload.get("sub")
    return subject if subject else None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency that extracts and validates the session cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = _decode_token(token)
    user_id = payload.get("sub") if payload else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Stateless-JWT revocation: a token whose "ver" claim no longer matches the
    # user's token_version was issued before a credential change and is stale.
    if payload.get("ver") != user.token_version:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return user
