"""Small datetime helpers."""
from datetime import UTC, datetime, timedelta


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


def days_from_now(days: int) -> datetime | None:
    """Return an aware UTC datetime *days* days from now, or ``None`` on overflow.

    ``event_retention_days`` is only ``ge=1``-bounded at the API (no upper cap);
    ``cert_renewal_window_days`` is ``le=10000``-capped there, but a legacy or
    directly-``set_setting``-ed value bypasses both, so
    ``datetime.now(UTC) +/- timedelta(days=huge)`` can still raise
    ``OverflowError`` past the representable date range. This centralizes the
    guard that was hand-written three times (``services/cert_ops`` far-healthy
    check, ``routers/dashboard`` cert-attention threshold, ``events/retention_task``
    purge cutoff). *days* may be negative for a past cutoff. Each caller decides
    what ``None`` means for it (renew eagerly / clamp to ``datetime.max`` /
    "nothing old enough"), so the saturating policy stays explicit at the call
    site rather than baked into the arithmetic.
    """
    try:
        return datetime.now(UTC) + timedelta(days=days)
    except OverflowError:
        return None


def iso(dt: datetime | None) -> str | None:
    """Serialize *dt* to ISO-8601, or ``None`` when *dt* is ``None``.

    The single home for the nullable ``dt.isoformat() if dt else None`` wire-format
    idiom used by response shapers when a timestamp may be absent (``routers/``
    endpoints, ``services/cert_ops`` and service-status fields). Stored datetimes
    are naive UTC (``NaiveUTCDateTime``), and the wire format is deliberately the
    naive ``.isoformat()`` — the frontend's ``parseBackendDate`` relies on the
    absent tz designator — so this does NOT attach UTC (unlike ``as_utc``, which
    is for in-process comparisons only).
    """
    return dt.isoformat() if dt is not None else None
