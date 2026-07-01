"""Tests for the cert renewal background task."""

import os
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

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


def _write_real_pair(cert_dir, *, matching=True, not_after=None):
    """Publish a real cert + privkey under *cert_dir* in the generation layout.

    Mirrors what _atomic_copy_certs leaves on disk - a ``gen-*`` directory
    holding the pair plus a relative ``current`` symlink - so process_service_cert
    runs its actual get_cert_expiry / cert_key_pair_matches checks against the
    same ``current/`` path it reads in production. Mismatch the key if asked.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_dir.mkdir(parents=True, exist_ok=True)
    gen_dir = cert_dir / "gen-test"
    gen_dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert_key = key if matching else rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )
    if not_after is None:
        not_after = datetime.now(UTC) + timedelta(days=300)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "testapp.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(cert_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(not_after)
        .sign(cert_key, hashes.SHA256())
    )
    (gen_dir / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (gen_dir / "privkey.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    current = cert_dir / "current"
    if current.is_symlink() or current.exists():
        current.unlink()
    os.symlink("gen-test", current)

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
        from app.locks import reconcile_lock_for

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

        with reconcile_lock_for(service_id):
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
        # renew_cert returns (cert_dir, fresh_issued); a real in-place renewal
        # reports fresh_issued=False, so the caller emits cert_renewed.
        mock_renew.return_value = (MagicMock(), False)

        svc = _create_service(db_session)
        process_service_cert(db_session, svc)

        mock_renew.assert_called_once()
        events = db_session.query(Event).filter(Event.kind == "cert_renewed").all()
        assert len(events) == 1
        assert (
            db_session.query(Event).filter(Event.kind == "cert_issued").count() == 0
        )

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.renew_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_renew_fallback_emits_cert_issued(
        self, mock_secret, mock_renew, mock_expiry, db_session
    ):
        """When renew_cert internally falls back to a fresh issue it returns
        fresh_issued=True, and the caller must label the event cert_issued (not
        cert_renewed) so the event log truthfully reflects what happened."""
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        # Cert exists but expires within the renewal window -> needs_renew path.
        soon = datetime.now(UTC) + timedelta(days=10)
        fresh = datetime.now(UTC) + timedelta(days=90)
        mock_expiry.side_effect = [soon, fresh]
        # renew_cert fell back to a fresh issue.
        mock_renew.return_value = (MagicMock(), True)

        svc = _create_service(db_session)
        process_service_cert(db_session, svc)

        mock_renew.assert_called_once()
        assert (
            db_session.query(Event).filter(Event.kind == "cert_issued").count() == 1
        )
        assert (
            db_session.query(Event).filter(Event.kind == "cert_renewed").count() == 0
        )

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
    def test_stale_invalid_renewal_window_fails_loud(
        self, mock_secret, mock_expiry, db_session
    ):
        """A corrupt cert_renewal_window_days now fails loud (raises ValueError)
        instead of silently falling back: writes enforce ge=1, so a stored
        non-integer is corruption that must surface rather than be masked."""
        from app.certs.renewal_task import process_service_cert
        from app.settings_store import set_setting

        mock_secret.return_value = "cf-token"
        mock_expiry.return_value = datetime.now(UTC) + timedelta(days=60)
        set_setting(db_session, "cert_renewal_window_days", "not-an-int")
        db_session.commit()

        svc = _create_service(db_session)

        with pytest.raises(ValueError):
            process_service_cert(db_session, svc)

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

    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_reissues_when_cert_key_pair_mismatches(self, mock_secret, mock_issue, db_session):
        """An unexpired cert whose private key does not match (on-disk corruption
        or external tampering producing a mismatched current/ pair) must be
        force-reissued, not left serving a broken pair to Caddy."""
        from app.certs.renewal_task import process_service_cert
        from app.settings_store import set_setting

        mock_secret.return_value = "cf-token"
        certs_root = self._tmp_certs_root(db_session, set_setting)

        svc = _create_service(db_session)
        # Valid (far-future) cert on disk, but privkey.pem holds an unrelated key.
        _write_real_pair(certs_root / svc.hostname, matching=False)

        process_service_cert(db_session, svc)

        # The mismatch forced a fresh issue rather than the healthy skip path.
        mock_issue.assert_called_once()
        assert (
            db_session.query(Event).filter(Event.kind == "cert_issued").count() == 1
        )

    @patch("app.certs.renewal_task.renew_cert")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_skips_when_pair_matches(self, mock_secret, mock_issue, mock_renew, db_session):
        """False-positive guard: a healthy matching pair far from expiry must take
        the skip path and never trigger an ACME issue/renew."""
        from app.certs.renewal_task import process_service_cert
        from app.settings_store import set_setting

        mock_secret.return_value = "cf-token"
        certs_root = self._tmp_certs_root(db_session, set_setting)

        svc = _create_service(db_session)
        _write_real_pair(certs_root / svc.hostname, matching=True)

        process_service_cert(db_session, svc)

        mock_issue.assert_not_called()
        mock_renew.assert_not_called()
        cert = db_session.get(Certificate, svc.id)
        assert cert is not None
        assert cert.expires_at is not None
        assert cert.last_failure is None
        assert (
            db_session.query(Event)
            .filter(Event.kind.in_(["cert_issued", "cert_renewed"]))
            .count()
            == 0
        )

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_force_bypasses_retry_backoff(
        self, mock_secret, mock_issue, mock_expiry, db_session
    ):
        """force=True must skip the next_retry_at backoff and process anyway."""
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        # No cert on disk before; readable after the forced issue.
        mock_expiry.side_effect = [None, datetime(2027, 6, 1, tzinfo=UTC)]

        svc = _create_service(db_session)
        cert_record = Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            last_failure="previous error",
            next_retry_at=datetime.now(UTC) + timedelta(hours=3),
        )
        db_session.add(cert_record)
        db_session.commit()

        process_service_cert(db_session, svc, force=True)

        # Backoff bypassed: a real issue happened despite the future retry time.
        mock_issue.assert_called_once()
        assert db_session.query(Event).filter(Event.kind == "cert_issued").count() == 1

    @patch("app.certs.renewal_task.renew_cert")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_force_bypasses_healthy_noop(
        self, mock_secret, mock_issue, mock_renew, db_session
    ):
        """force=True must renew a healthy, far-from-expiry, matching pair that
        the unforced path would skip."""
        from app.certs.renewal_task import process_service_cert
        from app.settings_store import set_setting

        mock_secret.return_value = "cf-token"
        mock_renew.return_value = (MagicMock(), False)
        certs_root = self._tmp_certs_root(db_session, set_setting)

        svc = _create_service(db_session)
        # Healthy matching pair far from expiry -> unforced skip path.
        _write_real_pair(certs_root / svc.hostname, matching=True)

        process_service_cert(db_session, svc, force=True)

        # Healthy-noop bypassed: a renew happened instead of skipping.
        mock_issue.assert_not_called()
        mock_renew.assert_called_once()
        assert db_session.query(Event).filter(Event.kind == "cert_renewed").count() == 1

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.renew_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_force_threads_force_into_renew_cert(
        self, mock_secret, mock_renew, mock_expiry, db_session
    ):
        """A forced renewal of a far-from-expiry cert must tell renew_cert to
        force, so `lego renew` actually re-issues instead of no-opping on its
        --days skip. Without force=force the manual renew silently does nothing."""
        from app.certs.renewal_task import process_service_cert

        mock_secret.return_value = "cf-token"
        # Far from expiry (60 days > 30-day window): the unforced path would skip.
        far = datetime.now(UTC) + timedelta(days=60)
        renewed = datetime.now(UTC) + timedelta(days=90)
        mock_expiry.side_effect = [far, renewed]
        mock_renew.return_value = (MagicMock(), False)

        svc = _create_service(db_session)
        process_service_cert(db_session, svc, force=True)

        mock_renew.assert_called_once()
        assert mock_renew.call_args.kwargs.get("force") is True

    @patch("app.certs.renewal_task.read_secret")
    def test_healthy_skip_clears_stale_retry_marker(self, mock_secret, db_session):
        """A healthy, matching pair found AFTER a prior failure's backoff has
        elapsed must clear the stale next_retry_at/last_failure so the success
        state matches the issue/renew path. The expired marker no longer skips
        (now >= retry_at), so the healthy-skip branch is reached and must reset
        the pending-retry bookkeeping instead of leaving it dangling."""
        from app.certs.renewal_task import process_service_cert
        from app.settings_store import set_setting

        mock_secret.return_value = "cf-token"
        certs_root = self._tmp_certs_root(db_session, set_setting)

        svc = _create_service(db_session)
        _write_real_pair(certs_root / svc.hostname, matching=True)

        # A prior failure left a now-EXPIRED retry marker; the backoff guard must
        # not skip (retry time already passed), so the healthy-skip path runs.
        cert_record = Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            last_failure="previous error",
            next_retry_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db_session.add(cert_record)
        db_session.commit()

        process_service_cert(db_session, svc)

        cert = db_session.get(Certificate, svc.id)
        assert cert is not None
        assert cert.expires_at is not None
        # The healthy skip cleared the stale failure bookkeeping.
        assert cert.last_failure is None
        assert cert.next_retry_at is None
        # No ACME contact: a healthy matching pair must not issue or renew.
        assert (
            db_session.query(Event)
            .filter(Event.kind.in_(["cert_issued", "cert_renewed"]))
            .count()
            == 0
        )

    @staticmethod
    def _tmp_certs_root(db_session, set_setting):
        import tempfile
        from pathlib import Path

        root = Path(tempfile.mkdtemp(prefix="tb-certs-"))
        set_setting(db_session, "cert_root", str(root))
        db_session.commit()
        return root


class TestRunRenewalScan:
    @patch("app.certs.renewal_task.process_service_cert")
    @patch("app.database.SessionLocal")
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
    @patch("app.database.SessionLocal")
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

    @patch("app.certs.renewal_task.read_secret")
    @patch("app.database.SessionLocal")
    def test_scan_survives_corrupt_renewal_window(
        self, mock_session_cls, mock_secret, db_session, db_engine, caplog
    ):
        """A corrupt cert_renewal_window_days makes get_positive_int_setting fail
        loud (ValueError) for every service. The scan must catch it per-service
        and keep going - not raise out and wedge after the first, skipping every
        remaining service until the next daily run."""
        import logging

        from sqlalchemy.orm import sessionmaker

        from app.certs.renewal_task import run_renewal_scan
        from app.settings_store import set_setting

        mock_secret.return_value = "cf-token"
        # run_renewal_scan opens its own session_scope() session; bind it to the
        # shared in-memory test engine so it sees the rows committed below.
        mock_session_cls.side_effect = sessionmaker(bind=db_engine)

        set_setting(db_session, "cert_renewal_window_days", "garbage")
        _create_service(db_session)
        _create_service(
            db_session,
            hostname="second.example.com",
            edge_container_name="edge_second",
            network_name="edge_net_second",
            ts_hostname="edge-second",
        )
        db_session.commit()

        with caplog.at_level(logging.ERROR, logger="app.certs.renewal_task"):
            result = run_renewal_scan()  # must NOT raise

        # Every service failed loud, none "processed"; but the loop did NOT wedge:
        # both services were attempted (one per-service error logged each).
        assert result == 0
        attempted = [
            r for r in caplog.records
            if "Unexpected error processing cert" in r.getMessage()
        ]
        assert len(attempted) == 2


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

        assert mock_issue.called
        call_args = mock_issue.call_args
        cert_dir = call_args[0][3] if len(call_args[0]) > 3 else call_args.kwargs.get("cert_dir")
        lego_dir = call_args[0][4] if len(call_args[0]) > 4 else call_args.kwargs.get("lego_dir")
        assert str(cert_dir) == str(Path("/custom/certs") / svc.hostname)
        assert str(lego_dir) == str(Path("/custom/certs") / ".lego")
