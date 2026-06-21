"""Regression tests for serialized database write helpers."""


import pytest

from app.database import commit_with_lock, db_write_section, flush_with_lock


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
