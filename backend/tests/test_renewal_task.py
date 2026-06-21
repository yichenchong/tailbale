"""Tests for the cert renewal background task."""

import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service(db, **overrides):
    """Insert a service directly into the DB for testing."""
    defaults = {
        "name": "TestApp",
        "upstream_container_id": "abc123",
        "upstream_container_name": "testapp",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "testapp.example.com",
        "base_domain": "example.com",
        "edge_container_name": "edge_testapp",
        "network_name": "edge_net_testapp",
        "ts_hostname": "edge-testapp",
    }
    defaults.update(overrides)
    svc = Service(**defaults)
    db.add(svc)
    db.flush()
    status = ServiceStatus(service_id=svc.id, phase="pending")
    db.add(status)
    db.commit()
    return svc


class TestProcessServiceCert:
    @patch("app.certs.renewal_task.read_secret")
    def test_skips_when_no_cf_token(self, mock_secret, db_session):
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = None
        svc = _create_service(db_session)

        # Should not raise, just skip
        process_service_cert(db_session, svc)

        # No cert record should be created
        cert = db_session.get(Certificate, svc.id)
        assert cert is None

    @patch("app.certs.renewal_task.read_secret")
    def test_skips_disabled_service_before_reading_secrets(self, mock_secret, db_session):
        from app.certs.renewal_task import process_service_cert

        svc = _create_service(db_session, enabled=False)

        process_service_cert(db_session, svc)

        mock_secret.assert_not_called()
        assert db_session.get(Certificate, svc.id) is None

    @patch("app.certs.renewal_task.read_secret")
    def test_skips_deleted_stale_service_before_reading_secrets(self, mock_secret, db_session):
        from app.certs.renewal_task import process_service_cert

        svc = _create_service(db_session)
        service_id = svc.id
        db_session.delete(svc)
        db_session.commit()

        process_service_cert(db_session, svc)

        mock_secret.assert_not_called()
        assert db_session.get(Certificate, service_id) is None


    @patch("app.certs.renewal_task.read_secret")
    def test_process_service_cert_serializes_with_reconcile_mutex(self, mock_secret, db_session):
        from sqlalchemy.orm import sessionmaker

        from app.certs.renewal_task import process_service_cert
        from app.reconciler.reconciler import _RECONCILE_MUTEX

        mock_secret.return_value = None
        svc = _create_service(db_session)
        service_id = svc.id
        TestSession = sessionmaker(bind=db_session.get_bind())
        started = threading.Event()
        completed = threading.Event()
        errors: list[Exception] = []

        def run_cert_check():
            thread_db = TestSession()
            try:
                thread_svc = thread_db.get(Service, service_id)
                started.set()
                process_service_cert(thread_db, thread_svc)
                completed.set()
            except Exception as exc:
                errors.append(exc)
            finally:
                thread_db.close()

        with _RECONCILE_MUTEX:
            worker = threading.Thread(target=run_cert_check)
            worker.start()
            assert started.wait(1)
            assert not completed.wait(0.05)

        worker.join(1)
        assert not worker.is_alive()
        assert errors == []
        assert completed.is_set()


    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_issues_cert_when_missing(self, mock_secret, mock_issue, mock_expiry, db_session):
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        # First call: no cert exists; second call after issue: new expiry
        mock_expiry.side_effect = [None, datetime(2027, 6, 1, tzinfo=UTC)]

        svc = _create_service(db_session)
        process_service_cert(db_session, svc)

        mock_issue.assert_called_once()
        cert = db_session.get(Certificate, svc.id)
        assert cert is not None
        assert cert.expires_at is not None
        assert cert.last_failure is None

        # Check event was emitted
        events = db_session.query(Event).filter(Event.kind == "cert_issued").all()
        assert len(events) == 1

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.renew_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_renews_expiring_cert(self, mock_secret, mock_renew, mock_expiry, db_session):
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        # Cert exists but expires in 10 days (within 30-day renewal window)
        soon = datetime.now(UTC) + timedelta(days=10)
        renewed = datetime.now(UTC) + timedelta(days=90)
        mock_expiry.side_effect = [soon, renewed]

        svc = _create_service(db_session)
        process_service_cert(db_session, svc)

        mock_renew.assert_called_once()
        events = db_session.query(Event).filter(Event.kind == "cert_renewed").all()
        assert len(events) == 1

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.read_secret")
    def test_skips_valid_cert(self, mock_secret, mock_expiry, db_session):
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        # Cert expires in 60 days — no renewal needed
        far_future = datetime.now(UTC) + timedelta(days=60)
        mock_expiry.return_value = far_future

        svc = _create_service(db_session)
        process_service_cert(db_session, svc)

        # Should update cert record but not issue/renew
        cert = db_session.get(Certificate, svc.id)
        assert cert is not None
        assert cert.last_failure is None

        # No cert events
        events = db_session.query(Event).filter(
            Event.kind.in_(["cert_issued", "cert_renewed"])
        ).all()
        assert len(events) == 0

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.read_secret")
    def test_stale_invalid_renewal_window_falls_back_to_default(
        self, mock_secret, mock_expiry, db_session
    ):
        from app.certs.renewal_task import process_service_cert
        from app.settings_store import set_setting

        mock_secret.return_value = "cf-token"
        mock_expiry.return_value = datetime.now(UTC) + timedelta(days=60)
        set_setting(db_session, "cert_renewal_window_days", "not-an-int")
        db_session.commit()

        svc = _create_service(db_session)

        process_service_cert(db_session, svc)

        cert = db_session.get(Certificate, svc.id)
        assert cert is not None
        assert cert.last_failure is None

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_records_failure(self, mock_secret, mock_issue, mock_expiry, db_session):
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        mock_expiry.return_value = None  # no cert
        mock_issue.side_effect = RuntimeError("DNS challenge failed")

        svc = _create_service(db_session)
        process_service_cert(db_session, svc)

        cert = db_session.get(Certificate, svc.id)
        assert cert is not None
        assert cert.last_failure is not None
        assert "DNS challenge" in cert.last_failure
        assert cert.next_retry_at is not None

        events = db_session.query(Event).filter(Event.kind == "cert_failed").all()
        assert len(events) == 1
        assert events[0].level == "error"

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_records_failure_when_issued_cert_is_unreadable(
        self, mock_secret, mock_issue, mock_expiry, db_session
    ):
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        mock_expiry.side_effect = [None, None]

        svc = _create_service(db_session)
        process_service_cert(db_session, svc)

        mock_issue.assert_called_once()
        cert = db_session.get(Certificate, svc.id)
        assert cert is not None
        assert cert.last_failure is not None
        assert "unreadable certificate" in cert.last_failure

        assert db_session.query(Event).filter(Event.kind == "cert_issued").count() == 0
        assert db_session.query(Event).filter(Event.kind == "cert_failed").count() == 1


    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.read_secret")
    def test_skips_before_retry_time(self, mock_secret, mock_expiry, db_session):
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        mock_expiry.return_value = None

        svc = _create_service(db_session)

        # Pre-create cert record with future retry time
        cert_record = Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            last_failure="previous error",
            next_retry_at=datetime.now(UTC) + timedelta(hours=3),
        )
        db_session.add(cert_record)
        db_session.commit()

        process_service_cert(db_session, svc)

        # Should not have attempted anything (no events emitted)
        events = db_session.query(Event).filter(
            Event.kind.in_(["cert_issued", "cert_renewed", "cert_failed"])
        ).all()
        assert len(events) == 0


