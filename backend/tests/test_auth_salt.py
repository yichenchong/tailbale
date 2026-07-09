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
