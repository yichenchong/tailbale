"""Tests for the event-log retention task."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

import app.events.retention_task as retention_mod
from app.events.retention_task import purge_old_events
from app.models.event import Event
from app.settings_store import set_setting


def _add_event(db, *, age_days: float, kind: str = "reconcile_completed") -> str:
    """Insert an event aged ``age_days`` days and return its id.

    The id is read right after flush (before any commit expires the instance) so
    callers never touch a possibly-deleted ORM row after the purge.
    """
    evt = Event(
        service_id=None,
        kind=kind,
        level="info",
        message="x",
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db.add(evt)
    db.flush()
    return evt.id


class TestPurgeOldEvents:
    def test_deletes_only_events_older_than_window(self, db_session):
        old_id = _add_event(db_session, age_days=40)
        recent_id = _add_event(db_session, age_days=5)
        db_session.commit()

        deleted = purge_old_events(db_session, retention_days=30)

        assert deleted == 1
        remaining = {e.id for e in db_session.query(Event).all()}
        assert remaining == {recent_id}
        assert old_id not in remaining

    def test_returns_zero_when_nothing_to_purge(self, db_session):
        _add_event(db_session, age_days=1)
        _add_event(db_session, age_days=10)
        db_session.commit()

        assert purge_old_events(db_session, retention_days=30) == 0
        assert db_session.query(Event).count() == 2

    def test_keeps_event_just_inside_window_and_purges_just_outside(self, db_session):
        inside_id = _add_event(db_session, age_days=29)
        outside_id = _add_event(db_session, age_days=31)
        db_session.commit()

        deleted = purge_old_events(db_session, retention_days=30)

        assert deleted == 1
        remaining = {e.id for e in db_session.query(Event).all()}
        assert inside_id in remaining
        assert outside_id not in remaining

    def test_short_window_purges_everything_older(self, db_session):
        _add_event(db_session, age_days=2)
        _add_event(db_session, age_days=3)
        db_session.commit()

        assert purge_old_events(db_session, retention_days=1) == 2
        assert db_session.query(Event).count() == 0

    def test_huge_window_returns_zero_without_raising(self, db_session):
        # Regression (HE1): event_retention_days has no upper bound at write
        # (settings only validate ge=1). A value large enough to push the cutoff
        # past datetime.min makes `now - timedelta(days=N)` raise OverflowError
        # *before* the query. Unguarded, that aborts every sweep, so the
        # retention loop backs off forever and the events table grows unbounded
        # — the exact failure retention prevents. purge must treat it as
        # "nothing is old enough to delete" (return 0), leaving all events.
        _add_event(db_session, age_days=1)
        _add_event(db_session, age_days=10_000)
        db_session.commit()

        assert purge_old_events(db_session, retention_days=4_000_000) == 0
        assert db_session.query(Event).count() == 2


class TestRunRetentionPurge:
    def test_reads_configured_window_from_settings(self, db_session, db_engine, monkeypatch):
        # The runner reads event_retention_days and purges accordingly.
        set_setting(db_session, "event_retention_days", "10")
        _add_event(db_session, age_days=20)
        _add_event(db_session, age_days=3)
        db_session.commit()

        monkeypatch.setattr(retention_mod, "SessionLocal", sessionmaker(bind=db_engine))
        deleted = retention_mod.run_retention_purge()

        assert deleted == 1
        assert db_session.query(Event).count() == 1

    def test_corrupt_setting_raises_and_never_purges_everything(
        self, db_session, db_engine, monkeypatch
    ):
        # Regression: get_positive_int_setting fails loud on a corrupt
        # event_retention_days (< 1 / non-integer). run_retention_purge must let
        # that propagate (so the loop backs off) and crucially must NOT fall
        # through to purge_old_events with a bogus window — a window of 0 would
        # wipe every event. All events stay intact.
        set_setting(db_session, "event_retention_days", "0")  # corrupt: < 1
        _add_event(db_session, age_days=400)
        _add_event(db_session, age_days=1)
        db_session.commit()

        monkeypatch.setattr(retention_mod, "SessionLocal", sessionmaker(bind=db_engine))
        with pytest.raises(ValueError):
            retention_mod.run_retention_purge()

        # No purge-all: every event survives the failed run.
        assert db_session.query(Event).count() == 2


class TestRetentionLoop:
    def test_loop_backs_off_on_corrupt_setting_without_crashing(self, monkeypatch):
        # The background loop must swallow a corrupt-setting ValueError, log it,
        # and back off with the daily interval — never crash the task or
        # tight-loop. Drive exactly one iteration, then break out on the
        # post-iteration sleep via a sentinel.
        import asyncio

        calls = {"purge": 0, "sleeps": []}

        def boom() -> int:
            calls["purge"] += 1
            raise ValueError("Setting 'event_retention_days' has a non-positive value 0")

        monkeypatch.setattr(retention_mod, "run_retention_purge", boom)

        class _Stop(Exception):
            pass

        async def fake_sleep(seconds):
            calls["sleeps"].append(seconds)
            # First call is the 15s startup delay; the second is the back-off
            # after the failed purge — stop the loop there.
            if len(calls["sleeps"]) >= 2:
                raise _Stop

        monkeypatch.setattr(retention_mod.asyncio, "sleep", fake_sleep)

        with pytest.raises(_Stop):
            asyncio.run(retention_mod.retention_loop())

        # The purge was attempted, the ValueError did not crash the loop, and it
        # backed off with the configured daily interval.
        assert calls["purge"] == 1
        assert retention_mod.RETENTION_INTERVAL_SECONDS in calls["sleeps"]
