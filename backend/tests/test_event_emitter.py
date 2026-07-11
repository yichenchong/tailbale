"""Tests for the centralized event emitter."""

import ast
import logging
from datetime import datetime
from pathlib import Path

from app.events.event_emitter import EVENT_KINDS, emit_event
from app.events.querying import escape_like, query_events
from app.events.serialization import event_to_dict
from app.events.types import EventKind
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.timeutil import iso


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

        with caplog.at_level(logging.WARNING, logger="app.events.event_emitter"):
            emit_event(db_session, None, "reconcile_completed", "All good")

        drift = [r for r in caplog.records if r.name == "app.events.event_emitter"]
        assert drift == []

    def test_unregistered_kind_logs_drift_warning_but_persists(self, db_session, caplog):

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

    def test_registry_is_derived_from_eventkind_catalogue(self):
        """AR14 single-source guard: EVENT_KINDS is exactly the set of EventKind
        string constants (no hand-maintained mirror can drift from the catalogue).
        """
        expected = {
            value
            for name, value in vars(EventKind).items()
            if not name.startswith("_") and isinstance(value, str)
        }
        assert expected == EVENT_KINDS
        assert len(EVENT_KINDS) == 27

    def test_every_emitted_kind_is_registered(self):
        """Static guard: every event ``kind`` emitted across ``backend/app`` — as
        a string literal, an ``EventKind.<NAME>`` constant, or a reconciler
        ``{"kind": ..., "message": ...}`` dict — must resolve to a value in
        EVENT_KINDS, so the frontend kind filter (built from the registry) can
        never silently miss an emitted kind. Also catches a typo'd
        ``EventKind.<NAME>`` (unknown attribute). Dynamic kinds (arbitrary
        variables / ``event["kind"]``) are out of static reach and rely on the
        runtime drift canary instead.
        """
        app_dir = Path(__file__).resolve().parents[1] / "app"
        emitted: set[str] = set()

        def _str_const(node) -> str | None:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            return None

        def _kind_value(node) -> str | None:
            # A bare string literal ...
            literal = _str_const(node)
            if literal is not None:
                return literal
            # ... or an EventKind.<NAME> constant reference (AR14).
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "EventKind"
            ):
                value = getattr(EventKind, node.attr, None)
                assert isinstance(value, str), (
                    f"emit site references unknown EventKind.{node.attr}"
                )
                return value
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
                        if len(node.args) >= 3 and (kind := _kind_value(node.args[2])):
                            emitted.add(kind)
                        for kw in node.keywords:
                            if kw.arg == "kind" and (kind := _kind_value(kw.value)):
                                emitted.add(kind)
                elif in_reconciler and isinstance(node, ast.Dict):
                    keys = {_str_const(k) for k in node.keys}
                    if {"kind", "message"} <= keys:
                        for k, v in zip(node.keys, node.values, strict=True):
                            if _str_const(k) == "kind" and (kind := _kind_value(v)):
                                emitted.add(kind)

        # Sanity floor: a broken scan that finds nothing must fail loudly rather
        # than vacuously pass.
        assert len(emitted) >= 20, f"static kind scan found too few kinds: {sorted(emitted)}"

        unregistered = emitted - EVENT_KINDS
        assert not unregistered, (
            "emit_event call sites use kinds missing from EVENT_KINDS "
            f"(add them to the registry): {sorted(unregistered)}"
        )


class TestEventToDict:
    """Direct coverage of the shared event serialization shape. It is the single
    wire form reused by the events, dashboard, and services routers, but was only
    ever exercised indirectly through those routers — a key rename or a dropped
    ``iso()`` on ``created_at`` would slip past every existing test."""

    def _make_event(self, db_session):
        evt = Event(
            service_id=None,
            kind="dns_created",
            level="warning",
            message="DNS record created",
            details={"hostname": "a.example.com"},
            created_at=datetime(2026, 1, 2, 3, 4, 5),
        )
        db_session.add(evt)
        db_session.flush()
        return evt

    def test_full_record_shape(self, db_session):
        evt = self._make_event(db_session)
        assert event_to_dict(evt) == {
            "id": evt.id,
            "service_id": None,
            "kind": "dns_created",
            "level": "warning",
            "message": "DNS record created",
            "details": {"hostname": "a.example.com"},
            "created_at": iso(evt.created_at),
        }

    def test_created_at_uses_naive_iso_wire_format(self, db_session):
        # The wire format is the naive isoformat (no tz designator) that the
        # frontend's parseBackendDate relies on — pin it so a switch to str(dt)
        # or an aware format can't slip through.
        evt = self._make_event(db_session)
        result = event_to_dict(evt)
        assert result["created_at"] == "2026-01-02T03:04:05"
        assert "+" not in result["created_at"]
        assert not result["created_at"].endswith("Z")

    def test_field_projection_selects_exact_subset(self, db_session):
        # A projected subset preserves the caller's exact key set AND order (the
        # dict comprehension iterates ``fields``), never leaking unrequested keys.
        evt = self._make_event(db_session)
        subset = event_to_dict(evt, fields=("id", "kind", "message"))
        assert list(subset.keys()) == ["id", "kind", "message"]
        assert subset == {
            "id": evt.id,
            "kind": "dns_created",
            "message": "DNS record created",
        }

    def test_empty_fields_projects_empty_dict(self, db_session):
        evt = self._make_event(db_session)
        assert event_to_dict(evt, fields=()) == {}


