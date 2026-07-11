import logging

from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # (table, column, SQL type)
    ("service_status", "probe_retry_at", "DATETIME"),
    ("service_status", "probe_retry_attempt", "INTEGER"),
    ("service_status", "last_probe_at", "DATETIME"),
    ("users", "token_version", "INTEGER NOT NULL DEFAULT 0"),
)

# (table, index name, column) — index names match SQLAlchemy's default
# ``ix_<table>_<column>`` so each entry is a no-op on databases already created
# from the current models. Keep these table names in sync with model
# ``__tablename__`` values; tests drop model-declared indexes and verify this
# list restores them on legacy databases.
_INDEX_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("events", "ix_events_service_id", "service_id"),
    ("events", "ix_events_created_at", "created_at"),
    ("events", "ix_events_kind", "kind"),
    ("jobs", "ix_jobs_service_id", "service_id"),
    ("jobs", "ix_jobs_status", "status"),
    ("jobs", "ix_jobs_kind", "kind"),
    ("jobs", "ix_jobs_created_at", "created_at"),
    ("certificates", "ix_certificates_expires_at", "expires_at"),
    ("service_status", "ix_service_status_phase", "phase"),
)


def run_schema_migrations(target_engine: Engine) -> None:
    insp = inspect(target_engine)
    with target_engine.begin() as conn:
        for table, column, sql_type in _COLUMN_MIGRATIONS:
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
