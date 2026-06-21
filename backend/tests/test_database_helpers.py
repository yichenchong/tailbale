"""Regression tests for serialized database write helpers."""


import threading

import pytest

from app.database import (
    _DB_WRITE_MUTEX,
    commit_with_lock,
    db_write_section,
    flush_with_lock,
)


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
