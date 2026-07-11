"""Pure selectors over a Cloudflare A-record set (AR1).

``cloudflare_adapter.list_a_records`` returns the raw, id-sorted A records for a
hostname; these pure functions encode the record-SELECTION policy that was
previously re-implemented across ``adapters/dns_reconciler.py``, the
``routers/jobs.py`` orphan-cleanup path, and ``health/health_checker.py``:

* :func:`select_owned_or_lowest` — pick OUR record (owned-marker preferred, else
  the deterministic lowest-id fallback), as reconcile and the live-DNS health
  check need.
* :func:`find_by_id` — locate a SPECIFIC record by id, as orphan-cleanup needs
  (it must act on the exact orphaned id, not the lowest-id pick).
* :func:`owned_duplicates` — enumerate the provably-owned duplicate records a
  partially failed create can leave, for pruning.

Transport / CRUD stays in ``cloudflare_adapter``; this leaf performs NO I/O.
Records are the raw Cloudflare dicts (``id`` / ``content`` / ``comment`` / ...);
selection never mutates them.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

Record = dict[str, Any]


def select_owned_or_lowest(records: list[Record], own_comment: str) -> Record | None:
    """Pick OUR record among the hostname's A records.

    Prefers a record provably ours (``comment == own_comment``) over the lowest-id
    fallback. ``records`` is already id-sorted by ``list_a_records``, so the first
    owned record (or the first record overall) is the deterministic lowest-id
    pick. Returns ``None`` when there are no records.
    """
    if not records:
        return None
    for record in records:
        if record.get("comment") == own_comment:
            return record
    return records[0]


def lowest_id(records: list[Record]) -> Record | None:
    """Return the deterministic lowest-id record (the first, since
    ``list_a_records`` returns them id-sorted), or ``None`` if the set is empty.
    The owner-agnostic pick ``find_record`` uses when no ownership marker is in
    play — keeps the record-selection policy out of the transport adapter."""
    return records[0] if records else None


def find_by_id(records: list[Record], record_id: str) -> Record | None:
    """Return the record whose ``id`` equals *record_id* (compared as strings),
    or ``None`` if no record in the set has that id.

    A record with a missing/empty ``id`` is skipped, never matched: without this
    guard a malformed entry's ``str(None) == "None"`` would spuriously match a
    ``None``/``"None"`` target (mirrors the falsy-id skip in
    :func:`owned_duplicates`), so orphan-cleanup never acts on the wrong record.
    """
    target = str(record_id)
    for record in records:
        record_id_value = record.get("id")
        if not record_id_value:
            continue
        if str(record_id_value) == target:
            return record
    return None


def owned_duplicates(
    records: list[Record], *, canonical_id: str, own_comment: str
) -> Iterator[Record]:
    """Yield every record OTHER than *canonical_id* that PROVABLY carries our
    ownership marker (``comment == own_comment``).

    Safety invariant: a record without the exact marker (external/manual, or
    another service's) is NEVER yielded, so a caller pruning duplicates can only
    ever delete records it provably owns. Records missing an ``id`` are skipped.
    """
    canonical = str(canonical_id)
    for record in records:
        record_id = record.get("id")
        if not record_id or str(record_id) == canonical:
            continue
        if record.get("comment") != own_comment:
            continue
        yield record
