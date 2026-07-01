"""Tests for M5 cert API endpoints (renew-cert, logs/cert)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.models.certificate import Certificate
from app.models.event import Event


def _create_service(client, **overrides):
    """Helper to create a service with defaults."""
    body = {
        "name": "Nextcloud",
        "upstream_container_id": "abc123def456",
        "upstream_container_name": "nextcloud",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "nextcloud.example.com",
        "base_domain": "example.com",
    }
    body.update(overrides)
    return client.post("/api/services", json=body)


def _upsert_cert(db, svc_id, hostname, *, expires_at, last_failure=None):
    """Insert or update the Certificate row for a service."""
    cert = db.get(Certificate, svc_id)
    if cert is None:
        cert = Certificate(service_id=svc_id, hostname=hostname)
        db.add(cert)
    cert.expires_at = expires_at
    cert.last_failure = last_failure
    db.commit()
    return cert


class TestRenewCertEndpoint:
    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_success(self, mock_process, client):
        svc_id = _create_service(client).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["performed"] is True
        assert data["needs_force"] is False
        mock_process.assert_called_once()
        # The manual endpoint always asks for a real issue/renew.
        assert mock_process.call_args.kwargs == {"force": True}

    def test_renew_cert_nonexistent_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/renew-cert")
        assert resp.status_code == 404

    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_failure(self, mock_process, client):
        svc_id = _create_service(client).json()["id"]
        mock_process.side_effect = Exception("ACME rate limited")

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 500

    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_far_healthy_no_force_requires_force(
        self, mock_process, client, db_session
    ):
        svc_id = _create_service(client).json()["id"]
        _upsert_cert(
            db_session, svc_id, "nextcloud.example.com",
            expires_at=datetime.now(UTC) + timedelta(days=60),
        )

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["performed"] is False
        assert data["needs_force"] is True
        assert "healthy" in data["message"]
        # A far-from-expiry healthy cert must not contact Let's Encrypt.
        mock_process.assert_not_called()

    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_far_healthy_force_renews(self, mock_process, client, db_session):
        svc_id = _create_service(client).json()["id"]
        _upsert_cert(
            db_session, svc_id, "nextcloud.example.com",
            expires_at=datetime.now(UTC) + timedelta(days=60),
        )

        resp = client.post(f"/api/services/{svc_id}/renew-cert?force=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["performed"] is True
        assert data["needs_force"] is False
        mock_process.assert_called_once()
        assert mock_process.call_args.kwargs == {"force": True}

    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_near_expiry_no_force_performs(self, mock_process, client, db_session):
        svc_id = _create_service(client).json()["id"]
        _upsert_cert(
            db_session, svc_id, "nextcloud.example.com",
            expires_at=datetime.now(UTC) + timedelta(days=5),
        )

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        data = resp.json()
        assert data["performed"] is True
        assert data["needs_force"] is False
        mock_process.assert_called_once()
        assert mock_process.call_args.kwargs == {"force": True}

    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_expired_no_force_performs(self, mock_process, client, db_session):
        svc_id = _create_service(client).json()["id"]
        _upsert_cert(
            db_session, svc_id, "nextcloud.example.com",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        assert resp.json()["performed"] is True
        mock_process.assert_called_once()

    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_missing_cert_no_force_performs(self, mock_process, client):
        svc_id = _create_service(client).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        assert resp.json()["performed"] is True
        mock_process.assert_called_once()


class TestCertLogsEndpoint:
    def test_cert_logs_empty(self, client):
        svc_id = _create_service(client).json()["id"]

        resp = client.get(f"/api/services/{svc_id}/logs/cert")
        assert resp.status_code == 200
        assert resp.json()["events"] == []

    def test_cert_logs_returns_cert_events(self, client, db_session):
        svc_id = _create_service(client).json()["id"]

        # Insert cert-related events
        events_data = [
            Event(service_id=svc_id, kind="cert_issued", level="info",
                  message="Certificate issued"),
            Event(service_id=svc_id, kind="cert_renewed", level="info",
                  message="Certificate renewed"),
            Event(service_id=svc_id, kind="cert_failed", level="error",
                  message="Certificate failed"),
            # This should NOT be returned (different kind)
            Event(service_id=svc_id, kind="service_created", level="info",
                  message="Service created"),
        ]
        for evt in events_data:
            db_session.add(evt)
        db_session.commit()

        resp = client.get(f"/api/services/{svc_id}/logs/cert")
        assert resp.status_code == 200
        data = resp.json()
        # Only cert-related events (issued, renewed, failed)
        assert len(data["events"]) == 3
        kinds = {e["kind"] for e in data["events"]}
        assert kinds == {"cert_issued", "cert_renewed", "cert_failed"}

    def test_cert_logs_nonexistent_service(self, client):
        resp = client.get("/api/services/svc_nonexistent/logs/cert")
        assert resp.status_code == 404

    def test_cert_logs_limit_parameter(self, client, db_session):
        svc_id = _create_service(client).json()["id"]

        # Insert more events than limit
        for i in range(5):
            db_session.add(Event(
                service_id=svc_id, kind="cert_issued", level="info",
                message=f"Cert event {i}",
            ))
        db_session.commit()

        resp = client.get(f"/api/services/{svc_id}/logs/cert?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == 3

    def test_cert_logs_rejects_invalid_limit(self, client):
        resp = client.get("/api/services/svc_nonexistent/logs/cert?limit=0")
        assert resp.status_code == 422

        resp = client.get("/api/services/svc_nonexistent/logs/cert?limit=501")
        assert resp.status_code == 422


    def test_cert_logs_include_details(self, client, db_session):
        svc_id = _create_service(client).json()["id"]

        db_session.add(Event(
            service_id=svc_id, kind="cert_issued", level="info",
            message="Cert issued",
            details={"hostname": "nextcloud.example.com"},
        ))
        db_session.commit()

        resp = client.get(f"/api/services/{svc_id}/logs/cert")
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["details"]["hostname"] == "nextcloud.example.com"
