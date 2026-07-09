"""Regression tests for serialized database write helpers."""

import threading

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy import inspect as _inspect
from sqlalchemy import text as _text

import app.models as _registered_models  # noqa: F401  -- register every table on Base.metadata
from app.database import (
    _DB_WRITE_MUTEX,
    Base,
    _set_sqlite_pragma,
    commit_with_lock,
    db_write_section,
    flush_with_lock,
    run_migrations,
)
from app.models.certificate import Certificate
from app.models.event import Event
from app.models.job import Job
from app.models.service_status import ServiceStatus


class FakeSession:
    def __init__(self, *, commit_exc=None, flush_exc=None):
        self.commit_exc = commit_exc
        self.flush_exc = flush_exc
        self.calls: list[str] = []
        self.no_autoflush_entered = False
        self.no_autoflush_exited = False

    @property
    def no_autoflush(self):
        session = self

        class NoAutoflush:
            def __enter__(self):
                session.calls.append("no_autoflush_enter")
                session.no_autoflush_entered = True

            def __exit__(self, exc_type, exc, tb):
                session.no_autoflush_exited = True
                session.calls.append("no_autoflush_exit")
                return False

        return NoAutoflush()

    def commit(self):
        self.calls.append("commit")
        if self.commit_exc is not None:
            raise self.commit_exc

    def flush(self):
        self.calls.append("flush")
        if self.flush_exc is not None:
            raise self.flush_exc

    def rollback(self):
        self.calls.append("rollback")


def test_db_write_section_uses_no_autoflush_and_rolls_back_on_error():
    db = FakeSession()

    with pytest.raises(RuntimeError, match="boom"), db_write_section(db):
        assert db.no_autoflush_entered is True
        raise RuntimeError("boom")

    assert db.no_autoflush_exited is True
    assert db.calls == ["no_autoflush_enter", "no_autoflush_exit", "rollback"]


def test_commit_with_lock_rolls_back_and_reraises_on_commit_error():
    db = FakeSession(commit_exc=RuntimeError("database is locked"))

    with pytest.raises(RuntimeError, match="database is locked"):
        commit_with_lock(db)

    assert db.calls == ["commit", "rollback"]


def test_flush_with_lock_rolls_back_and_reraises_on_flush_error():
    db = FakeSession(flush_exc=RuntimeError("database is locked"))

    with pytest.raises(RuntimeError, match="database is locked"):
        flush_with_lock(db)

    assert db.calls == ["flush", "rollback"]


def test_db_write_mutex_is_reentrant():
    """The write mutex MUST be reentrant.

    ``commit_with_lock`` / ``flush_with_lock`` are always invoked *inside* a
    ``db_write_section`` (which already holds the mutex). If the mutex were a
    plain ``threading.Lock`` instead of an ``RLock`` every write in the app
    would deadlock on this re-entry.
    """
    assert _DB_WRITE_MUTEX.acquire(blocking=False)
    try:
        # Re-acquire from the same thread: only succeeds for a reentrant lock.
        assert _DB_WRITE_MUTEX.acquire(blocking=False)
        _DB_WRITE_MUTEX.release()
    finally:
        _DB_WRITE_MUTEX.release()


def test_commit_and_flush_nest_inside_db_write_section_without_deadlock():
    """Exercise the canonical app pattern: flush/commit nested in a section.

    Runs in a short-lived worker thread guarded by a timeout so a reentrancy
    regression surfaces as a failed assertion rather than hanging the suite.
    """
    db = FakeSession()
    done = threading.Event()

    def worker():
        with db_write_section(db):
            flush_with_lock(db)
            commit_with_lock(db)
        done.set()

    threading.Thread(target=worker, daemon=True).start()
    assert done.wait(timeout=5), (
        "flush/commit deadlocked inside db_write_section — write mutex not reentrant"
    )
    assert db.calls == ["no_autoflush_enter", "flush", "commit", "no_autoflush_exit"]


