"""Small datetime helpers."""
from datetime import UTC, datetime


def as_utc(dt: datetime) -> datetime:
    """Return *dt* as an aware UTC datetime for internal comparisons.

    ``NaiveUTCDateTime`` (``models/types.py``) stores naive UTC and reads back
    naive, so every internal comparison against ``datetime.now(UTC)`` must
    re-attach UTC first. A value that already carries a timezone is returned
    unchanged. This is the single home for the
    ``dt if dt.tzinfo else dt.replace(tzinfo=UTC)`` idiom that was duplicated
    across the cert renewal / reconcile / service-ops expiry math.

    The wire format is deliberately untouched: the API still serializes the
    naive ``.isoformat()`` (the frontend's ``parseBackendDate`` relies on the
    missing tz designator), so this helper is for in-process comparisons only.
    """
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
