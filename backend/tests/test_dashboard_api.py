"""Tests for the Dashboard summary API endpoint."""

from datetime import datetime, timedelta, timezone

from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service(db, name="TestApp", phase="pending"):
    slug = name.lower().replace(" ", "")
    svc = Service(
        name=name, upstream_container_id="abc123",
        upstream_container_name=slug, upstream_scheme="http",
        upstream_port=80, hostname=f"{slug}.example.com",
        base_domain="example.com", edge_container_name=f"edge_{slug}",
        network_name=f"edge_net_{slug}", ts_hostname=f"edge-{slug}",
    )
    db.add(svc)
    db.flush()
    db.add(ServiceStatus(service_id=svc.id, phase=phase))
    db.commit()
    return svc


class TestDashboardSummary:
    def test_empty_dashboard(self, client):
        resp = client.get("/api/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["services"]["total"] == 0
        assert data["services"]["healthy"] == 0
        assert len(data["expiring_certs"]) == 0
        assert len(data["recent_errors"]) == 0
        assert len(data["recent_events"]) == 0

    def test_service_counts(self, client, db_session):
        _create_service(db_session, "App1", phase="healthy")
        _create_service(db_session, "App2", phase="healthy")
        _create_service(db_session, "App3", phase="warning")
        _create_service(db_session, "App4", phase="failed")

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert data["services"]["total"] == 4
        assert data["services"]["healthy"] == 2
        assert data["services"]["warning"] == 1
        assert data["services"]["error"] == 1

    def test_expiring_certs(self, client, db_session):
        svc = _create_service(db_session, "Expiring")
        cert = Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        )
        db_session.add(cert)
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["expiring_certs"]) == 1
        assert data["expiring_certs"][0]["service_name"] == "Expiring"

    def test_non_expiring_cert_excluded(self, client, db_session):
        svc = _create_service(db_session, "Healthy")
        cert = Certificate(
            service_id=svc.id,
            hostname=svc.hostname,
            expires_at=datetime.now(timezone.utc) + timedelta(days=60),
        )
        db_session.add(cert)
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["expiring_certs"]) == 0

    def test_recent_errors(self, client, db_session):
        db_session.add(Event(kind="reconcile_failed", level="error", message="Failed!"))
        db_session.add(Event(kind="service_created", level="info", message="Created"))
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["recent_errors"]) == 1
        assert data["recent_errors"][0]["message"] == "Failed!"

    def test_recent_events(self, client, db_session):
        for i in range(25):
            db_session.add(Event(kind="test", level="info", message=f"Event {i}"))
        db_session.commit()

        resp = client.get("/api/dashboard/summary")
        data = resp.json()
        assert len(data["recent_events"]) == 20  # limited to 20