def test_db_write_section_serializes_writers_across_threads():
    """Two threads must not be inside a write section simultaneously."""
    db = FakeSession()
    holder_inside = threading.Event()
    release_holder = threading.Event()
    contender_acquired = threading.Event()

    def holder():
        with db_write_section(db):
            holder_inside.set()
            release_holder.wait(timeout=5)

    def contender():
        assert holder_inside.wait(timeout=5)
        # The mutex is held by `holder`; a non-blocking acquire from this
        # separate thread MUST fail, proving writes are serialized.
        if _DB_WRITE_MUTEX.acquire(blocking=False):
            _DB_WRITE_MUTEX.release()
            contender_acquired.set()

    th = threading.Thread(target=holder, daemon=True)
    tc = threading.Thread(target=contender, daemon=True)
    th.start()
    tc.start()
    tc.join(timeout=5)
    release_holder.set()
    th.join(timeout=5)

    assert not contender_acquired.is_set(), (
        "a second thread entered a db_write_section while another held it"
    )


# ---------------------------------------------------------------------------
# run_migrations: idempotent index backfill for Event/Job.service_id
# ---------------------------------------------------------------------------


def _index_names(engine, table):

    return {ix["name"] for ix in _inspect(engine).get_indexes(table)}


def _legacy_engine(tmp_path):
    """Build an on-disk SQLite engine seeded like a pre-index release.

    The full schema is created, then the indexes newer model revisions added
    are dropped so the database resembles one created before ``index=True`` was
    added to the models (Event ``service_id``/``kind``/``created_at``, the Job
    ``service_id``/``status``/``kind``/``created_at`` filters/ordering indexes,
    ``certificate.expires_at`` and ``service_status.phase``).
    """


    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(_text("DROP INDEX IF EXISTS ix_events_service_id"))
        conn.execute(_text("DROP INDEX IF EXISTS ix_events_kind"))
        conn.execute(_text("DROP INDEX IF EXISTS ix_jobs_service_id"))
        conn.execute(_text("DROP INDEX IF EXISTS ix_jobs_status"))
        conn.execute(_text("DROP INDEX IF EXISTS ix_jobs_kind"))
        conn.execute(_text("DROP INDEX IF EXISTS ix_jobs_created_at"))
        conn.execute(_text("DROP INDEX IF EXISTS ix_events_created_at"))
        conn.execute(_text("DROP INDEX IF EXISTS ix_certificates_expires_at"))
        conn.execute(_text("DROP INDEX IF EXISTS ix_service_status_phase"))
    return engine


def test_run_migrations_backfills_service_id_indexes(tmp_path):

    engine = _legacy_engine(tmp_path)
    try:
        # Precondition: the legacy DB lacks the indexes the fix introduces.
        assert "ix_events_service_id" not in _index_names(engine, "events")
        assert "ix_jobs_service_id" not in _index_names(engine, "jobs")

        run_migrations(engine)

        assert "ix_events_service_id" in _index_names(engine, "events")
        assert "ix_jobs_service_id" in _index_names(engine, "jobs")
    finally:
        engine.dispose()


def test_run_migrations_backfills_job_status_kind_created_at_indexes(tmp_path):
    """jobs.py filters on status/kind and orders by created_at; legacy DBs that
    predate ``index=True`` on those columns must have the indexes backfilled."""

    engine = _legacy_engine(tmp_path)
    try:
        before = _index_names(engine, "jobs")
        assert "ix_jobs_status" not in before
        assert "ix_jobs_kind" not in before
        assert "ix_jobs_created_at" not in before

        run_migrations(engine)

        after = _index_names(engine, "jobs")
        assert "ix_jobs_status" in after
        assert "ix_jobs_kind" in after
        assert "ix_jobs_created_at" in after
    finally:
        engine.dispose()


def test_run_migrations_backfills_events_kind_index(tmp_path):
    """events.py filters ``GET /api/events`` on ``Event.kind`` (routers/events.py
    ``query.filter(Event.kind == kind)``); the model declares ``kind`` with
    ``index=True`` so fresh DBs get ``ix_events_kind`` from create_all. A DB
    created before that index was added must have it backfilled too, or the kind
    filter degrades to a full scan of the unbounded-growth events table. This is
    the analogue of ``ix_jobs_kind`` (which IS backfilled)."""

    engine = _legacy_engine(tmp_path)
    try:
        assert "ix_events_kind" not in _index_names(engine, "events")

        run_migrations(engine)

        assert "ix_events_kind" in _index_names(engine, "events")
    finally:
        engine.dispose()


