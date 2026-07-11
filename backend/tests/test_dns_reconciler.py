"""Tests for DNS reconciliation adapter convergence (reconcile_dns)."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.cloudflare_adapter import CloudflareAPIError, ownership_comment
from app.adapters.dns_reconciler import CF_CLEANUP_TIMEOUT, reconcile_dns
from app.models.dns_record import DnsRecord
from app.models.event import Event
from tests._services_helpers import _create_service_in_db as _create_service


class TestReconcileDns:
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_creates_record_when_absent(self, mock_list, mock_create, db_session):

        mock_list.return_value = []
        mock_create.return_value = {"id": "new_r1"}

        svc = _create_service(db_session)
        result = reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert result.record_id == "new_r1"
        assert result.value == "100.64.0.1"
        # The created record is stamped with our ownership marker.
        mock_create.assert_called_once_with(
            "cf-token", "zone1", "testapp.example.com", "100.64.0.1",
            timeout=10.0, comment=ownership_comment(svc.id),
        )

        events = db_session.query(Event).filter(Event.kind == "dns_created").all()
        assert len(events) == 1

    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_created_event_level_and_message(self, mock_list, mock_create, db_session):
        """Guard against a level/message argument-order swap when emitting events:
        the severity must land in `level` and the human string in `message`."""

        mock_list.return_value = []
        mock_create.return_value = {"id": "new_r1"}

        svc = _create_service(db_session)
        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        events = db_session.query(Event).filter(Event.kind == "dns_created").all()
        assert len(events) == 1
        event = events[0]
        assert event.level == "info"
        assert event.message == "Created DNS A record testapp.example.com -> 100.64.0.1"
        assert event.details["record_id"] == "new_r1"

    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_create_without_record_id_does_not_persist_stale_local_row(
        self, mock_list, mock_create, db_session
    ):

        mock_list.return_value = []
        mock_create.return_value = {"content": "100.64.0.1"}

        svc = _create_service(db_session)

        with pytest.raises(RuntimeError, match="did not return a DNS record id"):
            reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert db_session.get(DnsRecord, svc.id) is None

    @patch("app.adapters.dns_reconciler.list_a_records")
    @patch("app.adapters.cloudflare_adapter.httpx2.post")
    def test_create_with_null_cf_result_raises_clean_error(
        self, mock_post, mock_list, db_session
    ):
        """End-to-end: a Cloudflare create returning ``"result": null`` must yield a
        clear 'did not return a DNS record id' RuntimeError (through the REAL
        create_a_record) rather than a cryptic AttributeError, and must leave no
        stale local row behind."""

        mock_list.return_value = []
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "result": None, "errors": []}
        mock_post.return_value = resp

        svc = _create_service(db_session)
        with pytest.raises(RuntimeError, match="did not return a DNS record id"):
            reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert db_session.get(DnsRecord, svc.id) is None

    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_existing_record_without_id_raises_clean_error(
        self, mock_list, mock_create, mock_update, db_session
    ):
        """The selected EXISTING record carrying no id (a malformed Cloudflare list
        entry) must raise the clean 'find ... did not return a DNS record id' error
        via _require_record_id's find path -- never silently adopt/update it nor
        persist a phantom local row with record_id=None."""

        # Correct IP + unmarked: would otherwise take the adopt branch, but the
        # missing id must short-circuit with a clean error before any CF write.
        mock_list.return_value = [{"content": "100.64.0.1", "comment": None}]

        svc = _create_service(db_session)
        with pytest.raises(RuntimeError, match=r"find .* did not return a DNS record id"):
            reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        mock_create.assert_not_called()
        mock_update.assert_not_called()
        assert db_session.get(DnsRecord, svc.id) is None

    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_updates_record_when_ip_changed(self, mock_list, mock_update, db_session):

        mock_list.return_value = [{"id": "r1", "content": "100.64.0.1"}]
        mock_update.return_value = {"id": "r1", "content": "100.64.0.2"}

        svc = _create_service(db_session)
        result = reconcile_dns(db_session, svc, "100.64.0.2", "cf-token", "zone1")

        assert result.value == "100.64.0.2"
        # The update re-stamps our ownership marker.
        mock_update.assert_called_once_with(
            "cf-token", "zone1", "r1", "100.64.0.2",
            timeout=10.0, comment=ownership_comment(svc.id),
        )

        events = db_session.query(Event).filter(Event.kind == "dns_updated").all()
        assert len(events) == 1
        assert "100.64.0.1" in events[0].message  # old IP mentioned
        assert "100.64.0.2" in events[0].message  # new IP mentioned

    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_noop_when_ip_matches_and_already_marked(self, mock_list, mock_update, db_session):

        svc = _create_service(db_session)
        # Record already correct AND already carries our marker -> pure no-op.
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": ownership_comment(svc.id)}
        ]

        result = reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert result.record_id == "r1"
        assert result.value == "100.64.0.1"
        mock_update.assert_not_called()  # nothing to write on CF

        # No create/update events
        events = db_session.query(Event).filter(
            Event.kind.in_(["dns_created", "dns_updated"])
        ).all()
        assert len(events) == 0

    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_creates_dns_record_entry(self, mock_list, mock_create, db_session):

        mock_list.return_value = []
        mock_create.return_value = {"id": "r1"}

        svc = _create_service(db_session)
        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        dns = db_session.get(DnsRecord, svc.id)
        assert dns is not None
        assert dns.hostname == "testapp.example.com"
        assert dns.record_type == "A"

    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_updates_existing_dns_record_entry(self, mock_list, mock_update, db_session):

        svc = _create_service(db_session)

        # Pre-create a dns_record
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="old_r", value="100.64.0.9")
        db_session.add(dns)
        db_session.commit()

        # CF record already correct + marked -> local row value still reconciled.
        mock_list.return_value = [
            {"id": "old_r", "content": "100.64.0.1", "comment": ownership_comment(svc.id)}
        ]

        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        dns = db_session.get(DnsRecord, svc.id)
        assert dns.value == "100.64.0.1"
        mock_update.assert_not_called()

    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_resyncs_stale_hostname_on_existing_row(self, mock_list, mock_update, db_session):
        """reconcile_dns mirrors the service's DNS state into the local row. A row
        whose hostname drifted from service.hostname (e.g. one that survived a
        CF-unconfigured hostname change) must be re-synced to the CURRENT hostname,
        never left stale while record_id/value already point at the new hostname's
        Cloudflare record -- otherwise the local mirror lies about which name the
        record serves."""

        svc = _create_service(db_session)  # hostname testapp.example.com
        # Pre-existing row still carrying a STALE hostname from a prior name.
        dns = DnsRecord(
            service_id=svc.id, hostname="old.example.com", record_id="r1", value="100.64.0.1"
        )
        db_session.add(dns)
        db_session.commit()

        # CF already holds the correct, owned record for the CURRENT hostname -> no
        # remote change; only the local mirror needs to converge.
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": ownership_comment(svc.id)}
        ]
        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        row = db_session.get(DnsRecord, svc.id)
        assert row.hostname == "testapp.example.com"  # resynced to service.hostname
        assert row.record_id == "r1"
        mock_update.assert_not_called()  # no remote write was needed

    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_reconcile_is_idempotent(self, mock_list, mock_create, db_session):
        """Re-running reconcile_dns converges: no duplicate row, no second remote
        create, and no spurious events on the second pass."""

        # First pass: record absent -> created (stamped with our marker).
        mock_list.return_value = []
        mock_create.return_value = {"id": "r1"}
        svc = _create_service(db_session)
        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        # Second pass: CF now reports the record present, matching, and OURS -> no-op.
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": ownership_comment(svc.id)}
        ]
        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        rows = db_session.query(DnsRecord).filter(DnsRecord.service_id == svc.id).all()
        assert len(rows) == 1
        assert rows[0].record_id == "r1"
        assert rows[0].value == "100.64.0.1"
        mock_create.assert_called_once()  # never re-created on the convergent pass

        created = db_session.query(Event).filter(Event.kind == "dns_created").all()
        updated = db_session.query(Event).filter(Event.kind == "dns_updated").all()
        assert len(created) == 1
        assert len(updated) == 0

    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_adopts_current_cf_record_when_local_id_is_stale(
        self, mock_list, mock_update, mock_create, db_session
    ):
        """When the local row holds a stale record_id but CF already has a matching
        record under a different id (already ours), reconcile adopts the live id
        without creating or updating a remote record (convergence, no orphan)."""

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="stale", value="100.64.0.1")
        db_session.add(dns)
        db_session.commit()

        mock_list.return_value = [
            {"id": "live", "content": "100.64.0.1", "comment": ownership_comment(svc.id)}
        ]
        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        row = db_session.get(DnsRecord, svc.id)
        assert row.record_id == "live"
        mock_create.assert_not_called()
        mock_update.assert_not_called()

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_adopts_and_stamps_unmarked_matching_record(
        self, mock_list, mock_create, mock_update, mock_delete, db_session
    ):
        """A pre-existing/external record with the correct IP but NO ownership marker
        is adopted via an update that stamps our marker (so it is provably ours next
        time). No create, no duplicate deletes."""

        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        mock_list.return_value = [{"id": "ext", "content": "100.64.0.1", "comment": None}]
        mock_update.return_value = {"id": "ext", "content": "100.64.0.1"}

        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        mock_create.assert_not_called()
        mock_update.assert_called_once_with(
            "cf-token", "zone1", "ext", "100.64.0.1", timeout=10.0, comment=own
        )
        mock_delete.assert_not_called()

        row = db_session.get(DnsRecord, svc.id)
        assert row.record_id == "ext"
        events = db_session.query(Event).filter(Event.kind == "dns_updated").all()
        assert len(events) == 1
        # Parity with the dns_created / dns_duplicate_removed guards: the adopt
        # branch's audit event must land the severity in `level` and the human
        # string in `message` (regression guard for an arg-order swap).
        assert events[0].level == "info"
        assert events[0].message == "Adopted DNS A record testapp.example.com and stamped ownership marker"
        assert events[0].details["record_id"] == "ext"

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_prefers_marked_record_over_lowest_id(
        self, mock_list, mock_create, mock_update, mock_delete, db_session
    ):
        """With several A records for one hostname, reconcile picks the one PROVABLY
        ours (our marker) even when an UNMARKED record has a lower id. The unmarked
        record is never adopted, never updated, and never deleted (not ours)."""

        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        # Lowest id (r1) is UNMARKED; the marked record (r9) has a higher id.
        mock_list.return_value = [
            {"id": "r1", "content": "9.9.9.9", "comment": None},
            {"id": "r9", "content": "100.64.0.1", "comment": own},
        ]

        result = reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        # Picks the MARKED record, not the lowest-id unmarked one.
        assert result.record_id == "r9"
        assert result.value == "100.64.0.1"
        mock_create.assert_not_called()
        mock_update.assert_not_called()
        # r1 lacks our marker -> never deleted, even with a divergent IP.
        mock_delete.assert_not_called()

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_removes_owned_duplicates_and_never_deletes_unmarked(
        self, mock_list, mock_create, mock_update, mock_delete, db_session
    ):
        """2+ records carrying OUR marker -> the non-canonical owned duplicate(s) are
        deleted; a sibling record WITHOUT our marker is NEVER deleted. Emits a
        dns_duplicate_removed audit event naming exactly what was removed."""

        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": own},   # canonical (lowest-id, ours)
            {"id": "r2", "content": "100.64.0.1", "comment": own},   # owned duplicate -> delete
            {"id": "r3", "content": "100.64.0.1", "comment": None},  # NOT ours -> never delete
        ]

        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        row = db_session.get(DnsRecord, svc.id)
        assert row.record_id == "r1"
        # Canonical already correct + marked -> no create/update.
        mock_create.assert_not_called()
        mock_update.assert_not_called()
        # Only the owned duplicate r2 is deleted; the unmarked r3 is never touched.
        mock_delete.assert_called_once_with("cf-token", "zone1", "r2", timeout=10.0)
        deleted_ids = [c.args[2] for c in mock_delete.call_args_list]
        assert "r3" not in deleted_ids

        events = db_session.query(Event).filter(Event.kind == "dns_duplicate_removed").all()
        assert len(events) == 1
        assert events[0].details["removed_record_ids"] == ["r2"]
        assert events[0].details["canonical_record_id"] == "r1"
        # Guard the dns_duplicate_removed event's severity/wording against a
        # level/message argument-order swap (it is the only warning-level event
        # reconcile emits): the count lands in the human message, level="warning".
        assert events[0].level == "warning"
        assert events[0].message == "Removed 1 duplicate DNS A record(s) for testapp.example.com"

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_duplicate_delete_cf_error_does_not_abort_reconcile(
        self, mock_list, mock_create, mock_update, mock_delete, db_session
    ):
        """A non-not-found Cloudflare error while deleting an owned duplicate is
        best-effort: it must be swallowed (logged) and must NOT abort the reconcile.
        The failed id is NOT reported removed, so with nothing removed no
        dns_duplicate_removed audit event is emitted. Guards the per-delete
        try/except in _remove_owned_duplicates against a regression that would let a
        transient CF blip on a stray duplicate fail the entire reconcile."""

        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": own},  # canonical -> noop
            {"id": "r2", "content": "100.64.0.1", "comment": own},  # owned dup -> delete fails
        ]
        mock_delete.side_effect = CloudflareAPIError(
            "delete_a_record", "Internal error (code 1002)",
            errors=[{"code": 1002, "message": "Internal error"}],
        )

        # Must NOT raise even though the duplicate delete failed.
        result = reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert result.record_id == "r1"
        mock_create.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_called_once_with("cf-token", "zone1", "r2", timeout=10.0)

        events = db_session.query(Event).filter(Event.kind == "dns_duplicate_removed").all()
        assert events == []  # failed delete -> nothing reported removed -> no event

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_duplicate_already_gone_counts_as_removed(
        self, mock_list, mock_create, mock_update, mock_delete, db_session
    ):
        """When the owned duplicate is concurrently removed, Cloudflare reports it
        already gone (81044). That is the DESIRED end state, so the id still counts
        as removed and the audit event names it. Guards the is_not_found branch of
        the dedup loop."""

        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": own},
            {"id": "r2", "content": "100.64.0.1", "comment": own},
        ]
        mock_delete.side_effect = CloudflareAPIError(
            "delete_a_record", "Record does not exist. (code 81044)",
            errors=[{"code": 81044, "message": "Record does not exist."}],
        )

        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        events = db_session.query(Event).filter(Event.kind == "dns_duplicate_removed").all()
        assert len(events) == 1
        assert events[0].details["removed_record_ids"] == ["r2"]
        assert events[0].details["canonical_record_id"] == "r1"

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_duplicate_partial_failure_reports_only_removed(
        self, mock_list, mock_create, mock_update, mock_delete, db_session
    ):
        """With two owned duplicates where one delete succeeds and one fails, the
        loop attempts BOTH (a failure on one never short-circuits the rest) and
        reports only the successfully-removed id in the audit event."""

        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": own},  # canonical -> noop
            {"id": "r2", "content": "100.64.0.1", "comment": own},  # dup -> deletes ok
            {"id": "r3", "content": "100.64.0.1", "comment": own},  # dup -> delete fails
        ]

        def _delete(_token, _zone, rec_id, timeout=None):
            if rec_id == "r3":
                raise CloudflareAPIError(
                    "delete_a_record", "Internal error (code 1002)",
                    errors=[{"code": 1002, "message": "Internal error"}],
                )

        mock_delete.side_effect = _delete

        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        attempted = {c.args[2] for c in mock_delete.call_args_list}
        assert attempted == {"r2", "r3"}  # both attempted despite r3 failing
        events = db_session.query(Event).filter(Event.kind == "dns_duplicate_removed").all()
        assert len(events) == 1
        assert events[0].details["removed_record_ids"] == ["r2"]
        assert events[0].details["canonical_record_id"] == "r1"

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_passes_short_timeout_to_cloudflare_calls(
        self, mock_list, mock_create, mock_update, mock_delete, db_session
    ):
        """reconcile_dns runs under the per-service reconcile lock, so every
        Cloudflare call is capped at CF_CLEANUP_TIMEOUT (10s) instead of
        the 30s default, bounding the worst-case lock hold (list+create/update)."""

        assert CF_CLEANUP_TIMEOUT == 10.0

        # Create path: record absent -> list + create, both must be capped.
        mock_list.return_value = []
        mock_create.return_value = {"id": "r1"}
        svc = _create_service(db_session)
        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert mock_list.call_args.kwargs["timeout"] == CF_CLEANUP_TIMEOUT
        assert mock_create.call_args.kwargs["timeout"] == CF_CLEANUP_TIMEOUT

        # Update path: record present but drifted -> list + update, both capped.
        mock_list.return_value = [{"id": "r1", "content": "100.64.0.9"}]
        reconcile_dns(db_session, svc, "100.64.0.2", "cf-token", "zone1")

        assert mock_list.call_args.kwargs["timeout"] == CF_CLEANUP_TIMEOUT
        assert mock_update.call_args.kwargs["timeout"] == CF_CLEANUP_TIMEOUT

    @patch("app.adapters.dns_reconciler.list_a_records")
    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_warns_on_unmanaged_sibling_records(self, mock_delete, mock_list, db_session, caplog):
        """A sibling A record for the hostname that does NOT carry our ownership
        marker is left untouched by design, but it makes DNS resolve to multiple
        (likely wrong) IPs -- so reconcile must surface an operator warning (the
        reconcile-path equivalent of find_record's >1-record warning). The unmarked
        record is NEVER deleted."""


        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": own},   # canonical, ours
            {"id": "r2", "content": "9.9.9.9", "comment": None},     # foreign/unmarked
        ]

        with caplog.at_level(logging.WARNING, logger="app.adapters.dns_reconciler"):
            reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not managed by tailBale" in m and "testapp.example.com" in m for m in msgs)
        # The unmarked sibling is never deleted (not provably ours).
        mock_delete.assert_not_called()

    @patch("app.adapters.dns_reconciler.list_a_records")
    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    def test_no_conflict_warning_when_all_records_owned(
        self, mock_create, mock_delete, mock_list, db_session, caplog
    ):
        """When every other A record provably carries OUR marker (an owned duplicate
        that gets removed), there is no unmanaged sibling, so the conflict warning
        must NOT fire -- only the duplicate-removal path runs."""


        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.1", "comment": own},  # canonical, ours
            {"id": "r2", "content": "100.64.0.1", "comment": own},  # owned dup -> removed
        ]

        with caplog.at_level(logging.WARNING, logger="app.adapters.dns_reconciler"):
            reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        mock_create.assert_not_called()
        msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("not managed by tailBale" in m for m in msgs)

    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_hard_list_failure_propagates_and_persists_no_row(
        self, mock_list, mock_create, db_session
    ):
        """Module contract: reconcile_dns lets a hard Cloudflare API failure bubble
        out (never swallowed) and, because the LIST fails before any local write,
        persists NO phantom DnsRecord row. A create must NOT be attempted after a
        failed list."""

        mock_list.side_effect = CloudflareAPIError(
            "find_record", "Invalid token (code 10000)",
            errors=[{"code": 10000, "message": "Invalid token"}],
        )
        svc = _create_service(db_session)

        with pytest.raises(CloudflareAPIError, match="Invalid token"):
            reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        mock_create.assert_not_called()
        assert db_session.get(DnsRecord, svc.id) is None

    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_hard_create_failure_propagates_and_persists_no_row(
        self, mock_list, mock_create, db_session
    ):
        """A hard Cloudflare failure on the CREATE path bubbles out and leaves no
        stale local row behind (the db write section is never reached)."""

        mock_list.return_value = []
        mock_create.side_effect = CloudflareAPIError(
            "create_a_record", "Record already exists (code 81057)",
            errors=[{"code": 81057, "message": "Record already exists"}],
        )
        svc = _create_service(db_session)

        with pytest.raises(CloudflareAPIError, match="Record already exists"):
            reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert db_session.get(DnsRecord, svc.id) is None

    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_hard_update_failure_propagates_and_persists_no_row(
        self, mock_list, mock_create, mock_update, db_session
    ):
        """Completes reconcile's hard-failure contract for the UPDATE path (the
        list/create cases are already covered): a Cloudflare error while updating a
        drifted owned record bubbles out (never swallowed) and, because it fails
        before the db write section, persists NO phantom local row. No create is
        attempted on the update path."""
        svc = _create_service(db_session)
        # Owned record present with a WRONG ip -> update path; the update fails hard.
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.9", "comment": ownership_comment(svc.id)}
        ]
        mock_update.side_effect = CloudflareAPIError(
            "update_a_record", "Internal error (code 1002)",
            errors=[{"code": 1002, "message": "Internal error"}],
        )

        with pytest.raises(CloudflareAPIError, match="Internal error"):
            reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        mock_create.assert_not_called()
        assert db_session.get(DnsRecord, svc.id) is None

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.list_a_records")
    def test_updates_canonical_and_removes_owned_duplicate_in_one_pass(
        self, mock_list, mock_create, mock_update, mock_delete, db_session
    ):
        """Single-pass convergence: the canonical (lowest-id) OWNED record carries a
        WRONG ip while a higher-id OWNED duplicate happens to hold the right ip.
        reconcile updates the canonical to the correct ip (never adopts the dup as
        canonical -- the lowest-id owned record is authoritative) AND removes the
        owned duplicate, emitting BOTH a dns_updated and a dns_duplicate_removed
        event. Guards that update and dedup compose correctly in one reconcile."""

        svc = _create_service(db_session)
        own = ownership_comment(svc.id)
        mock_list.return_value = [
            {"id": "r1", "content": "100.64.0.9", "comment": own},  # canonical, WRONG ip
            {"id": "r2", "content": "100.64.0.1", "comment": own},  # owned dup, right ip
        ]
        mock_update.return_value = {"id": "r1", "content": "100.64.0.1"}

        result = reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert result.record_id == "r1"
        assert result.value == "100.64.0.1"
        mock_create.assert_not_called()
        # Canonical updated to the correct ip (re-stamped with our marker).
        mock_update.assert_called_once_with(
            "cf-token", "zone1", "r1", "100.64.0.1", timeout=10.0, comment=own
        )
        # The owned duplicate is removed (never the canonical).
        mock_delete.assert_called_once_with("cf-token", "zone1", "r2", timeout=10.0)

        updated = db_session.query(Event).filter(Event.kind == "dns_updated").all()
        dups = db_session.query(Event).filter(Event.kind == "dns_duplicate_removed").all()
        assert len(updated) == 1
        assert "100.64.0.9" in updated[0].message and "100.64.0.1" in updated[0].message
        assert len(dups) == 1
        assert dups[0].details["removed_record_ids"] == ["r2"]
        assert dups[0].details["canonical_record_id"] == "r1"
