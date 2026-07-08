import logging
import threading
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


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


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

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
    existing tables and (1) adds any new nullable columns the ORM models
    declare but the DB lacks, and (2) creates indexes that newer model
    revisions added.  Every step is idempotent and safe to run repeatedly.

    Contract: Additive migrations only (nullable ADD COLUMN, CREATE INDEX IF
    NOT EXISTS); non-additive changes (drops, renames, type changes, NOT NULL,
    backfills) require a manual one-off and are intentionally NOT supported here
    -- there is no migration framework.
    """
    eng = target_engine if target_engine is not None else engine

    _MIGRATIONS: list[tuple[str, str, str]] = [
        # (table, column, SQL type)
        ("service_status", "probe_retry_at", "DATETIME"),
        ("service_status", "probe_retry_attempt", "INTEGER"),
        ("service_status", "last_probe_at", "DATETIME"),
        ("users", "token_version", "INTEGER NOT NULL DEFAULT 0"),
    ]
    # (table, index name, column) — index name matches SQLAlchemy's default
    # ``ix_<table>_<column>`` so it is a no-op on databases already created
    # from the current models. __tablename__ values are read from the models.
    from app.models.certificate import Certificate
    from app.models.event import Event
    from app.models.job import Job
    from app.models.service_status import ServiceStatus

    _INDEX_MIGRATIONS: list[tuple[str, str, str]] = [
        (Event.__tablename__, f"ix_{Event.__tablename__}_service_id", "service_id"),
        (Event.__tablename__, f"ix_{Event.__tablename__}_created_at", "created_at"),
        (Event.__tablename__, f"ix_{Event.__tablename__}_kind", "kind"),
        (Job.__tablename__, f"ix_{Job.__tablename__}_service_id", "service_id"),
        (Job.__tablename__, f"ix_{Job.__tablename__}_status", "status"),
        (Job.__tablename__, f"ix_{Job.__tablename__}_kind", "kind"),
        (Job.__tablename__, f"ix_{Job.__tablename__}_created_at", "created_at"),
        (
            Certificate.__tablename__,
            f"ix_{Certificate.__tablename__}_expires_at",
            "expires_at",
        ),
        (
            ServiceStatus.__tablename__,
            f"ix_{ServiceStatus.__tablename__}_phase",
            "phase",
        ),
    ]

    insp = inspect(eng)
    with eng.begin() as conn:
        for table, column, sql_type in _MIGRATIONS:
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if column not in existing:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                ))
                logger.info("Migration: added %s.%s (%s)", table, column, sql_type)

        for table, index_name, column in _INDEX_MIGRATIONS:
            if not insp.has_table(table):
                continue
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"
            ))
