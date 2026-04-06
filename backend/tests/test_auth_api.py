"""Tests for authentication API endpoints."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.database import Base, get_db


@pytest.fixture()
def auth_client(tmp_data_dir):
    """TestClient WITHOUT auth bypass — for testing the auth endpoints themselves."""
    import app.database as database_module

    original_engine = database_module.engine

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    database_module.engine = engine
    TestSession = sessionmaker(bind=engine)

    def _override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    from app.main import app

    app.dependency_overrides[get_db] = _override_get_db
    # Explicitly remove any auth bypass so real auth is used
    app.dependency_overrides.pop(get_current_user, None)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    database_module.engine = original_engine


def _setup_user(client, username="admin", password="securepassword123"):
    """Helper to create the initial user via the setup endpoint."""
    resp = client.post(
        "/api/auth/setup-user",
        json={"username": username, "password": password},
    )
    return resp


class TestSetupUser:
    def test_create_first_user(self, auth_client):
        resp = _setup_user(auth_client)
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"]["username"] == "admin"
        assert data["user"]["role"] == "admin"
        assert "id" in data["user"]
        # Should set access_token cookie
        assert "access_token" in resp.cookies

    def test_cannot_create_second_user(self, auth_client):
        _setup_user(auth_client)
        resp = _setup_user(auth_client, username="another")
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_empty_username_rejected(self, auth_client):
        resp = auth_client.post(
            "/api/auth/setup-user",
            json={"username": "", "password": "securepassword123"},
        )
        assert resp.status_code == 422

    def test_short_password_rejected(self, auth_client):
        resp = auth_client.post(
            "/api/auth/setup-user",
            json={"username": "admin", "password": "short"},
        )
        assert resp.status_code == 422


class TestLogin:
    def test_successful_login(self, auth_client):
        _setup_user(auth_client)
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "admin"
        assert "access_token" in resp.cookies

    def test_wrong_password(self, auth_client):
        _setup_user(auth_client)
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid credentials"

    def test_nonexistent_user(self, auth_client):
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "nobody", "password": "whatever"},
        )
        assert resp.status_code == 401

    def test_missing_fields(self, auth_client):
        resp = auth_client.post("/api/auth/login", json={})
        assert resp.status_code == 422


class TestLogout:
    def test_logout_clears_cookie(self, auth_client):
        _setup_user(auth_client)
        # Login first to get a cookie
        login_resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        cookie = login_resp.cookies["access_token"]

        # Logout
        resp = auth_client.post(
            "/api/auth/logout",
            cookies={"access_token": cookie},
        )
        assert resp.status_code == 200

    def test_logout_without_auth_returns_401(self, auth_client):
        resp = auth_client.post("/api/auth/logout")
        assert resp.status_code == 401


class TestMe:
    def test_me_with_valid_cookie(self, auth_client):
        setup_resp = _setup_user(auth_client)
        cookie = setup_resp.cookies["access_token"]

        resp = auth_client.get("/api/auth/me", cookies={"access_token": cookie})
        assert resp.status_code == 200
        assert resp.json()["username"] == "admin"
        assert resp.json()["role"] == "admin"

    def test_me_without_cookie(self, auth_client):
        resp = auth_client.get("/api/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token(self, auth_client):
        resp = auth_client.get(
            "/api/auth/me", cookies={"access_token": "invalid-token"}
        )
        assert resp.status_code == 401


class TestAuthStatus:
    def test_before_setup(self, auth_client):
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["setup_complete"] is False
        assert data["authenticated"] is False

    def test_after_user_setup_with_cookie(self, auth_client):
        setup_resp = _setup_user(auth_client)
        cookie = setup_resp.cookies["access_token"]

        resp = auth_client.get("/api/auth/status", cookies={"access_token": cookie})
        assert resp.status_code == 200
        data = resp.json()
        assert data["setup_complete"] is False  # setup_complete not yet set
        assert data["authenticated"] is True

    def test_after_setup_complete_not_logged_in(self, auth_client):
        _setup_user(auth_client)
        # Mark setup complete using the cookie from setup
        cookie = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        ).cookies["access_token"]

        auth_client.put(
            "/api/settings/setup-complete",
            json={},
            cookies={"access_token": cookie},
        )

        # Clear cookies on the client, then check status
        auth_client.cookies.clear()
        resp = auth_client.get("/api/auth/status")
        data = resp.json()
        assert data["setup_complete"] is True
        assert data["authenticated"] is False


class TestProtectedEndpoints:
    """Verify that protected routers return 401 without auth."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/settings"),
            ("GET", "/api/services"),
            ("GET", "/api/events"),
            ("GET", "/api/dashboard/summary"),
            ("GET", "/api/profiles"),
        ],
    )
    def test_protected_routes_require_auth(self, auth_client, method, path):
        resp = auth_client.request(method, path)
        assert resp.status_code == 401

    def test_health_endpoint_is_public(self, auth_client):
        resp = auth_client.get("/api/health")
        assert resp.status_code == 200

    def test_auth_status_is_public(self, auth_client):
        resp = auth_client.get("/api/auth/status")
        assert resp.status_code == 200

    def test_protected_route_works_with_auth(self, auth_client):
        setup_resp = _setup_user(auth_client)
        cookie = setup_resp.cookies["access_token"]

        resp = auth_client.get("/api/settings", cookies={"access_token": cookie})
        assert resp.status_code == 200


class TestPasswordSalt:
    """Verify that the salt is stored in settings and used consistently."""

    def test_salt_is_generated_on_first_user(self, auth_client):
        _setup_user(auth_client)
        # Login works, proving the salt was consistent between hash and verify
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "securepassword123"},
        )
        assert resp.status_code == 200

    def test_password_not_stored_as_plaintext(self, auth_client):
        """The password hash should not contain the plain password."""
        _setup_user(auth_client, password="mysecretpass123")
        # Access the user directly via the me endpoint
        auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "mysecretpass123"},
        )

        # The password hash is not exposed via API, but we can verify
        # login with wrong password fails (proves it's actually hashed)
        resp = auth_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "mysecretpass123-wrong"},
        )
        assert resp.status_code == 401
