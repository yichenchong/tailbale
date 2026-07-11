"""Tests for DNS record cleanup adapter behavior."""

from unittest.mock import patch

import app.adapters.dns_reconciler as dns_mod
from app.adapters.cloudflare_adapter import CloudflareAPIError
from app.adapters.dns_reconciler import CF_CLEANUP_TIMEOUT, cleanup_dns_record
from app.models.dns_record import DnsRecord
from app.models.event import Event
from tests._services_helpers import _create_service_in_db as _create_service


class TestCleanupDnsRecord:
    """cleanup_dns_record should return structured result and only delete local row on success."""

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_success_deletes_both(self, mock_delete, db_session):

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_123", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result["deleted_remote"] is True
        assert result["deleted_local"] is True
        assert result["error"] is None

        # Local row should be gone (flush + expire to see the deletion)
        db_session.flush()
        db_session.expire_all()
        assert db_session.get(DnsRecord, svc.id) is None

    @patch("app.adapters.dns_reconciler.delete_a_record", side_effect=RuntimeError("API error"))
    def test_failure_preserves_local_row(self, mock_delete, db_session):

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_456", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result["deleted_remote"] is False
        assert result["deleted_local"] is False
        assert result["error"] is not None
        assert "API error" in result["error"]

        # Local row should still exist
        row = db_session.get(DnsRecord, svc.id)
        assert row is not None
        assert row.record_id == "cf_456"


    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_not_found_deletes_stale_local_row(self, mock_delete, db_session):

        mock_delete.side_effect = CloudflareAPIError(
            "delete_a_record",
            "Record not found (code 1001)",
            errors=[{"code": 1001, "message": "Record not found"}],
        )
        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_gone", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")

        assert result == {"deleted_remote": False, "deleted_local": True, "error": None}
        db_session.flush()
        db_session.expire_all()
        assert db_session.get(DnsRecord, svc.id) is None

        events = db_session.query(Event).filter(Event.kind == "dns_removed").all()
        assert len(events) == 1

    def test_no_record_returns_noop(self, db_session):

        svc = _create_service(db_session)
        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result["deleted_remote"] is False
        assert result["deleted_local"] is False
        assert result["error"] is None

    def test_no_record_id_returns_noop(self, db_session):

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id=None)
        db_session.add(dns)
        db_session.commit()

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result["deleted_remote"] is False
        assert result["deleted_local"] is False
        assert result["error"] is None

    @patch("app.adapters.dns_reconciler.delete_a_record", side_effect=Exception("timeout"))
    def test_failure_emits_warning_event(self, mock_delete, db_session):

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_789", value="5.6.7.8")
        db_session.add(dns)
        db_session.commit()

        cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        db_session.flush()

        events = db_session.query(Event).filter(Event.kind == "dns_cleanup_failed").all()
        assert len(events) == 1
        assert events[0].level == "warning"
        assert "timeout" in events[0].message.lower()

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_success_emits_info_event(self, mock_delete, db_session):

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_aaa", value="1.1.1.1")
        db_session.add(dns)
        db_session.commit()

        cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        db_session.flush()

        events = db_session.query(Event).filter(Event.kind == "dns_removed").all()
        assert len(events) == 1
        assert events[0].level == "info"

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_success_ownership_guard_keeps_reclaimed_row(self, mock_delete, db_session):
        """If a concurrent reconcile repoints the local row at a NEW Cloudflare id
        between the remote delete and the local delete, the success-path ownership
        guard must NOT drop the freshly-reclaimed row."""

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_old", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        def _reclaim(*_args, **_kwargs):
            row = db_session.get(DnsRecord, svc.id)
            row.record_id = "cf_new"
            row.value = "9.9.9.9"
            db_session.flush()

        mock_delete.side_effect = _reclaim

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result == {"deleted_remote": True, "deleted_local": False, "error": None}

        row = db_session.get(DnsRecord, svc.id)
        assert row is not None
        assert row.record_id == "cf_new"

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_not_found_ownership_guard_keeps_reclaimed_row(self, mock_delete, db_session):
        """Same ownership guard on the already-gone path: a record reclaimed under a
        new id is not dropped when Cloudflare reports the old id absent."""

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_old", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        def _reclaim_then_gone(*_args, **_kwargs):
            row = db_session.get(DnsRecord, svc.id)
            row.record_id = "cf_new"
            db_session.flush()
            raise CloudflareAPIError(
                "delete_a_record",
                "Record does not exist. (code 81044)",
                errors=[{"code": 81044, "message": "Record does not exist."}],
            )

        mock_delete.side_effect = _reclaim_then_gone

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result == {"deleted_remote": False, "deleted_local": False, "error": None}

        row = db_session.get(DnsRecord, svc.id)
        assert row is not None
        assert row.record_id == "cf_new"

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_success_reclaim_emits_no_removal_event(self, mock_delete, db_session):
        """When the local row is reclaimed under a new id between the remote delete
        and the local guard, no 'dns_removed' event may be logged: the service's
        DNS record is live (cf_new), so a removal event would be a false audit entry."""

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_old", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        def _reclaim(*_args, **_kwargs):
            row = db_session.get(DnsRecord, svc.id)
            row.record_id = "cf_new"
            row.value = "9.9.9.9"
            db_session.flush()

        mock_delete.side_effect = _reclaim

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result == {"deleted_remote": True, "deleted_local": False, "error": None}
        db_session.flush()

        events = db_session.query(Event).filter(Event.kind == "dns_removed").all()
        assert events == []

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_not_found_reclaim_emits_no_removal_event(self, mock_delete, db_session):
        """Already-gone path: a row reclaimed under a new id must not produce a
        'dns_removed' event, since nothing was actually removed."""

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_old", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        def _reclaim_then_gone(*_args, **_kwargs):
            row = db_session.get(DnsRecord, svc.id)
            row.record_id = "cf_new"
            db_session.flush()
            raise CloudflareAPIError(
                "delete_a_record",
                "Record does not exist. (code 81044)",
                errors=[{"code": 81044, "message": "Record does not exist."}],
            )

        mock_delete.side_effect = _reclaim_then_gone

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result == {"deleted_remote": False, "deleted_local": False, "error": None}
        db_session.flush()

        events = db_session.query(Event).filter(Event.kind == "dns_removed").all()
        assert events == []

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_default_timeout_is_short_cleanup_cap(self, mock_delete, db_session):
        """cleanup_dns_record runs under the lifecycle mutex, so by default it must
        cap the Cloudflare delete at CF_CLEANUP_TIMEOUT (10s), not the 30s default."""

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_t", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert mock_delete.call_args.kwargs["timeout"] == CF_CLEANUP_TIMEOUT

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_custom_timeout_is_threaded_to_delete(self, mock_delete, db_session):
        """An explicit timeout overrides the default and is threaded into the delete."""

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_t", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        cleanup_dns_record(db_session, svc, "cf-token", "zone123", timeout=3.5)
        assert mock_delete.call_args.kwargs["timeout"] == 3.5

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_db_failure_after_remote_delete_is_not_a_remote_failure(self, mock_delete, db_session):
        """The remote Cloudflare delete SUCCEEDS, then the local DB bookkeeping
        fails. That is NOT a Cloudflare failure: it must report deleted_remote=True
        with error=None (the record IS gone) -- never report a phantom remote
        failure (deleted_remote=False, error=<db msg>) that makes callers raise a
        misleading 'Cloudflare delete failed' 502 and log a false dns_cleanup_failed
        audit event. Guards against re-coupling the remote and local try/except."""

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_db", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        # delete_a_record (mock) succeeds; only the FIRST commit (local bookkeeping)
        # raises, mimicking a transient SQLite write failure after the remote delete.
        real_commit = dns_mod.commit_with_lock
        state = {"n": 0}

        def _flaky_commit(db):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("database is locked")
            return real_commit(db)

        with patch.object(dns_mod, "commit_with_lock", _flaky_commit):
            result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")

        # The remote record is gone -> remote success, no error reported.
        assert result["deleted_remote"] is True
        assert result["error"] is None
        # No FALSE dns_cleanup_failed audit event for a delete that actually worked.
        events = db_session.query(Event).filter(Event.kind == "dns_cleanup_failed").all()
        assert events == []

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_removal_event_message_when_deleted(self, mock_delete, db_session):
        """The dns_removed audit message on the success path names the actual
        removal ('Removed DNS record for <host>'), distinct from the already-absent
        wording -- collapsing the two would blur the operator's audit trail."""
        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_ok", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        db_session.flush()

        events = db_session.query(Event).filter(Event.kind == "dns_removed").all()
        assert len(events) == 1
        assert events[0].message == f"Removed DNS record for {svc.hostname}"

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_removal_event_message_when_already_absent(self, mock_delete, db_session):
        """When Cloudflare reports the record already gone, the dns_removed message
        must use the distinct 'stale local ... already absent' wording (never the
        plain deleted wording), so the audit trail records that nothing was actually
        removed remotely, only the stale local mirror was cleaned up."""
        mock_delete.side_effect = CloudflareAPIError(
            "delete_a_record", "Record does not exist. (code 81044)",
            errors=[{"code": 81044, "message": "Record does not exist."}],
        )
        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_gone", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        db_session.flush()

        events = db_session.query(Event).filter(Event.kind == "dns_removed").all()
        assert len(events) == 1
        assert "already absent" in events[0].message
        assert events[0].message != f"Removed DNS record for {svc.hostname}"
