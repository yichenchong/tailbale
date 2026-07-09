"""Shared helpers for backend authentication tests."""

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database_module
import app.main as main_module
import app.routers.auth as auth_router
from app.auth import get_current_user
from app.database import Base, get_db


@pytest.fixture(autouse=True)
def _reset_login_rate_limiter() -> Iterator[None]:
    """Clear brute-force limiter state around tests that use the real auth router."""
    auth_router.reset_login_rate_limiter()
    yield
    auth_router.reset_login_rate_limiter()


@pytest.fixture()
def auth_client(tmp_data_dir) -> Iterator[TestClient]:
    """TestClient WITHOUT the suite-wide auth bypass, for auth endpoint tests."""
    original_engine = database_module.engine
    original_session_local = database_module.SessionLocal

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    database_module.engine = engine
    database_module.SessionLocal = sessionmaker(bind=engine)
    test_session = sessionmaker(bind=engine)

    def _override_get_db():
        session = test_session()
        try:
            yield session
        finally:
            session.close()

    main_module.app.dependency_overrides[get_db] = _override_get_db
    # Explicitly remove the default no-auth bypass so real auth is exercised.
    main_module.app.dependency_overrides.pop(get_current_user, None)
    with patch("app.edge.image_builder.ensure_edge_image"), TestClient(main_module.app) as client:
        yield client
    main_module.app.dependency_overrides.clear()
    database_module.engine = original_engine
    database_module.SessionLocal = original_session_local
    engine.dispose()


def setup_user(client, username: str = "admin", password: str = "securepassword123"):
    """Create the initial admin user through the public setup endpoint."""
    return client.post(
        "/api/auth/setup-user",
        json={"username": username, "password": password},
    )


def set_auth_cookie(client, cookie_value: str) -> None:
    """Set the access_token cookie on a client without per-request cookies=."""
    client.cookies.set("access_token", cookie_value)


def configure_setup_prerequisites(client) -> None:
    """Fill all setup prerequisites through the public settings endpoints."""
    client.put(
        "/api/settings/general",
        json={
            "base_domain": "example.com",
            "acme_email": "admin@example.com",
        },
    )
    client.put(
        "/api/settings/cloudflare",
        json={
            "zone_id": "zone123",
            "token": "cf-token",
        },
    )
    client.put(
        "/api/settings/tailscale",
        json={
            "auth_key": "tskey-auth-abc123",
            "api_key": "tskey-api-abc123",
        },
    )
    client.put(
        "/api/settings/docker",
        json={
            "socket_path": "unix:///var/run/docker.sock",
        },
    )
