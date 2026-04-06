"""Tests for the cert renewal background task."""

from datetime import datetime, timedelta, timezone
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

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_issues_cert_when_missing(self, mock_secret, mock_issue, mock_expiry, db_session):
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        # First call: no cert exists; second call after issue: new expiry
        mock_expiry.side_effect = [None, datetime(2027, 6, 1, tzinfo=timezone.utc)]

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
        soon = datetime.now(timezone.utc) + timedelta(days=10)
        renewed = datetime.now(timezone.utc) + timedelta(days=90)
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
        far_future = datetime.now(timezone.utc) + timedelta(days=60)
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
            next_retry_at=datetime.now(timezone.utc) + timedelta(hours=3),
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
