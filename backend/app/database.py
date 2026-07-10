import threading
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app import migrations as migration_runner
from app.config import settings


class Base(DeclarativeBase):
    pass


engine: Engine | None = None
SessionLocal: sessionmaker | None = None


def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def init_engine() -> None:
    """Build the SQLAlchemy engine and session factory, assigning module globals.

    Idempotent: a second call is a no-op once the engine exists.
    """
    global engine, SessionLocal
    if engine is not None:
        return
    engine = create_engine(
        f"sqlite:///{settings.db_path}",
        connect_args={"check_same_thread": False},
        # AnyIO caps sync request handlers at ~40 threads, each holding one session
        # for the life of its request. The background loops (reconcile sweep,
        # renewal scan, probe-retry, enable/update-edge) run on their OWN threads
        # and each open a separate session, so at request saturation a background
        # checkout would be the 41st and queue/time out behind the request threads.
        # Size the pool ABOVE 40 (10 + 40 = 50 total) to give those loops genuine
        # headroom. Writes still serialize via _DB_WRITE_MUTEX.
        pool_size=10,
        max_overflow=40,
        pool_timeout=30,
        echo=False,
    )
    event.listen(engine, "connect", _set_sqlite_pragma)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)

# Tier-3 (innermost) lock in the process-wide lock order; see app.locks for the
# canonical tiering/AB-BA invariant. Acquire AFTER any tier-1/2 lock, never before.
_DB_WRITE_MUTEX = threading.RLock()


@contextmanager
def db_write_lock():
    with _DB_WRITE_MUTEX:
        yield


def rollback_with_lock(db: Session) -> None:
    with db_write_lock():
        db.rollback()


@contextmanager
def db_write_section(db: Session):
    with db_write_lock():
        try:
            with db.no_autoflush:
                yield
        except Exception:
            rollback_with_lock(db)
            raise


def commit_with_lock(db: Session) -> None:
    with db_write_lock():
        try:
            db.commit()
        except Exception:
            rollback_with_lock(db)
            raise


def flush_with_lock(db: Session) -> None:
    with db_write_lock():
        try:
            db.flush()
        except Exception:
            rollback_with_lock(db)
            raise


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """Yield a fresh Session and guarantee it is closed.

    The off-request counterpart to :func:`get_db`: for background threads,
    ``asyncio.to_thread`` workers, and the reconcile/health/cert loops that open
    their own session instead of receiving the request-scoped one. It only owns
    the session lifecycle (create + close); it deliberately does NOT commit or
    roll back, so callers keep managing transactions through the ``db_write_*``
    lock helpers exactly as before.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations(target_engine: Engine | None = None) -> None:
    """Apply lightweight schema migrations for changes made after initial release.

    SQLAlchemy's ``create_all`` only creates missing *tables*, not missing
    columns or indexes on tables that already exist.  This function inspects
    existing tables and (1) adds any new columns the ORM models declare but the
    DB lacks -- nullable, or NOT NULL with a DEFAULT so SQLite backfills existing
    rows safely (e.g. ``users.token_version`` -> ``INTEGER NOT NULL DEFAULT 0``)
    -- and (2) creates indexes that newer model revisions added.  Every step is
    idempotent and safe to run repeatedly.

    Contract: Additive migrations only (ADD COLUMN that is nullable or carries a
    DEFAULT, CREATE INDEX IF NOT EXISTS); non-additive changes (drops, renames,
    type changes, adding a NOT NULL column WITHOUT a default, backfills) require a
    manual one-off and are intentionally NOT supported here
    -- there is no migration framework.
    """
    eng = target_engine if target_engine is not None else engine
    migration_runner.run_schema_migrations(eng)
