"""Tests for the centralized event emitter."""

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
        parsed = evt.details
        assert parsed["hostname"] == "test.example.com"
        assert parsed["ip"] == "100.64.0.1"

    def test_empty_details_are_preserved(self, db_session):
        emit_event(db_session, None, "empty_details", "Empty details", details={})
        db_session.flush()

        evt = db_session.query(Event).first()
        assert evt.details == {}

    def test_custom_level(self, db_session):
        emit_event(db_session, None, "test_error", "Bad thing", level="error")
        db_session.flush()

        evt = db_session.query(Event).first()
        assert evt.level == "error"

    def test_returns_event_object(self, db_session):
        evt = emit_event(db_session, None, "test", "msg")
        assert isinstance(evt, Event)
        assert evt.kind == "test"

    def test_does_not_commit_caller_owns_transaction(self, db_session):
        # Contract: emit_event adds but never commits — every caller wraps it in
        # db_write_section/commit_with_lock and relies on the event being part of
        # that atomic boundary. If emit_event committed internally, a caller's
        # later rollback could not undo the event, breaking all-or-nothing
        # semantics. Proof: roll back after emit and the row must be gone.
        emit_event(db_session, None, "rollback_kind", "Should vanish on rollback")
        db_session.rollback()

        assert db_session.query(Event).filter(Event.kind == "rollback_kind").count() == 0

    def test_empty_details_round_trip_through_db(self, db_session):
        # An empty dict must survive a real commit/expire/reload — exercising the
        # JSONEncodedDict bind (``{}`` -> ``"{}"``) AND result decode
        # (``"{}"`` -> ``{}``), not just the in-memory attribute. A guarded
        # ``if not value`` decode would wrongly collapse the stored "{}" to None.
        emit_event(db_session, None, "empty_details", "Empty details", details={})
        db_session.commit()
        db_session.expire_all()

        evt = db_session.query(Event).filter(Event.kind == "empty_details").one()
        assert evt.details == {}


class TestEventKindsRegistry:
    def test_contains_representative_kinds(self):
        from app.events.event_emitter import EVENT_KINDS

        for kind in (
            "reconcile_completed",
            "cert_issued",
            "dns_orphan_dismissed",
            "service_created",
            "edge_started",
            "probe_retry_phase_change",
        ):
            assert kind in EVENT_KINDS

    def test_registered_kind_emits_no_drift_warning(self, db_session, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="app.events.event_emitter"):
            emit_event(db_session, None, "reconcile_completed", "All good")

        drift = [r for r in caplog.records if r.name == "app.events.event_emitter"]
        assert drift == []

    def test_unregistered_kind_logs_drift_warning_but_persists(self, db_session, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="app.events.event_emitter"):
            evt = emit_event(db_session, None, "totally_made_up_kind", "Drifted")
        db_session.flush()

        # Non-fatal: the event is still created and persisted.
        assert evt.kind == "totally_made_up_kind"
        assert (
            db_session.query(Event)
            .filter(Event.kind == "totally_made_up_kind")
            .count()
            == 1
        )

        # The drift canary fires exactly once, naming the offending kind.
        drift = [
            r
            for r in caplog.records
            if r.name == "app.events.event_emitter" and r.levelno == logging.WARNING
        ]
        assert len(drift) == 1
        assert "totally_made_up_kind" in drift[0].getMessage()

    def test_every_emitted_literal_kind_is_registered(self):
        """Static guard: every event ``kind`` emitted as a string literal across
        ``backend/app`` must be in EVENT_KINDS, so the frontend kind filter (built
        from the registry) can never silently miss an emitted kind. Covers
        ``emit_event(db, sid, "<kind>", ...)`` call sites and the reconciler's
        ``{"kind": "<kind>", "message": ...}`` event dicts threaded through
        ``_persist_status``. Dynamic kinds (variables / ``event["kind"]``) are out
        of static reach and rely on the runtime drift canary instead.
        """
        import ast
        from pathlib import Path

        from app.events.event_emitter import EVENT_KINDS

        app_dir = Path(__file__).resolve().parents[1] / "app"
        emitted: set[str] = set()

        def _str_const(node) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            return None

        for path in app_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            in_reconciler = "reconciler" in path.parts
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = (
                        func.attr if isinstance(func, ast.Attribute)
                        else func.id if isinstance(func, ast.Name)
                        else None
                    )
                    if name == "emit_event":
                        if len(node.args) >= 3 and (kind := _str_const(node.args[2])):
                            emitted.add(kind)
                        for kw in node.keywords:
                            if kw.arg == "kind" and (kind := _str_const(kw.value)):
                                emitted.add(kind)
                elif in_reconciler and isinstance(node, ast.Dict):
                    keys = {_str_const(k) for k in node.keys}
                    if {"kind", "message"} <= keys:
                        for k, v in zip(node.keys, node.values, strict=True):
                            if _str_const(k) == "kind" and (kind := _str_const(v)):
                                emitted.add(kind)

        # Sanity floor: a broken scan that finds nothing must fail loudly rather
        # than vacuously pass.
        assert len(emitted) >= 20, f"static kind scan found too few kinds: {sorted(emitted)}"

        unregistered = emitted - EVENT_KINDS
        assert not unregistered, (
            "emit_event call sites use kinds missing from EVENT_KINDS "
            f"(add them to the registry): {sorted(unregistered)}"
        )
