"""Tests for the Events API endpoints."""

import json

from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service(db, name="TestApp"):
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
    db.add(ServiceStatus(service_id=svc.id, phase="pending"))
    db.commit()
    return svc


def _add_event(db, service_id=None, kind="test_event", level="info", message="Test"):
    evt = Event(service_id=service_id, kind=kind, level=level, message=message)
    db.add(evt)
    db.commit()
    return evt


class TestListEvents:
    def test_returns_events(self, client, db_session):
        _add_event(db_session, kind="service_created", message="Created service")
        _add_event(db_session, kind="edge_started", message="Edge started")

        resp = client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["events"]) == 2

    def test_empty_when_no_events(self, client):
        resp = client.get("/api/events")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_filter_by_kind(self, client, db_session):
        _add_event(db_session, kind="service_created", message="Created")
        _add_event(db_session, kind="edge_started", message="Started")

        resp = client.get("/api/events?kind=edge_started")
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["kind"] == "edge_started"

    def test_filter_by_level(self, client, db_session):
        _add_event(db_session, level="info", message="Info msg")
        _add_event(db_session, level="error", message="Error msg")

        resp = client.get("/api/events?level=error")
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["level"] == "error"

    def test_filter_by_search(self, client, db_session):
        _add_event(db_session, message="DNS record created for app.example.com")
        _add_event(db_session, message="Edge container started")

        resp = client.get("/api/events?search=DNS")
        data = resp.json()
        assert data["total"] == 1
        assert "DNS" in data["events"][0]["message"]

    def test_filter_by_service_id(self, client, db_session):
        svc = _create_service(db_session)
        _add_event(db_session, service_id=svc.id, message="Linked")
        _add_event(db_session, message="Global")

        resp = client.get(f"/api/events?service_id={svc.id}")
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["service_id"] == svc.id

    def test_pagination(self, client, db_session):
        for i in range(5):
            _add_event(db_session, message=f"Event {i}")

        resp = client.get("/api/events?limit=2&offset=0")
        data = resp.json()
        assert data["total"] == 5
        assert len(data["events"]) == 2

        resp2 = client.get("/api/events?limit=2&offset=2")
        data2 = resp2.json()
        assert len(data2["events"]) == 2

    def test_event_details_parsed(self, client, db_session):
        evt = Event(kind="dns_created", level="info", message="DNS created",
                    details=json.dumps({"hostname": "app.example.com"}))
        db_session.add(evt)
        db_session.commit()

        resp = client.get("/api/events")
        data = resp.json()
        assert data["events"][0]["details"]["hostname"] == "app.example.com"


class TestServiceEvents:
    def test_returns_service_events(self, client, db_session):
        svc = _create_service(db_session)
        _add_event(db_session, service_id=svc.id, kind="edge_started", message="Started")
        _add_event(db_session, service_id=svc.id, kind="dns_created", message="DNS")
        _add_event(db_session, message="Global event")

        resp = client.get(f"/api/events/services/{svc.id}")
        data = resp.json()
        assert data["total"] == 2

    def test_404_for_missing_service(self, client):
        resp = client.get("/api/events/services/svc_nonexistent")
        assert resp.status_code == 404

    def test_filter_by_kind(self, client, db_session):
        svc = _create_service(db_session)
        _add_event(db_session, service_id=svc.id, kind="edge_started")
        _add_event(db_session, service_id=svc.id, kind="dns_created")

        resp = client.get(f"/api/events/services/{svc.id}?kind=dns_created")
        data = resp.json()
        assert data["total"] == 1

    def test_filter_by_level(self, client, db_session):
        svc = _create_service(db_session)
        _add_event(db_session, service_id=svc.id, level="info")
        _add_event(db_session, service_id=svc.id, level="error")

        resp = client.get(f"/api/events/services/{svc.id}?level=error")
        data = resp.json()
        assert data["total"] == 1
