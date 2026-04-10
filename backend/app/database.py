import logging

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()

SessionLocal = sessionmaker(bind=engine)


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Apply lightweight schema migrations for columns added after initial release.

    SQLAlchemy's ``create_all`` only creates missing *tables*, not missing
    columns.  This function inspects existing tables and adds any new
    nullable columns that the ORM models declare but the DB lacks.
    """
    _MIGRATIONS: list[tuple[str, str, str]] = [
        # (table, column, SQL type)
        ("service_status", "probe_retry_at", "DATETIME"),
        ("service_status", "probe_retry_attempt", "INTEGER"),
        ("service_status", "last_probe_at", "DATETIME"),
    ]
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _MIGRATIONS:
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            if column not in existing:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                ))
                logger.info("Migration: added %s.%s (%s)", table, column, sql_type)
