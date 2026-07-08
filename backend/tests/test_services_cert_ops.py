"""renew-cert endpoint tests: disabled service, healthy-needs-force, and force paths.

Mirrors app.services.cert_ops (split from test_services_api.py)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.models.certificate import Certificate
from tests._services_helpers import (
    _create_service,
)


class TestRenewCertDisabledService:
    """process_service_cert skips a disabled service outright, so the renew-cert
    endpoint must not claim a cert was processed. It reports performed:false and
    never calls into the cert pipeline. Enabled-service behavior is unchanged."""

    @patch("app.certs.renewal_task.process_service_cert")
    def test_disabled_service_reports_not_performed(self, mock_process, client):
        svc_id = _create_service(
            client, name="App", hostname="app.example.com", enabled=False
        ).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["performed"] is False
        assert data["needs_force"] is False
        assert "disabled" in data["message"]
        # A disabled service's cert is never served; the pipeline is skipped so
        # the endpoint cannot report a phantom success.
        mock_process.assert_not_called()

    @patch("app.certs.renewal_task.process_service_cert")
    def test_disabled_service_with_force_still_not_performed(self, mock_process, client):
        svc_id = _create_service(
            client, name="App", hostname="app.example.com", enabled=False
        ).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/renew-cert?force=true")
        assert resp.status_code == 200
        assert resp.json()["performed"] is False
        mock_process.assert_not_called()


class TestRenewCertHealthyNeedsForce:
    """An ENABLED service whose cert is healthy and far from expiry must NOT
    silently renew on a plain request: contacting Let's Encrypt for a still-valid
    cert wastes a round-trip and risks rate limits, so the endpoint refuses with
    performed:false / needs_force:true and never enters the cert pipeline.
    ``?force=true`` opts in, bypassing the healthy-noop and actually processing
    the cert (the success path that reports performed:true)."""

    def _seed_healthy_cert(self, db_session, svc_id):
        # Expires far beyond the 30-day default renewal window, no prior failure,
        # so service_layer.renew_cert classifies it far_healthy. expires_at is a
        # NaiveUTCDateTime column, so store a naive value (as_utc reattaches UTC).
        cert = Certificate(service_id=svc_id, hostname="app.example.com")
        cert.expires_at = (datetime.now(UTC) + timedelta(days=365)).replace(tzinfo=None)
        db_session.add(cert)
        db_session.commit()

    @patch("app.certs.renewal_task.process_service_cert")
    def test_healthy_cert_refuses_without_force(self, mock_process, client, db_session):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        self._seed_healthy_cert(db_session, svc_id)

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        data = resp.json()
        assert data["performed"] is False
        assert data["needs_force"] is True
        # Refusing must not contact the cert pipeline (no Let's Encrypt round-trip).
        mock_process.assert_not_called()

    @patch("app.certs.renewal_task.process_service_cert")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_healthy_cert_renews_with_force(self, mock_secret, mock_process, client, db_session):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        self._seed_healthy_cert(db_session, svc_id)

        resp = client.post(f"/api/services/{svc_id}/renew-cert?force=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["performed"] is True
        assert data["needs_force"] is False
        # force=true bypasses the healthy-noop and runs the pipeline with force=True.
        mock_process.assert_called_once()
        assert mock_process.call_args.kwargs.get("force") is True

class TestRenewCertFarFromExpiryWithFailureStillRenews:
    """A cert far from expiry is refused (needs_force) ONLY when it is genuinely
    healthy. A stale far-future expires_at left over from a prior success, paired
    with a recorded ``last_failure`` (a later renewal that errored), must NOT be
    treated as far-healthy: renew_cert gates far_healthy on ``last_failure is
    None``, so such a cert proceeds to a real renewal on a plain request instead
    of being wrongly refused with needs_force. Guards that subtle condition."""

    @patch("app.certs.renewal_task.process_service_cert")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_far_future_expiry_with_last_failure_renews_without_force(
        self, mock_secret, mock_process, client, db_session
    ):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        # Far beyond the 30-day window (would be far_healthy) BUT carrying a prior
        # failure — so far_healthy must be False and the pipeline must run.
        cert = Certificate(service_id=svc_id, hostname="app.example.com")
        cert.expires_at = (datetime.now(UTC) + timedelta(days=365)).replace(tzinfo=None)
        cert.last_failure = "previous DNS-01 error"
        db_session.add(cert)
        db_session.commit()

        # A successful renewal clears the stale failure.
        def fake_process(db, svc, *, force):
            c = db.get(Certificate, svc.id)
            c.last_failure = None
            c.expires_at = (datetime.now(UTC) + timedelta(days=90)).replace(tzinfo=None)
            db.commit()

        mock_process.side_effect = fake_process

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        data = resp.json()
        # Must NOT refuse: a failed cert is not far-healthy even far from expiry.
        assert data["needs_force"] is False
        assert data["performed"] is True
        assert data["success"] is True
        mock_process.assert_called_once()


class TestRenewCertForceReportsFailureHonestly:
    """When a forced renewal RUNS but the cert pipeline fails internally,
    process_service_cert SWALLOWS the error (records last_failure, emits
    cert_failed, returns normally without raising). cert_ops must report that
    honestly - success:False + a failure message - not the "Certificate
    processed" success message the frontend surfaces to the operator verbatim."""

    @patch("app.certs.renewal_task.process_service_cert")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_force_renew_internal_failure_reports_failure(
        self, mock_secret, mock_process, client, db_session
    ):
        svc_id = _create_service(
            client, name="App", hostname="app.example.com"
        ).json()["id"]

        # Mimic process_service_cert's swallow: it stamps last_failure on the
        # cert row and returns normally (no raise), exactly as the real failure
        # path does when lego/DNS errors out.
        def fake_process(db, svc, *, force):
            cert = db.get(Certificate, svc.id)
            if cert is None:
                cert = Certificate(service_id=svc.id, hostname=svc.hostname)
                db.add(cert)
            cert.last_failure = "DNS challenge failed"
            db.commit()

        mock_process.side_effect = fake_process

        resp = client.post(f"/api/services/{svc_id}/renew-cert?force=true")
        assert resp.status_code == 200
        data = resp.json()
        mock_process.assert_called_once()
        # The pipeline DID run (performed) but FAILED; the response must say so.
        # Pre-fix: success:True + message "Certificate processed ..." (a lie the
        # frontend showed the operator verbatim while last_failure was ignored).
        assert data["performed"] is True
        assert data["success"] is False
        assert data["last_failure"] == "DNS challenge failed"
        assert "failed" in data["message"].lower()
        assert "DNS challenge failed" in data["message"]
