from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.database import Base, get_db
from app.models.user import User


@pytest.fixture()
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory and patch app.config.settings."""
    from app import config

    original = config.settings.data_dir
    config.settings.data_dir = tmp_path
    config.settings.ensure_dirs()
    yield tmp_path
    config.settings.data_dir = original


@pytest.fixture()
def db_engine():
    """Create an in-memory SQLite engine with foreign key support.

    Uses StaticPool so all sessions share the same in-memory database.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def db_session(tmp_data_dir, db_engine):
    """Create a database session from the in-memory engine."""
    TestSession = sessionmaker(bind=db_engine)
    session = TestSession()
    yield session
    session.close()


@pytest.fixture()
def client(tmp_data_dir, db_engine):
    """FastAPI TestClient with overridden DB dependency and temp data dir."""
    import app.database as database_module

    original_engine = database_module.engine
    original_session_local = database_module.SessionLocal
    database_module.engine = db_engine
    database_module.SessionLocal = sessionmaker(bind=db_engine)

    TestSession = sessionmaker(bind=db_engine)

    def _override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    from app.main import app

    # Bypass auth for all existing tests — they aren't testing authentication
    dummy_user = User(
        id="usr_testuser0001",
        username="testadmin",
        password_hash="not-a-real-hash",
        role="admin",
    )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: dummy_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    database_module.engine = original_engine
    database_module.SessionLocal = original_session_local


def _make_fake_container(name: str = "fakecontainer", exposed_ports=None, **attrs_overrides):
    """Build a MagicMock that looks like a Docker container.

    If *exposed_ports* is None (default), the container reports no exposed
    ports, so port validation is permissive (any port is accepted).
    Pass a dict like ``{"80/tcp": {}}`` to restrict to specific ports.
    """
    c = MagicMock()
    c.name = name
    c.attrs = {
        "Config": {
            "ExposedPorts": exposed_ports or {},
        },
        "HostConfig": {"PortBindings": {}},
    }
    c.attrs.update(attrs_overrides)
    c.status = "running"
    c.labels = {}
    return c


@pytest.fixture(autouse=True)
def _mock_upstream_validation():
    """Auto-mock upstream container/port validation in create_service.

    This is autouse because almost every test that creates a service via the API
    would otherwise fail with a 422/503 (upstream container not found).
    Individual tests that need to exercise the real validation can simply
    override this fixture or patch ``_validate_upstream`` at a narrower scope.
    """
    with patch("app.routers.services._validate_upstream"):
        yield


@pytest.fixture(autouse=True)
def _mock_background_reconcile():
    """Auto-mock the background reconciliation triggered after service creation.

    Without this, every test that creates a service would trigger a real
    reconcile_one() call that tries to connect to Docker/Tailscale.
    """
    with patch("app.reconciler.reconcile_loop.reconcile_one"):
        yield
