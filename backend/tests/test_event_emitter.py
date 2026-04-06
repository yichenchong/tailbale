"""Tests for the centralized event emitter."""

import json

from app.events.event_emitter import emit_event
from app.models.event import Event


class TestEmitEvent:
    def test_creates_event(self, db_session):
        emit_event(db_session, None, "test_kind", "Something happened")
        db_session.flush()

        events = db_session.query(Event).all()
        assert len(events) == 1
        assert events[0].kind == "test_kind"
        assert events[0].message == "Something happened"
        assert events[0].level == "info"
        assert events[0].details is None

    def test_with_service_id(self, db_session):
        from app.models.service import Service
        from app.models.service_status import ServiceStatus

        svc = Service(
            name="Test", upstream_container_id="c1", upstream_container_name="test",
            upstream_port=80, hostname="test.example.com", base_domain="example.com",
            edge_container_name="edge_test", network_name="edge_net_test", ts_hostname="edge-test",
        )
        db_session.add(svc)
        db_session.flush()
        db_session.add(ServiceStatus(service_id=svc.id, phase="pending"))
        db_session.commit()

        emit_event(db_session, svc.id, "svc_event", "Linked event")
        db_session.flush()

        evt = db_session.query(Event).filter(Event.service_id == svc.id).first()
        assert evt is not None
        assert evt.service_id == svc.id

    def test_with_details(self, db_session):
        details = {"hostname": "test.example.com", "ip": "100.64.0.1"}
        emit_event(db_session, None, "dns_created", "DNS record created", details=details)
        db_session.flush()

        evt = db_session.query(Event).first()
        parsed = json.loads(evt.details)
        assert parsed["hostname"] == "test.example.com"
        assert parsed["ip"] == "100.64.0.1"

    def test_custom_level(self, db_session):
        emit_event(db_session, None, "test_error", "Bad thing", level="error")
        db_session.flush()

        evt = db_session.query(Event).first()
        assert evt.level == "error"

    def test_returns_event_object(self, db_session):
        evt = emit_event(db_session, None, "test", "msg")
        assert isinstance(evt, Event)
        assert evt.kind == "test"