def test_index_migrations_cover_every_core_model_index(tmp_path):
    """Structural guard for the model<->migration pairing on the unbounded-growth
    / dashboard hot-path tables. run_migrations only backfills the indexes it
    enumerates, while create_all indexes every ``index=True`` column — so a
    legacy production DB silently misses any model index absent from the backfill
    list. Dropping every such index and running the migration must restore ALL of
    them, so adding ``index=True`` to a core model without a backfill entry fails
    loudly here (this is exactly how the missing ``ix_events_kind`` slipped in)."""

    engine = _legacy_engine(tmp_path)
    try:
        run_migrations(engine)
        for model in (Event, Job, Certificate, ServiceStatus):
            table = model.__tablename__
            have = _index_names(engine, table)
            want = {ix.name for ix in model.__table__.indexes}
            missing = want - have
            assert not missing, f"{table} missing backfilled indexes: {missing}"
    finally:
        engine.dispose()


def test_run_migrations_backfills_dashboard_hot_path_indexes(tmp_path):
    """dashboard.py orders events by created_at, scans certificates by
    expires_at, and counts service_status by phase; legacy DBs that predate
    ``index=True`` on those columns must have the indexes backfilled."""

    engine = _legacy_engine(tmp_path)
    try:
        assert "ix_events_created_at" not in _index_names(engine, "events")
        assert "ix_certificates_expires_at" not in _index_names(engine, "certificates")
        assert "ix_service_status_phase" not in _index_names(engine, "service_status")

        run_migrations(engine)

        assert "ix_events_created_at" in _index_names(engine, "events")
        assert "ix_certificates_expires_at" in _index_names(engine, "certificates")
        assert "ix_service_status_phase" in _index_names(engine, "service_status")
    finally:
        engine.dispose()


def test_run_migrations_is_idempotent(tmp_path):

    engine = _legacy_engine(tmp_path)
    try:
        # Running repeatedly must never raise (CREATE INDEX IF NOT EXISTS).
        run_migrations(engine)
        run_migrations(engine)
        run_migrations(engine)

        assert "ix_events_service_id" in _index_names(engine, "events")
        assert "ix_jobs_service_id" in _index_names(engine, "jobs")
        job_indexes = _index_names(engine, "jobs")
        assert "ix_jobs_status" in job_indexes
        assert "ix_jobs_kind" in job_indexes
        assert "ix_jobs_created_at" in job_indexes
        assert "ix_events_created_at" in _index_names(engine, "events")
        assert "ix_certificates_expires_at" in _index_names(engine, "certificates")
        assert "ix_service_status_phase" in _index_names(engine, "service_status")
    finally:
        engine.dispose()


def test_run_migrations_twice_is_noop_on_current_model_db(tmp_path):
    """Names in _INDEX_MIGRATIONS must match SQLAlchemy's ``ix_<table>_<column>``
    exactly, so on a DB freshly created from the current models run_migrations is
    a pure no-op: the index set (incl. the AR13 dashboard indexes) is identical
    before and after two runs, with no duplicate CREATE INDEX failures."""


    engine = create_engine(f"sqlite:///{tmp_path / 'current.db'}")
    try:
        Base.metadata.create_all(engine)
        tables = ("events", "jobs", "certificates", "service_status")
        before = {t: _index_names(engine, t) for t in tables}

        run_migrations(engine)
        run_migrations(engine)

        after = {t: _index_names(engine, t) for t in tables}
        assert after == before
        # The AR13 dashboard hot-path indexes are present (from the models).
        assert "ix_events_created_at" in after["events"]
        assert "ix_certificates_expires_at" in after["certificates"]
        assert "ix_service_status_phase" in after["service_status"]
    finally:
        engine.dispose()


def test_run_migrations_no_op_on_empty_database(tmp_path):
    """Tables absent entirely: the has_table guard keeps it crash-free."""


    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        run_migrations(engine)  # must not raise on a schema-less DB
    finally:
        engine.dispose()


