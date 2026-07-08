"""Tests for app.timeutil (``as_utc``, ``days_from_now``, ``iso``)."""
from datetime import UTC, datetime, timedelta, timezone

from app.timeutil import as_utc, days_from_now, iso


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


def test_days_from_now_positive_is_aware_utc():
    result = days_from_now(30)
    assert result is not None
    assert result.tzinfo is UTC
    expected = datetime.now(UTC) + timedelta(days=30)
    assert abs((result - expected).total_seconds()) < 5


def test_days_from_now_negative_is_in_the_past():
    result = days_from_now(-30)
    assert result is not None
    assert result.tzinfo is UTC
    assert result < datetime.now(UTC)


def test_days_from_now_returns_none_on_timedelta_construction_overflow():
    # days beyond timedelta's ~999,999,999-day ceiling raises OverflowError while
    # BUILDING the delta; the guard turns that into None, not a crash.
    assert days_from_now(10**18) is None


def test_days_from_now_returns_none_on_addition_overflow():
    # A delta that fits in timedelta but pushes the result past datetime.max
    # (~year 9999) overflows on the ADDITION; that path must also yield None.
    assert days_from_now(3_000_000) is None
    # ... and symmetrically past datetime.min for a huge past cutoff.
    assert days_from_now(-3_000_000) is None


def test_iso_none_returns_none():
    assert iso(None) is None


def test_iso_naive_datetime_has_no_tz_designator():
    # Wire format is deliberately the naive isoformat (no offset) — the frontend's
    # parseBackendDate relies on the missing tz designator. iso must NOT attach one.
    assert iso(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"


def test_iso_passes_through_whatever_it_is_given():
    # iso serializes verbatim; it does not strip a tz an aware value carries.
    assert iso(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)) == "2026-01-02T03:04:05+00:00"
