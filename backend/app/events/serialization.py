"""Event → JSON wire-shape serialization.

The single event-serialization shape reused by the events endpoints,
``routers/services.get_cert_logs`` (cert-log subset) and ``routers/dashboard``
(recent-errors / recent-events subsets). It lives in the events layer (AR2)
rather than inside the ``/events`` HTTP router so sibling routers no longer
import a private router symbol — the JSON shape is an events-subsystem concern.
"""

from collections.abc import Iterable

from app.models.event import Event
from app.timeutil import iso


def event_to_dict(evt: Event, fields: Iterable[str] | None = None) -> dict:
    """Shape an :class:`Event` row into its JSON wire form.

    Pass *fields* to project a subset (preserving each caller's exact key set);
    ``None`` returns the full record. ``created_at`` uses the shared
    nullable-datetime wire format.
    """
    full = {
        "id": evt.id,
        "service_id": evt.service_id,
        "kind": evt.kind,
        "level": evt.level,
        "message": evt.message,
        "details": evt.details,
        "created_at": iso(evt.created_at),
    }
    if fields is None:
        return full
    return {key: full[key] for key in fields}