class TestEscapeLike:
    """LIKE/ILIKE metacharacter escaping. Backslash MUST be escaped first, else
    the backslashes introduced for ``%``/``_`` would themselves be doubled and
    the escape char would no longer protect the wildcards."""

    def test_wildcards_and_backslash_escaped(self):
        assert escape_like("a_b%c") == r"a\_b\%c"

    def test_backslash_escaped_before_wildcards(self):
        # If ``\`` were escaped AFTER ``%``/``_``, the ``\`` prepended to the
        # wildcards would get doubled and the pattern would break. Pin the order.
        assert escape_like(r"x\_y") == r"x\\\_y"

    def test_plain_text_unchanged(self):
        assert escape_like("plain text") == "plain text"


class TestQueryEvents:
    """Direct coverage of the shared event-query helper: filters, whitespace-safe
    escaped search, stable ordering, and count-before-pagination semantics."""

    def _add(self, db, *, kind="reconcile_completed", level="info",
             message="msg", service_id=None, created_at=None):
        evt = Event(
            service_id=service_id,
            kind=kind,
            level=level,
            message=message,
            created_at=created_at or datetime(2026, 1, 1, 0, 0, 0),
        )
        db.add(evt)
        db.flush()
        return evt.id

    def test_search_escapes_underscore_wildcard(self, db_session):
        # The literal search term "a_b" must match "a_b" but NOT "axb": if
        # escape_like/escape="\\" were broken, "_" would act as a single-char
        # LIKE wildcard and wrongly match "axb".
        hit = self._add(db_session, message="a_b literal")
        self._add(db_session, message="axb wildcard trap")
        db_session.commit()

        rows, total = query_events(db_session, search="a_b")

        assert total == 1
        assert {r.id for r in rows} == {hit}

    def test_search_escapes_percent_wildcard(self, db_session):
        # A literal "%" in the term must not become a match-anything wildcard.
        hit = self._add(db_session, message="save 50% today")
        self._add(db_session, message="no discount here")
        db_session.commit()

        rows, _ = query_events(db_session, search="50%", include_total=False)

        assert {r.id for r in rows} == {hit}

    def test_search_is_case_insensitive_and_substring(self, db_session):
        hit = self._add(db_session, message="Reconcile COMPLETED for svc")
        db_session.commit()

        rows, _ = query_events(db_session, search="completed", include_total=False)

        assert {r.id for r in rows} == {hit}

    def test_whitespace_only_search_is_no_filter(self, db_session):
        self._add(db_session, message="one")
        self._add(db_session, message="two")
        db_session.commit()

        rows, total = query_events(db_session, search="   ")

        assert total == 2
        assert len(rows) == 2

    def test_filters_by_service_kind_level_and_kinds(self, db_session):
        svc = Service(
            id="svc_q", name="Q", upstream_container_id="c1",
            upstream_container_name="q", upstream_port=80,
            hostname="q.example.com", base_domain="example.com",
            edge_container_name="edge_q", network_name="net_q",
            ts_hostname="edge-q",
        )
        db_session.add(svc)
        db_session.flush()
        target = self._add(
            db_session, kind="cert_issued", level="warning",
            message="target", service_id="svc_q",
        )
        self._add(db_session, kind="cert_renewed", level="warning", message="other kind")
        self._add(db_session, kind="cert_issued", level="info", message="other level")
        self._add(db_session, kind="cert_issued", level="warning", message="no service")
        db_session.commit()

        rows, total = query_events(
            db_session, service_id="svc_q", kind="cert_issued", level="warning",
            kinds=("cert_issued", "cert_renewed"),
        )

        assert total == 1
        assert {r.id for r in rows} == {target}

    def test_stable_order_created_at_desc_then_id_desc(self, db_session):
        # Ties on created_at break by id desc (deterministic pagination). A newer
        # event always precedes an older one regardless of insertion order.
        newer = self._add(db_session, created_at=datetime(2026, 6, 1, 12, 0, 0))
        older = self._add(db_session, created_at=datetime(2026, 1, 1, 12, 0, 0))
        tie_a = self._add(db_session, created_at=datetime(2026, 3, 1, 12, 0, 0))
        tie_b = self._add(db_session, created_at=datetime(2026, 3, 1, 12, 0, 0))
        db_session.commit()

        rows, _ = query_events(db_session, include_total=False)
        ids = [r.id for r in rows]

        assert ids[0] == newer
        assert ids[-1] == older
        tie_slice = [i for i in ids if i in (tie_a, tie_b)]
        assert tie_slice == sorted((tie_a, tie_b), reverse=True)

    def test_count_reflects_full_match_not_page(self, db_session):
        # total must count every match, independent of limit/offset paging.
        for _ in range(5):
            self._add(db_session)
        db_session.commit()

        rows, total = query_events(db_session, limit=2, offset=1)

        assert total == 5
        assert len(rows) == 2

    def test_include_total_false_skips_count(self, db_session):
        self._add(db_session)
        db_session.commit()

        rows, total = query_events(db_session, include_total=False)

        assert total is None
        assert len(rows) == 1
