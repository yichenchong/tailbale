"""Tests for M5 cert API endpoints (renew-cert, logs/cert)."""

import json
from unittest.mock import patch

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


class TestRenewCertEndpoint:
    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_success(self, mock_process, client):
        svc_id = _create_service(client).json()["id"]

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_process.assert_called_once()

    def test_renew_cert_nonexistent_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/renew-cert")
        assert resp.status_code == 404

    @patch("app.certs.renewal_task.process_service_cert")
    def test_renew_cert_failure(self, mock_process, client):
        svc_id = _create_service(client).json()["id"]
        mock_process.side_effect = Exception("ACME rate limited")

        resp = client.post(f"/api/services/{svc_id}/renew-cert")
        assert resp.status_code == 500


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

    def test_cert_logs_include_details(self, client, db_session):
        svc_id = _create_service(client).json()["id"]

        db_session.add(Event(
            service_id=svc_id, kind="cert_issued", level="info",
            message="Cert issued",
            details=json.dumps({"hostname": "nextcloud.example.com"}),
        ))
        db_session.commit()

        resp = client.get(f"/api/services/{svc_id}/logs/cert")
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["details"]["hostname"] == "nextcloud.example.com"
