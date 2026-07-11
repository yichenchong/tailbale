"""Custom SQLAlchemy column types."""

import json
from datetime import UTC, datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.types import TypeDecorator


class NaiveUTCDateTime(TypeDecorator):
    """A DateTime column that stores every value as naive UTC.

    Model timestamp columns are tz-naive and every comparison assumes naive
    UTC, so a stray tz-aware write would later raise when compared against the
    naive values already stored. This decorator normalizes any aware datetime to
    naive UTC on the way in (``astimezone(UTC).replace(tzinfo=None)``), leaving
    naive datetimes and ``None`` untouched. ``impl`` stays ``DateTime`` so the
    emitted DDL is identical — no migration required.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value


class JSONEncodedDict(TypeDecorator):
    """A Text column that transparently stores its value as JSON.

    The JSON payload columns (``service_status.health_checks``, ``event.details``,
    ``job.details``) historically stored ``json.dumps`` text and were decoded by
    hand at every read site, with three subtly different failure policies. This
    decorator centralizes the codec: ``process_bind_param`` serializes with
    ``json.dumps`` (``None`` stays ``None``) and ``process_result_value`` does a
    guarded ``json.loads`` — ``None``/empty reads back as ``None``, and corrupt
    JSON already in the database returns ``None`` instead of raising, so one bad
    legacy row can never break a listing. ``impl`` stays ``Text`` so the emitted
    DDL is identical — no migration required.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if not value:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
