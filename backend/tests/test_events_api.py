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

    def test_filter_by_search_treats_like_wildcards_literally(self, client, db_session):
        _add_event(db_session, message="Backup 100% complete")
        _add_event(db_session, message="Backup 1000 complete")
        _add_event(db_session, message="Probe app_1 ok")
        _add_event(db_session, message="Probe appX1 ok")

        resp = client.get("/api/events?search=100%25")
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["message"] == "Backup 100% complete"

        resp = client.get("/api/events?search=app_1")
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["message"] == "Probe app_1 ok"

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

    def test_pagination_is_deterministic_with_tied_timestamps(self, client, db_session):
        # SQLite's CURRENT_TIMESTAMP has second resolution, so a burst of events
        # (e.g. one reconcile) shares an identical created_at. Ordering by
        # created_at alone leaves ties unspecified, so OFFSET/LIMIT pagination
        # could skip or duplicate events across pages. The query breaks ties by
        # id, so paging must yield every event exactly once in a stable order.
        from datetime import UTC, datetime

        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        for i in range(10):
            db_session.add(Event(
                kind="reconcile_completed", level="info",
                message=f"Event {i}", created_at=ts,
            ))
        db_session.commit()

        seen: list[str] = []
        for offset in range(0, 10, 3):
            resp = client.get(f"/api/events?limit=3&offset={offset}")
            assert resp.status_code == 200
            seen.extend(e["id"] for e in resp.json()["events"])

        assert len(seen) == 10
        assert len(set(seen)) == 10  # no event skipped or duplicated across pages
        assert seen == sorted(seen, reverse=True)  # deterministic id tiebreak

    def test_rejects_invalid_pagination_bounds(self, client):
        resp = client.get("/api/events?limit=0&offset=-1")
        assert resp.status_code == 422

        resp = client.get("/api/events/services/svc_nonexistent?limit=501")
        assert resp.status_code == 422


    def test_event_details_parsed(self, client, db_session):
        evt = Event(kind="dns_created", level="info", message="DNS created",
                    details=json.dumps({"hostname": "app.example.com"}))
        db_session.add(evt)
        db_session.commit()

        resp = client.get("/api/events")
        data = resp.json()
        assert data["events"][0]["details"]["hostname"] == "app.example.com"

    def test_invalid_event_details_do_not_break_listing(self, client, db_session):
        evt = Event(kind="legacy_event", level="info", message="Legacy", details="{not json")
        db_session.add(evt)
        db_session.commit()

        resp = client.get("/api/events")
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"][0]["details"] is None


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
