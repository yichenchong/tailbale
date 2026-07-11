"""Reusable event-query helpers shared by routers.

Routers should not import each other for event-log filtering or ordering. Keep the
stable event ordering and LIKE escaping here so dashboard, event-list, and
service-action log endpoints use the same query semantics without router-layer
coupling.
"""

from collections.abc import Sequence

from sqlalchemy.orm import Query, Session

from app.models.event import Event

EVENT_ORDER = (Event.created_at.desc(), Event.id.desc())


def escape_like(value: str) -> str:
    """Escape user text before embedding it in a SQL LIKE/ILIKE pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_event_query(
    db: Session,
    *,
    service_id: str | None = None,
    kind: str | None = None,
    kinds: Sequence[str] | None = None,
    level: str | None = None,
    search: str | None = None,
) -> Query:
    """Return the filtered event query before count/order/pagination is applied."""
    query = db.query(Event)
    if service_id:
        query = query.filter(Event.service_id == service_id)
    if kind:
        query = query.filter(Event.kind == kind)
    if kinds is not None:
        query = query.filter(Event.kind.in_(kinds))
    if level:
        query = query.filter(Event.level == level)
    if search is not None:
        normalized_search = search.strip()
        if normalized_search:
            query = query.filter(
                Event.message.ilike(f"%{escape_like(normalized_search)}%", escape="\\")
            )
    return query


def query_events(
    db: Session,
    *,
    service_id: str | None = None,
    kind: str | None = None,
    kinds: Sequence[str] | None = None,
    level: str | None = None,
    search: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_total: bool = True,
) -> tuple[list[Event], int | None]:
    """Filter, stably order, and optionally count/paginate the event log.

    ``search`` is stripped before applying an escaped ILIKE filter so whitespace-only
    input behaves like no search, and leading/trailing spaces do not accidentally
    become part of the match term.
    """
    query = build_event_query(
        db,
        service_id=service_id,
        kind=kind,
        kinds=kinds,
        level=level,
        search=search,
    )
    total = query.count() if include_total else None
    ordered = query.order_by(*EVENT_ORDER)
    if offset:
        ordered = ordered.offset(offset)
    if limit is not None:
        ordered = ordered.limit(limit)
    return ordered.all(), total
