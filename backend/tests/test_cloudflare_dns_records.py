"""Unit tests for the pure record-selection helpers (cloudflare_dns_records).

These selectors encode the record-SELECTION policy shared by reconcile, the
orphan-cleanup job, and the live-DNS health check. They were previously only
exercised INDIRECTLY (through reconcile_dns / jobs). These direct tests pin the
subtle invariants each selector guarantees so a regression is caught at the
leaf, independent of its callers.
"""

from app.adapters.cloudflare_dns_records import (
    find_by_id,
    lowest_id,
    owned_duplicates,
    select_owned_or_lowest,
)

OWN = "tailbale-managed:svc1"
OTHER = "tailbale-managed:svc2"


class TestSelectOwnedOrLowest:
    def test_empty_returns_none(self):
        assert select_owned_or_lowest([], OWN) is None

    def test_prefers_owned_over_lower_id_unmarked(self):
        # records arrive id-sorted; the lowest-id record is UNMARKED and a
        # higher-id record carries our marker -> the owned one must win, never the
        # lowest-id fallback (the reconcile "pick OUR record" invariant).
        records = [
            {"id": "r1", "content": "9.9.9.9", "comment": None},
            {"id": "r9", "content": "100.64.0.1", "comment": OWN},
        ]
        assert select_owned_or_lowest(records, OWN)["id"] == "r9"

    def test_first_owned_when_multiple_owned(self):
        # Two records carry our marker -> the FIRST (lowest-id, since the input is
        # id-sorted) is the deterministic canonical pick.
        records = [
            {"id": "r1", "content": "100.64.0.1", "comment": OWN},
            {"id": "r2", "content": "100.64.0.1", "comment": OWN},
        ]
        assert select_owned_or_lowest(records, OWN)["id"] == "r1"

    def test_lowest_id_fallback_when_none_owned(self):
        # No record carries OUR marker (one is another service's) -> fall back to
        # the lowest-id record (records[0]), never another owner's record.
        records = [
            {"id": "r1", "content": "9.9.9.9", "comment": None},
            {"id": "r2", "content": "8.8.8.8", "comment": OTHER},
        ]
        assert select_owned_or_lowest(records, OWN)["id"] == "r1"


class TestLowestId:
    def test_empty_returns_none(self):
        assert lowest_id([]) is None

    def test_returns_first_of_id_sorted_set(self):
        # Owner-agnostic: returns records[0] regardless of ownership marker.
        records = [
            {"id": "r1", "comment": OTHER},
            {"id": "r2", "comment": OWN},
        ]
        assert lowest_id(records)["id"] == "r1"


class TestFindById:
    def test_returns_matching_record(self):
        records = [{"id": "r1"}, {"id": "r2"}]
        assert find_by_id(records, "r2")["id"] == "r2"

    def test_not_found_returns_none(self):
        assert find_by_id([{"id": "r1"}], "missing") is None

    def test_matches_across_int_string_id_forms(self):
        # ids are compared AS STRINGS, so a numeric-looking id stored as an int in
        # the record still matches a string query (and vice versa) -- the
        # orphan-cleanup path locates a SPECIFIC id and must not miss it on a type
        # skew between the stored record_id and Cloudflare's returned id.
        assert find_by_id([{"id": 12345}], "12345")["id"] == 12345
        assert find_by_id([{"id": "12345"}], 12345)["id"] == "12345"

    def test_record_missing_id_is_skipped(self):
        # A malformed record with no id must NEVER spuriously match. Before the
        # falsy-id guard, ``str(record.get("id"))`` coerced a missing id to the
        # literal "None", so both a None target (the realistic orphan-cleanup
        # footgun -- a stored record_id that came back empty) and a literal "None"
        # query wrongly matched a missing-id record and returned it.
        assert find_by_id([{"content": "x"}], None) is None
        assert find_by_id([{"content": "x"}], "None") is None
        # An empty-string id is likewise never matched by an empty-string target.
        assert find_by_id([{"id": ""}], "") is None


class TestOwnedDuplicates:
    def test_yields_only_owned_non_canonical(self):
        records = [
            {"id": "r1", "comment": OWN},   # canonical
            {"id": "r2", "comment": OWN},   # owned dup -> yielded
            {"id": "r3", "comment": None},  # unmarked -> never yielded
            {"id": "r4", "comment": OTHER}, # another owner -> never yielded
        ]
        dups = list(owned_duplicates(records, canonical_id="r1", own_comment=OWN))
        assert [r["id"] for r in dups] == ["r2"]

    def test_canonical_excluded_via_string_coercion(self):
        # canonical_id compared as a string, so an int canonical id still excludes
        # the matching record (never deletes the canonical on a type skew).
        records = [
            {"id": 1, "comment": OWN},
            {"id": 2, "comment": OWN},
        ]
        dups = list(owned_duplicates(records, canonical_id="1", own_comment=OWN))
        assert [r["id"] for r in dups] == [2]

    def test_record_missing_id_is_skipped(self):
        # Safety: a record without an id can't be deleted, so it is never yielded.
        records = [
            {"id": "r1", "comment": OWN},
            {"comment": OWN},  # owned but no id -> skipped
        ]
        assert list(owned_duplicates(records, canonical_id="r1", own_comment=OWN)) == []

    def test_never_yields_unmarked_record(self):
        # The core safety invariant: only records PROVABLY ours (exact marker) are
        # yielded, so a duplicate-pruning caller can never delete a record it does
        # not own -- even an empty/None comment must be rejected.
        records = [
            {"id": "r1", "comment": OWN},
            {"id": "r2", "comment": ""},
            {"id": "r3"},  # no comment key at all
        ]
        assert list(owned_duplicates(records, canonical_id="r1", own_comment=OWN)) == []
