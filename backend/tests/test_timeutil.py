"""Tests for app.timeutil.as_utc."""
from datetime import UTC, datetime, timedelta, timezone

from app.timeutil import as_utc


def test_naive_datetime_gets_utc_attached():
    naive = datetime(2026, 6, 28, 12, 0, 0)
    result = as_utc(naive)
    assert result.tzinfo is UTC
    assert result == datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC)


def test_aware_utc_datetime_is_unchanged():
    aware = datetime(2026, 6, 28, 12, 0, 0, tzinfo=UTC)
    result = as_utc(aware)
    # Returned unchanged (identity), not re-wrapped.
    assert result is aware


def test_aware_non_utc_datetime_is_left_untouched():
    # as_utc matches the original idiom: an already-aware value is returned
    # as-is (NOT converted), so a non-UTC aware dt keeps its own offset.
    other = timezone(timedelta(hours=5))
    aware = datetime(2026, 6, 28, 12, 0, 0, tzinfo=other)
    result = as_utc(aware)
    assert result is aware
    assert result.utcoffset() == timedelta(hours=5)
