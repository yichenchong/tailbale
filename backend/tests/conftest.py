from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import docker
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database_module
from app import config as config_module
from app import secrets as secrets_module
from app import settings_store as settings_store_module
from app.auth import get_current_user
from app.database import Base, get_db
from app.main import app
from app.models.user import User


@pytest.fixture()
def tmp_data_dir(tmp_path):
    """Provide a temporary data directory and patch app.config.settings."""
    config = config_module

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
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(tmp_data_dir, db_engine):
    """Create a database session from the in-memory engine."""
    TestSession = sessionmaker(bind=db_engine)
    session = TestSession()
    yield session
    session.close()


@contextmanager
def _client_for_engine(db_engine, *, bypass_auth: bool):
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


    app.dependency_overrides[get_db] = _override_get_db
    if bypass_auth:
        # Bypass auth for tests that are not exercising authentication itself.
        dummy_user = User(
            id="usr_testuser0001",
            username="testadmin",
            password_hash="not-a-real-hash",
            role="admin",
        )
        app.dependency_overrides[get_current_user] = lambda: dummy_user

    try:
        # The lifespan starts a best-effort edge-image build; stub it so tests never
        # open a real Docker socket (no daemon under test — the SDK leaks its probe
        # socket on a refused connection).
        with patch("app.edge.image_builder.ensure_edge_image"), TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        database_module.engine = original_engine
        database_module.SessionLocal = original_session_local


@pytest.fixture()
def client(tmp_data_dir, db_engine):
    """FastAPI TestClient with overridden DB dependency, temp data dir, and auth bypass."""
    with _client_for_engine(db_engine, bypass_auth=True) as c:
        yield c


@pytest.fixture()
def auth_client(tmp_data_dir, db_engine):
    """FastAPI TestClient with the test DB/temp data dir but no auth bypass."""
    with _client_for_engine(db_engine, bypass_auth=False) as c:
        yield c


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


def make_fake_docker_client(*, containers=None, networks=None, images=None):
    """Build a lightweight MagicMock Docker client graph."""
    client = MagicMock()
    client.containers = containers if containers is not None else MagicMock()
    client.networks = networks if networks is not None else MagicMock()
    client.images = images if images is not None else MagicMock()
    return client


@pytest.fixture()
def fake_docker_client_factory():
    """Factory for opt-in Docker client mocks."""
    return make_fake_docker_client


@pytest.fixture()
def configured_cloudflare(tmp_data_dir):
    """Helper to store Cloudflare test settings and token in the temp data dir."""
    def _configure(db, *, zone_id="zone123", token="cf-token", commit=True):
        settings_store_module.set_setting(db, "cf_zone_id", zone_id)
        if token is not None:
            secrets_module.write_secret(secrets_module.CLOUDFLARE_TOKEN, token)
        if commit:
            db.commit()
        return {"zone_id": zone_id, "token": token}

    return _configure


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


@pytest.fixture(autouse=True)
def _no_real_docker():
    """Guarantee no test ever opens a real Docker connection.

    Without a local mock, ``docker.DockerClient(...)`` / ``.from_env()`` would try
    to reach the daemon and — with none present under test — leak the SDK's probe
    socket. Raising DockerException matches the no-daemon outcome tests already
    rely on, but opens nothing. Tests needing a working client patch it themselves,
    which overrides this default for their scope.
    """

    def _refuse(*_args, **_kwargs):
        raise docker.errors.DockerException("Docker access is disabled under test")

    with patch("docker.DockerClient", side_effect=_refuse) as mock_cls:
        mock_cls.from_env = MagicMock(side_effect=_refuse)
        yield
