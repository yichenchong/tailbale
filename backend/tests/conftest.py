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
    database_module.engine = db_engine

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