def _legacy_engine_missing_columns(tmp_path):
    """Build an on-disk SQLite engine whose ``service_status`` table predates the
    probe-retry columns (``probe_retry_at`` / ``probe_retry_attempt`` /
    ``last_probe_at``) — the original reason ``run_migrations`` exists. The full
    schema is created, then ``service_status`` is rebuilt without those columns.
    """


    engine = create_engine(f"sqlite:///{tmp_path / 'legacy_cols.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(_text("ALTER TABLE service_status RENAME TO _ss_old"))
        conn.execute(_text(
            "CREATE TABLE service_status ("
            "service_id VARCHAR PRIMARY KEY, phase VARCHAR, message VARCHAR, "
            "tailscale_ip VARCHAR, edge_container_id VARCHAR, health_checks TEXT, "
            "last_reconciled_at DATETIME, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(_text("DROP TABLE _ss_old"))
    return engine


def _column_names(engine, table):

    return {c["name"] for c in _inspect(engine).get_columns(table)}


def test_run_migrations_backfills_missing_probe_columns(tmp_path):
    """ADD COLUMN backfill (run_migrations' original job) must add every probe
    column a legacy ``service_status`` table lacks."""

    engine = _legacy_engine_missing_columns(tmp_path)
    try:
        before = _column_names(engine, "service_status")
        assert "probe_retry_at" not in before
        assert "probe_retry_attempt" not in before
        assert "last_probe_at" not in before

        run_migrations(engine)

        after = _column_names(engine, "service_status")
        assert {"probe_retry_at", "probe_retry_attempt", "last_probe_at"} <= after
    finally:
        engine.dispose()


def test_run_migrations_column_backfill_is_idempotent(tmp_path):
    """Re-running after a column backfill must not raise (duplicate-column ALTER)
    nor disturb the already-added columns."""

    engine = _legacy_engine_missing_columns(tmp_path)
    try:
        run_migrations(engine)
        run_migrations(engine)
        run_migrations(engine)

        after = _column_names(engine, "service_status")
        assert {"probe_retry_at", "probe_retry_attempt", "last_probe_at"} <= after
    finally:
        engine.dispose()


def _legacy_engine_missing_token_version(tmp_path):
    """Build an on-disk SQLite engine whose ``users`` table predates the AS3
    ``token_version`` column (JWT session invalidation). The full schema is
    created and seeded with an existing user, then ``token_version`` is dropped
    so the table resembles a pre-AS3 release being upgraded in place.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy_users.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(_text(
            "INSERT INTO users (id, username, password_hash, role, is_active) "
            "VALUES ('usr_legacy0001', 'admin', 'hash', 'admin', 1)"
        ))
        conn.execute(_text("ALTER TABLE users DROP COLUMN token_version"))
    return engine


def test_run_migrations_backfills_users_token_version_column(tmp_path):
    """The AS3 ``token_version`` entry is the one NOT NULL ADD COLUMN in the
    migration list. A legacy ``users`` table that predates it must have the
    column added AND every existing row backfilled with the ``DEFAULT 0`` —
    get_current_user reads ``token_version`` on every authenticated request, so
    a missing column (or a NULL in an existing row) would break auth after an
    in-place upgrade. Removing the ``("users", "token_version", ...)`` migration
    entry fails this test.
    """
    engine = _legacy_engine_missing_token_version(tmp_path)
    try:
        assert "token_version" not in _column_names(engine, "users")

        run_migrations(engine)

        assert "token_version" in _column_names(engine, "users")
        with engine.begin() as conn:
            value = conn.execute(
                _text("SELECT token_version FROM users WHERE id = 'usr_legacy0001'")
            ).scalar_one()
        assert value == 0, "the existing row must be backfilled with the DEFAULT 0"
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# PRAGMA listener: applied to EVERY pooled connection, incl. overflow
# ---------------------------------------------------------------------------


def test_set_sqlite_pragma_applies_to_overflow_connections(tmp_path):
    """The production ``_set_sqlite_pragma`` listener fires on every physical
    connect, so foreign-key enforcement (which makes CASCADE / SET NULL work)
    holds on overflow connections beyond ``pool_size`` too. If it ever regressed
    to e.g. a ``first_connect`` listener, overflow connections would silently
    drop FK enforcement and orphan child rows.
    """


    engine = create_engine(
        f"sqlite:///{tmp_path / 'overflow.db'}",
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=3,
        pool_timeout=5,
    )
    event.listen(engine, "connect", _set_sqlite_pragma)
    try:
        # Hold >pool_size connections open simultaneously to force the pool to
        # mint overflow connections (each triggers the connect listener).
        conns = [engine.connect() for _ in range(4)]
        try:
            for conn in conns:
                fk = conn.exec_driver_sql("PRAGMA foreign_keys").fetchone()[0]
                assert fk == 1, "foreign_keys must be ON on every connection"
                busy = conn.exec_driver_sql("PRAGMA busy_timeout").fetchone()[0]
                assert busy == 5000, "busy_timeout must be set on every connection"
                journal = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()[0]
                assert journal.lower() == "wal", "journal_mode must be WAL on a file DB"
        finally:
            for conn in conns:
                conn.close()
    finally:
        engine.dispose()