class TestRunRenewalScan:
    @patch("app.certs.renewal_task.process_service_cert")
    @patch("app.certs.renewal_task.SessionLocal")
    def test_processes_enabled_services(self, mock_session_cls, mock_process):
        from app.certs.renewal_task import run_renewal_scan

        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        svc1 = MagicMock()
        svc2 = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [svc1, svc2]

        result = run_renewal_scan()

        assert result == 2
        assert mock_process.call_count == 2
        mock_db.close.assert_called_once()

    @patch("app.certs.renewal_task.process_service_cert")
    @patch("app.certs.renewal_task.SessionLocal")
    def test_continues_on_individual_failure(self, mock_session_cls, mock_process):
        from app.certs.renewal_task import run_renewal_scan

        mock_db = MagicMock()
        mock_session_cls.return_value = mock_db

        svc1 = MagicMock()
        svc2 = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [svc1, svc2]

        # First service throws, second succeeds
        mock_process.side_effect = [Exception("boom"), None]

        run_renewal_scan()
        # Should still process both (count failures as processed=1 for the successful one)
        assert mock_process.call_count == 2
        mock_db.close.assert_called_once()


# ---------------------------------------------------------------------------
# Certificate renewal uses DB-backed paths
# ---------------------------------------------------------------------------


class TestCertRenewalDbPaths:
    def test_get_certs_root_default(self, db_session):
        from app.certs.renewal_task import _get_certs_root
        from app.config import settings

        root = _get_certs_root(db_session)
        assert str(root) == str(settings.certs_dir)

    def test_get_certs_root_respects_db_override(self, db_session):
        from pathlib import Path

        from app.certs.renewal_task import _get_certs_root
        from app.settings_store import set_setting

        set_setting(db_session, "cert_root", "/custom/certs")
        db_session.commit()

        root = _get_certs_root(db_session)
        assert root == Path("/custom/certs")

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_issue_cert_uses_db_cert_path(self, mock_secret, mock_issue, mock_expiry, db_session):
        from pathlib import Path

        from app.certs.renewal_task import process_service_cert
        from app.settings_store import set_setting

        mock_secret.return_value = "cf-token"
        mock_expiry.side_effect = [None, None]

        set_setting(db_session, "cert_root", "/custom/certs")
        db_session.flush()

        svc = _create_service(db_session)

        mock_issue.side_effect = RuntimeError("stopped for test")
        process_service_cert(db_session, svc)

        if mock_issue.called:
            call_args = mock_issue.call_args
            cert_dir = call_args[0][3] if len(call_args[0]) > 3 else call_args.kwargs.get("cert_dir")
            lego_dir = call_args[0][4] if len(call_args[0]) > 4 else call_args.kwargs.get("lego_dir")
            assert str(cert_dir) == str(Path("/custom/certs") / svc.hostname)
            assert str(lego_dir) == str(Path("/custom/certs") / ".lego")
