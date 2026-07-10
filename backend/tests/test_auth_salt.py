"""Password salt and password-hash storage behavior."""

from sqlalchemy.exc import IntegrityError

import app.auth as auth_module
import app.database as database_module
from app import settings_store
from app.models.user import User
from tests import auth_helpers
from tests.auth_helpers import setup_user

auth_client = auth_helpers.auth_client
_reset_login_rate_limiter = auth_helpers._reset_login_rate_limiter



class TestPasswordSalt:
    """Password hashing must use the persisted salt consistently."""

    def test_salt_is_generated_on_first_user(self, auth_client):
        setup_user(auth_client)
        # Login works, proving the salt was consistent between hash and verify.
        auth_client.cookies.clear()
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        assert resp.status_code == 200

    def test_password_not_stored_as_plaintext(self, auth_client):
        """The stored password_hash must be a bcrypt hash, never the plaintext."""
        plaintext = "mysecretpass123"
        setup_user(auth_client, password=plaintext)

        with database_module.SessionLocal() as db:
            stored = db.query(User).filter(User.username == "admin").first().password_hash

        assert stored != plaintext
        assert plaintext not in stored
        assert stored.startswith("$2")  # bcrypt hash id ($2a$/$2b$/$2y$)


class TestSaltConcurrentCreationRace:
    def test_get_or_create_salt_recovers_from_duplicate_insert(self, db_session, monkeypatch):
        """A duplicate salt insert must roll back and return the concurrent winner."""
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


class TestLongPasswordPrehash:
    """The salted SHA-256 pre-hash lets bcrypt accept passwords beyond its
    72-byte limit while preserving the whole password's contribution."""

    def test_password_longer_than_72_bytes_round_trips(self, db_session):
        # Modern bcrypt REJECTS a >72-byte password with ValueError; the pre-hash
        # collapses any length to a fixed 64-byte hex digest, so a long password
        # hashes and verifies without raising. A regression dropping the pre-hash
        # would 500 setup/change-password for such a password.
        long_pw = "A" * 100
        hashed = auth_module.hash_password(long_pw, db_session)
        assert auth_module.verify_password(long_pw, hashed, db_session) is True

    def test_prehash_defeats_bcrypt_72_byte_truncation(self, db_session):
        # Two passwords sharing a 72-byte prefix but differing only afterwards
        # must NOT cross-verify. Feeding bcrypt the raw password (no pre-hash) or a
        # manual ``[:72]`` truncation would collapse both to the same 72 bytes and
        # wrongly accept the impostor; the SHA-256 pre-hash covers the full input.
        prefix = "A" * 72
        real = prefix + "-real-suffix"
        impostor = prefix + "-impostor-suffix"
        hashed = auth_module.hash_password(real, db_session)
        assert auth_module.verify_password(impostor, hashed, db_session) is False
        assert auth_module.verify_password(real, hashed, db_session) is True


class TestVerifyPasswordMalformedHash:
    """verify_password must fail auth gracefully on a corrupt stored hash.

    bcrypt.checkpw raises ValueError on any non-bcrypt hash string ("Invalid
    salt"). A DB row whose password_hash was truncated/corrupted (botched
    backup restore, manual edit, legacy row) must therefore read as a failed
    credential (return False -> 401), never propagate the ValueError and 500
    the login endpoint. This pins the ``except ValueError`` guard.
    """

    def test_empty_hash_returns_false(self, db_session):
        assert auth_module.verify_password("securepassword123", "", db_session) is False

    def test_non_bcrypt_hash_returns_false(self, db_session):
        assert (
            auth_module.verify_password("securepassword123", "not-a-bcrypt-hash", db_session)
            is False
        )

    def test_truncated_bcrypt_hash_returns_false(self, db_session):
        # A real bcrypt hash chopped mid-string is no longer parseable.
        good = auth_module.hash_password("securepassword123", db_session)
        assert auth_module.verify_password("securepassword123", good[:20], db_session) is False
