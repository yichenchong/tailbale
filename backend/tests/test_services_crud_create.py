"""Service CRUD API tests: create + read (list/get), service/snippet-audit events, and create-time validation (hostname, base_domain, upstream container/port).

Covers create/read behavior after the app.services facade split from test_services_crud.py."""

import contextlib
import hashlib
import logging
import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

import docker
import pytest
from fastapi import HTTPException
from sqlalchemy import event as sa_event

from app.locks import _SERVICE_LIFECYCLE_MUTEX
from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.routers.services import (
    _validate_upstream as _real_validate_upstream,
)
from app.routers.services import (
    _validate_upstream_port,
)
from app.services.errors import DockerUnavailable
from tests._services_helpers import (
    _create_service,
    _make_container,
)


class TestCreateService:
    def test_create_basic(self, client):
        resp = _create_service(client)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Nextcloud"
        assert data["hostname"] == "nextcloud.example.com"
        assert data["upstream_container_name"] == "nextcloud"
        assert data["upstream_port"] == 80
        assert data["enabled"] is True
        assert data["id"].startswith("svc_")
        assert data["edge_container_name"] == "edge_nextcloud"
        assert data["network_name"] == "edge_net_nextcloud"
        assert data["ts_hostname"] == "edge-nextcloud"

    def test_create_with_status(self, client):
        resp = _create_service(client)
        data = resp.json()
        assert data["status"] is not None
        assert data["status"]["phase"] == "pending"
        assert "reconciliation" in data["status"]["message"].lower()

    def test_create_disabled_starts_disabled_without_reconcile(self, client):
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = _create_service(client, enabled=False)

        assert resp.status_code == 201
        data = resp.json()
        assert data["enabled"] is False
        assert data["status"]["phase"] == "disabled"
        assert data["status"]["message"] == "Service is disabled"
        mock_reconcile.assert_not_called()

    def test_create_emits_event(self, client):
        resp = _create_service(client)
        assert resp.status_code == 201

    def test_create_with_all_fields(self, client):
        resp = _create_service(
            client,
            name="Jellyfin",
            upstream_container_id="jelly123",
            upstream_container_name="jellyfin",
            upstream_scheme="https",
            upstream_port=8096,
            healthcheck_path="/health",
            hostname="jellyfin.example.com",
            base_domain="example.com",
            enabled=False,
            preserve_host_header=False,
            custom_caddy_snippet="header X-Custom true",
            app_profile="jellyfin",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["upstream_scheme"] == "https"
        assert data["upstream_port"] == 8096
        assert data["healthcheck_path"] == "/health"
        assert data["enabled"] is False
        assert data["preserve_host_header"] is False
        assert data["custom_caddy_snippet"] == "header X-Custom true"
        assert data["app_profile"] == "jellyfin"

    def test_duplicate_hostname_rejected(self, client):
        _create_service(client, hostname="app.example.com")
        resp = _create_service(client, name="Other", hostname="app.example.com")
        assert resp.status_code == 409
        assert "already in use" in resp.json()["detail"]

    def test_invalid_hostname_rejected(self, client):
        resp = _create_service(client, hostname="INVALID HOSTNAME!")
        assert resp.status_code == 422

    def test_invalid_port_rejected(self, client):
        resp = _create_service(client, upstream_port=0)
        assert resp.status_code == 422

    def test_invalid_port_too_high(self, client):
        resp = _create_service(client, upstream_port=70000)
        assert resp.status_code == 422

    def test_invalid_scheme_rejected(self, client):
        resp = _create_service(client, upstream_scheme="ftp")
        assert resp.status_code == 422

    def test_missing_required_fields(self, client):
        resp = client.post("/api/services", json={"name": "Incomplete"})
        assert resp.status_code == 422

    def test_whitespace_only_name_rejected(self, client):
        # A blank-after-strip name must fail min_length, not create a nameless service.
        resp = _create_service(client, name="   ")
        assert resp.status_code == 422

    def test_surrounding_whitespace_in_name_trimmed(self, client):
        resp = _create_service(client, name="  Trimmed App  ")
        assert resp.status_code == 201
        assert resp.json()["name"] == "Trimmed App"

    def test_max_length_enforced_after_strip(self, client):
        # 132 raw chars stripping to exactly 128 must be accepted, proving max_length
        # is applied AFTER the strip (a pre-strip check would reject the raw length).
        resp = _create_service(client, name="  " + "x" * 128 + "  ")
        assert resp.status_code == 201
        assert resp.json()["name"] == "x" * 128

    def test_slug_generation(self, client):
        resp = _create_service(
            client,
            name="My Cool App 2",
            hostname="myapp.example.com",
        )
        data = resp.json()
        assert data["edge_container_name"] == "edge_my-cool-app-2"
        assert data["network_name"] == "edge_net_my-cool-app-2"
        assert data["ts_hostname"] == "edge-my-cool-app-2"

    def test_long_name_ts_hostname_within_dns_label_limit(self, client):
        # A name at the 128-char display cap must still derive a ts_hostname
        # within the 63-char DNS-label limit Tailscale enforces on
        # `tailscale up --hostname=`. The display cap on name stays at 128.
        long_name = "a" * 128
        resp = _create_service(
            client, name=long_name, hostname="longname.example.com"
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == long_name  # display cap unchanged
        assert len(data["ts_hostname"]) <= 63
        # All three derived names share the same capped slug.
        slug = data["ts_hostname"][len("edge-"):]
        assert data["edge_container_name"] == f"edge_{slug}"
        assert data["network_name"] == f"edge_net_{slug}"


class TestMultiExposure:
    """One container can have multiple exposures (different ports/hostnames)."""

    def test_same_container_different_ports(self, client):
        resp1 = _create_service(
            client, name="Nextcloud Web", hostname="nextcloud.example.com",
            upstream_port=80,
        )
        assert resp1.status_code == 201
        resp2 = _create_service(
            client, name="Nextcloud DAV", hostname="dav.example.com",
            upstream_port=443,
        )
        assert resp2.status_code == 201
        assert resp1.json()["upstream_container_id"] == resp2.json()["upstream_container_id"]

    def test_same_container_same_port_different_hostnames(self, client):
        resp1 = _create_service(client, name="App Primary", hostname="app.example.com")
        assert resp1.status_code == 201
        resp2 = _create_service(client, name="App Alias", hostname="alias.example.com")
        assert resp2.status_code == 201

    def test_edge_names_unique_across_exposures(self, client):
        resp1 = _create_service(client, name="Nextcloud", hostname="nc1.example.com")
        resp2 = _create_service(client, name="Nextcloud", hostname="nc2.example.com")
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        d1, d2 = resp1.json(), resp2.json()
        assert d1["edge_container_name"] != d2["edge_container_name"]
        assert d1["network_name"] != d2["network_name"]
        assert d1["ts_hostname"] != d2["ts_hostname"]

    def test_three_exposures_unique_slugs(self, client):
        r1 = _create_service(client, name="App", hostname="a1.example.com")
        r2 = _create_service(client, name="App", hostname="a2.example.com")
        r3 = _create_service(client, name="App", hostname="a3.example.com")
        assert r1.status_code == 201
        assert r2.status_code == 201
        assert r3.status_code == 201
        names = {r.json()["edge_container_name"] for r in [r1, r2, r3]}
        assert len(names) == 3

    def test_long_distinct_names_get_unique_capped_slugs(self, client):
        # Two distinct long names that share the same truncated prefix must get
        # distinct slugs, both within the DNS-label limit (collision suffixing
        # operates on the capped base).
        shared = "z" * 60  # slugifies past the base cap; both truncate identically
        r1 = _create_service(client, name=shared + " one", hostname="lone.example.com")
        r2 = _create_service(client, name=shared + " two", hostname="ltwo.example.com")
        assert r1.status_code == 201
        assert r2.status_code == 201
        d1, d2 = r1.json(), r2.json()
        assert d1["ts_hostname"] != d2["ts_hostname"]
        assert len(d1["ts_hostname"]) <= 63
        assert len(d2["ts_hostname"]) <= 63

    def test_list_shows_all_exposures(self, client):
        _create_service(client, name="NC Web", hostname="nc-web.example.com", upstream_port=80)
        _create_service(client, name="NC DAV", hostname="nc-dav.example.com", upstream_port=443)
        resp = client.get("/api/services")
        assert resp.json()["total"] == 2


class TestListServices:
    def test_empty_list(self, client):
        resp = client.get("/api/services")
        assert resp.status_code == 200
        data = resp.json()
        assert data["services"] == []
        assert data["total"] == 0

    def test_list_multiple(self, client):
        _create_service(client, name="App1", hostname="app1.example.com")
        _create_service(client, name="App2", hostname="app2.example.com")
        _create_service(client, name="App3", hostname="app3.example.com")
        resp = client.get("/api/services")
        data = resp.json()
        assert data["total"] == 3
        assert len(data["services"]) == 3

    def test_list_includes_status(self, client):
        _create_service(client)
        resp = client.get("/api/services")
        svc = resp.json()["services"][0]
        assert svc["status"]["phase"] == "pending"

    def test_list_returns_all(self, client):
        _create_service(client, name="First", hostname="first.example.com")
        _create_service(client, name="Second", hostname="second.example.com")
        resp = client.get("/api/services")
        names = {s["name"] for s in resp.json()["services"]}
        assert names == {"First", "Second"}

    def test_list_batches_status_cert_and_handles_missing(self, client, db_session, db_engine):
        id1 = _create_service(client, name="A", hostname="a.example.com").json()["id"]
        id2 = _create_service(client, name="B", hostname="b.example.com").json()["id"]
        id3 = _create_service(client, name="C", hostname="c.example.com").json()["id"]

        # id1 gains a cert; id2 loses its status row entirely.
        cert = Certificate(service_id=id1, hostname="a.example.com")
        cert.expires_at = datetime(2026, 10, 1)
        db_session.add(cert)
        db_session.delete(db_session.get(ServiceStatus, id2))
        db_session.commit()

        status_q: list[str] = []
        cert_q: list[str] = []

        def _track(conn, cursor, statement, parameters, context, executemany):
            low = statement.lower()
            if "from service_status" in low:
                status_q.append(statement)
            elif "from certificates" in low:
                cert_q.append(statement)

        sa_event.listen(db_engine, "before_cursor_execute", _track)
        try:
            resp = client.get("/api/services")
        finally:
            sa_event.remove(db_engine, "before_cursor_execute", _track)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3

        # Constant query count regardless of N (unbatched would be one per service).
        assert len(status_q) == 1
        assert len(cert_q) == 1

        by_id = {s["id"]: s for s in data["services"]}
        assert by_id[id1]["status"]["cert_expires_at"] == "2026-10-01T00:00:00"
        assert by_id[id2]["status"] is None
        assert by_id[id3]["status"] is not None
        assert by_id[id3]["status"]["cert_expires_at"] is None

        # Ordering matches a direct ordered query.
        expected_order = [
            s.id
            for s in db_session.query(Service)
            .order_by(Service.created_at.desc(), Service.id.desc())
            .all()
        ]
        assert [s["id"] for s in data["services"]] == expected_order


class TestGetService:
    def test_get_existing(self, client):
        create_resp = _create_service(client)
        svc_id = create_resp.json()["id"]
        resp = client.get(f"/api/services/{svc_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == svc_id
        assert resp.json()["name"] == "Nextcloud"

    def test_get_nonexistent(self, client):
        resp = client.get("/api/services/svc_nonexistent")
        assert resp.status_code == 404

    def test_get_includes_full_details(self, client):
        create_resp = _create_service(client, healthcheck_path="/status.php")
        svc_id = create_resp.json()["id"]
        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["healthcheck_path"] == "/status.php"
        assert data["status"] is not None
        assert "created_at" in data
        assert "updated_at" in data


class TestServiceEvents:
    def test_create_generates_event(self, client, db_session):
        _create_service(client)
        events = db_session.query(Event).filter(Event.kind == "service_created").all()
        assert len(events) == 1
        assert "Nextcloud" in events[0].message

    def test_update_generates_event(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        events = db_session.query(Event).filter(Event.kind == "service_updated").all()
        assert len(events) == 1
        details = events[0].details
        assert details["name"] == "Renamed"

    def test_update_event_preserves_level_message_and_details(self, client, db_session):
        # Regression: services._emit_event was deleted and its call sites now go
        # straight to the central emit_event. The representative service_updated
        # emit must keep its level (default "info"), message, and *dict* details
        # (serialized centrally) intact across the consolidation.
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        event = (
            db_session.query(Event).filter(Event.kind == "service_updated").one()
        )
        assert event.level == "info"
        assert event.message == "Service 'Renamed' updated"
        assert event.details == {"name": "Renamed"}

    def test_disable_generates_event(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        client.post(f"/api/services/{svc_id}/disable")
        events = db_session.query(Event).filter(Event.kind == "service_disabled").all()
        assert len(events) == 1

    def test_delete_generates_event(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        client.delete(f"/api/services/{svc_id}")
        events = db_session.query(Event).filter(Event.kind == "service_deleted").all()
        assert len(events) == 1
        assert events[0].service_id is None

    def test_noop_update_no_event(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={})
        events = db_session.query(Event).filter(Event.kind == "service_updated").all()
        assert len(events) == 0


class TestSnippetAuditEvents:
    """A custom_caddy_snippet is an admin-only Caddy-config injection / SSRF
    vector, so any set/change/clear must surface as a DISTINCT, high-visibility
    audit event (kind 'service_snippet_changed', level 'warning') in addition to
    the routine service_created/service_updated events. Pre-fix this kind does
    not exist, so the positive assertions below fail."""

    _SNIPPET = "header X-Frame-Options DENY"
    _SNIPPET2 = "header X-Content-Type-Options nosniff"

    def _snippet_events(self, db, service_id=None):
        q = db.query(Event).filter(Event.kind == "service_snippet_changed")
        if service_id is not None:
            q = q.filter(Event.service_id == service_id)
        return q.all()

    def _by_action(self, events):
        return {e.details["action"]: e for e in events}

    def test_create_with_snippet_emits_warning_event(self, client, db_session):
        _create_service(client, custom_caddy_snippet=self._SNIPPET)
        events = self._snippet_events(db_session)
        assert len(events) == 1
        ev = events[0]
        assert ev.level == "warning"
        assert "set" in ev.message
        assert "Nextcloud" in ev.message
        details = ev.details
        assert details["action"] == "set"
        assert details["new_len"] == len(self._SNIPPET)
        assert details["new_sha256"] == hashlib.sha256(self._SNIPPET.encode()).hexdigest()

    def test_create_without_snippet_no_event(self, client, db_session):
        _create_service(client)
        assert len(self._snippet_events(db_session)) == 0

    def test_update_set_snippet_emits_event(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"custom_caddy_snippet": self._SNIPPET})
        events = self._snippet_events(db_session, svc_id)
        assert len(events) == 1
        assert events[0].level == "warning"
        assert events[0].details["action"] == "set"

    def test_update_change_snippet_emits_changed(self, client, db_session):
        svc_id = _create_service(client, custom_caddy_snippet=self._SNIPPET).json()["id"]
        # The create emitted one 'set'; modifying must add a DISTINCT 'changed'.
        client.put(f"/api/services/{svc_id}", json={"custom_caddy_snippet": self._SNIPPET2})
        by_action = self._by_action(self._snippet_events(db_session, svc_id))
        assert set(by_action) == {"set", "changed"}
        changed = by_action["changed"]
        assert changed.level == "warning"
        details = changed.details
        assert details["new_len"] == len(self._SNIPPET2)
        assert details["new_sha256"] == hashlib.sha256(self._SNIPPET2.encode()).hexdigest()

    def test_update_clear_snippet_emits_cleared(self, client, db_session):
        svc_id = _create_service(client, custom_caddy_snippet=self._SNIPPET).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"custom_caddy_snippet": ""})
        by_action = self._by_action(self._snippet_events(db_session, svc_id))
        assert set(by_action) == {"set", "cleared"}
        details = by_action["cleared"].details
        assert details["new_len"] == 0
        assert details["new_sha256"] is None

    def test_update_other_field_no_snippet_event(self, client, db_session):
        svc_id = _create_service(client, custom_caddy_snippet=self._SNIPPET).json()["id"]
        # Only the create's 'set' should exist; renaming is not a snippet delta.
        client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert len(self._snippet_events(db_session, svc_id)) == 1

    def test_update_same_snippet_no_new_event(self, client, db_session):
        svc_id = _create_service(client, custom_caddy_snippet=self._SNIPPET).json()["id"]
        # Re-sending the identical snippet is not a delta -> no extra event.
        client.put(f"/api/services/{svc_id}", json={"custom_caddy_snippet": self._SNIPPET})
        assert len(self._snippet_events(db_session, svc_id)) == 1


class TestHostnameValidation:
    """Hostname domain validation on create (spec fix #8)."""

    def _create(self, client, hostname="app.example.com", **kw):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": hostname,
            "base_domain": "example.com",
            **kw,
        }
        return client.post("/api/services", json=body)

    def test_hostname_matching_domain_accepted(self, client):
        resp = self._create(client, hostname="myapp.example.com")
        assert resp.status_code == 201

    def test_hostname_wrong_domain_rejected(self, client):
        resp = self._create(client, hostname="myapp.wrongdomain.com")
        assert resp.status_code == 422
        assert "must end with" in resp.json()["detail"]

    def test_hostname_bare_domain_rejected(self, client):
        resp = self._create(client, hostname="example.com")
        assert resp.status_code == 422

    def test_subdomain_deep_nesting_accepted(self, client):
        resp = self._create(client, hostname="a.b.c.example.com")
        assert resp.status_code == 201

    def test_hostname_overlong_label_rejected(self, client):
        resp = self._create(client, hostname="a" * 64 + ".example.com")
        assert resp.status_code == 422

    def test_hostname_max_label_accepted(self, client):
        resp = self._create(client, hostname="a" * 63 + ".example.com")
        assert resp.status_code == 201


class TestBaseDomainAlwaysConfigured:
    """base_domain is always the configured domain, derived server-side."""

    def _create(self, client, hostname="app.example.com", base_domain=None):
        body = {
            "name": "App",
            "upstream_container_id": "abc123",
            "upstream_container_name": "app",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": hostname,
        }
        if base_domain is not None:
            body["base_domain"] = base_domain
        return client.post("/api/services", json=body)

    def test_base_domain_derived_from_configured_domain(self, client):
        resp = self._create(client, hostname="app.example.com")
        assert resp.status_code == 201
        assert resp.json()["base_domain"] == "example.com"

    def test_client_supplied_base_domain_is_ignored(self, client):
        # Even a divergent client base_domain cannot override the configured one.
        resp = self._create(client, hostname="a.b.example.com", base_domain="b.example.com")
        assert resp.status_code == 201
        assert resp.json()["base_domain"] == "example.com"

    def test_hostname_outside_configured_domain_rejected(self, client):
        resp = self._create(client, hostname="app.wrong.com")
        assert resp.status_code == 422
        assert "must end with" in resp.json()["detail"]

    def test_deep_hostname_under_configured_domain_accepted(self, client):
        resp = self._create(client, hostname="a.b.example.com")
        assert resp.status_code == 201
        assert resp.json()["base_domain"] == "example.com"

    def test_hostname_equal_to_configured_domain_rejected(self, client):
        resp = self._create(client, hostname="example.com")
        assert resp.status_code == 422


class TestUpstreamContainerValidation:
    """create_service should reject requests when upstream container doesn't exist."""

    def test_missing_container_returns_422(self, client):
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=__import__("fastapi").HTTPException(status_code=422, detail="not found"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 422

    def test_docker_unreachable_returns_503(self, client):
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=__import__("fastapi").HTTPException(status_code=503, detail="cannot connect"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 503

    def test_valid_container_succeeds(self, client):
        resp = _create_service(client, name="App", hostname="app.example.com")
        assert resp.status_code == 201

    def test_create_service_calls_validate_upstream(self, client):
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 201
            mock_val.assert_called_once()
            args = mock_val.call_args
            assert args[0][1] == "abc123def456"
            assert args[0][2] == 80

    def test_validate_upstream_not_found_via_api(self, client):
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=422, detail="Upstream container 'x' not found"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 422
            assert "not found" in resp.json()["detail"].lower()

    def test_validate_upstream_docker_unreachable_via_api(self, client):
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=503, detail="Cannot connect to Docker"),
        ):
            resp = _create_service(client, name="App", hostname="app.example.com")
            assert resp.status_code == 503


    def test_validate_upstream_docker_unreachable_detail_is_generic(self, db_session, caplog):
        """_validate_upstream's 503 must NOT leak the socket path / DOCKER_HOST in
        str(exc); the real error is logged server-side instead."""

        secret = "unix:///run/secret-docker.sock connection refused"
        # The default docker_socket_path setting is truthy, so the DockerClient
        # constructor (not from_env) is exercised; make it raise a docker error
        # whose str() embeds a socket-path-like secret. _real_validate_upstream is
        # captured at import time to bypass the autouse _mock_upstream_validation.
        with (
            patch("docker.DockerClient", side_effect=docker.errors.DockerException(secret)),
            caplog.at_level(logging.ERROR, logger="app.routers.services"),
            pytest.raises(DockerUnavailable) as exc_info,
        ):
            _real_validate_upstream(db_session, "abc123", 80)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "Cannot connect to Docker to validate upstream container"
        assert secret not in exc_info.value.detail
        assert secret in caplog.text

    def test_validate_upstream_not_found_real_branch(self, db_session):
        """The real _validate_upstream maps a docker NotFound raised by
        containers.get to a 422 (container missing) — distinct from the
        daemon-down 503 path. Exercises the actual NotFound arm rather than a
        fully-patched _validate_upstream."""

        fake_client = MagicMock()
        fake_client.containers.get.side_effect = docker.errors.NotFound("no such container")

        @contextlib.contextmanager
        def fake_docker_client(_socket):
            yield fake_client

        with (
            patch("app.routers.services.docker_client", fake_docker_client),
            pytest.raises(HTTPException) as exc_info,
        ):
            _real_validate_upstream(db_session, "missing_ctr", 80)

        assert exc_info.value.status_code == 422
        assert "missing_ctr" in exc_info.value.detail


class TestUpstreamPortValidation:
    """_validate_upstream_port should check exposed ports on the container."""

    def test_port_in_exposed_ports_passes(self):
        container = _make_container(exposed_ports={"80/tcp": {}, "443/tcp": {}})
        _validate_upstream_port(container, 80)

    def test_port_not_in_exposed_ports_raises(self):
        container = _make_container(exposed_ports={"80/tcp": {}, "443/tcp": {}})
        with pytest.raises(HTTPException) as exc_info:
            _validate_upstream_port(container, 8080)
        assert exc_info.value.status_code == 422
        assert "8080" in exc_info.value.detail
        assert "80" in exc_info.value.detail

    def test_port_in_host_bindings_passes(self):
        container = _make_container(port_bindings={"3000/tcp": [{"HostPort": "3000"}]})
        _validate_upstream_port(container, 3000)

    def test_no_exposed_ports_allows_any(self):
        container = _make_container()
        _validate_upstream_port(container, 9999)

    def test_merged_exposed_and_bindings(self):
        container = _make_container(
            exposed_ports={"80/tcp": {}},
            port_bindings={"8080/tcp": [{"HostPort": "8080"}]},
        )
        _validate_upstream_port(container, 80)
        _validate_upstream_port(container, 8080)

    def test_rejects_port_when_others_exist(self):
        container = _make_container(
            exposed_ports={"80/tcp": {}},
            port_bindings={"8080/tcp": [{"HostPort": "8080"}]},
        )
        with pytest.raises(HTTPException) as exc_info:
            _validate_upstream_port(container, 3000)
        assert exc_info.value.status_code == 422


class TestCreateValidationHoistedOutOfLock:
    """The upstream container/port validation does a Docker round-trip. It must
    run BEFORE create_service takes ``_SERVICE_LIFECYCLE_MUTEX``, so a
    slow/unreachable Docker can't stall every other lifecycle op. The router
    validates ahead of the service-layer call that acquires the mutex; this is
    the create-side mirror of TestUpdatePortValidationHoistedOutOfLock (a
    regression that folded validation into the locked service layer would
    reintroduce the stall)."""

    def test_validation_runs_before_lifecycle_lock(self, client):
        observed: dict = {}

        def fake_validate(db, container_id, port):
            # Probe the lifecycle mutex from a DIFFERENT thread: an RLock is
            # reentrant for the holder, so only a separate thread reveals whether
            # the request handler is currently holding it. If validation ran under
            # the mutex this non-blocking acquire would fail.
            result: dict = {}

            def probe():
                acquired = _SERVICE_LIFECYCLE_MUTEX.acquire(blocking=False)
                result["acquired"] = acquired
                if acquired:
                    _SERVICE_LIFECYCLE_MUTEX.release()

            t = threading.Thread(target=probe)
            t.start()
            t.join()
            observed.update(result)

        with patch("app.routers.services._validate_upstream", side_effect=fake_validate):
            resp = _create_service(client, name="App", hostname="app.example.com")

        assert resp.status_code == 201
        assert observed.get("acquired") is True, (
            "lifecycle mutex was held during upstream validation on create; "
            "validation must be hoisted out of the lock"
        )
