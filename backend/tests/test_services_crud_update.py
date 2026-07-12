"""Service CRUD API tests: update (name/hostname/port) and update-time validation, plus snippet-redacting update events.

Covers update behavior after the app.services facade split from test_services_crud.py."""

import hashlib
import threading
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.locks import _SERVICE_LIFECYCLE_MUTEX
from app.models.dns_record import DnsRecord
from app.models.event import Event
from app.settings_store import set_setting
from tests._services_helpers import _create_service


class TestUpdateService:
    def test_update_name(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_port(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 8080})
        assert resp.status_code == 200
        assert resp.json()["upstream_port"] == 8080

    def test_update_scheme(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"upstream_scheme": "https"})
        assert resp.status_code == 200
        assert resp.json()["upstream_scheme"] == "https"

    def test_update_hostname(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"

    def test_update_hostname_conflict(self, client):
        _create_service(client, name="App1", hostname="app1.example.com")
        svc_id = _create_service(client, name="App2", hostname="app2.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app1.example.com"})
        assert resp.status_code == 409

    def test_update_hostname_same_is_ok(self, client):
        svc_id = _create_service(client, hostname="same.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "same.example.com"})
        assert resp.status_code == 200

    @patch("app.edge.container_manager.stop_edge")
    def test_update_enabled(self, mock_stop, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"enabled": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["status"]["phase"] == "disabled"
        assert data["status"]["message"] == "Service disabled by user"
        mock_stop.assert_called_once()

    def test_update_enable_marks_pending_and_schedules_reconcile(self, client):
        svc_id = _create_service(client, enabled=False).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"enabled": True})

        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["status"]["phase"] == "pending"
        assert data["status"]["message"] == "Awaiting reconciliation after enable"
        mock_reconcile.assert_called_once()

    def test_update_hostname_schedules_reconcile(self, client):
        """A hostname change on an enabled service must schedule an immediate
        reconcile. The change tears down the old hostname's DNS record and cert
        directory, so the new hostname needs a fresh DNS record + cert without
        waiting for the periodic loop."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"
        mock_reconcile.assert_called_once()

    def test_update_hostname_disabled_no_reconcile(self, client):
        """A disabled service stays offline: a hostname change must NOT schedule a
        reconcile (that would bring the service back online)."""
        svc_id = _create_service(
            client, name="App", hostname="app.example.com", enabled=False
        ).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        mock_reconcile.assert_not_called()

    def test_update_port_schedules_reconcile(self, client):
        """A config-affecting field change (upstream_port) on an enabled service
        must schedule an immediate reconcile so the re-rendered Caddyfile /
        reverse-proxy change applies in seconds, not after the periodic loop."""
        svc_id = _create_service(client).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 8080})
        assert resp.status_code == 200
        mock_reconcile.assert_called_once()

    def test_update_name_only_does_not_schedule_reconcile(self, client):
        """A non-config field change (name) on an enabled service must NOT schedule
        a reconcile — it doesn't alter the rendered Caddyfile, so don't over-trigger."""
        svc_id = _create_service(client).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        mock_reconcile.assert_not_called()

    def test_update_config_on_disabled_does_not_schedule_reconcile(self, client):
        """A config-affecting change on a DISABLED service must NOT schedule a
        reconcile — there is nothing to bring up."""
        svc_id = _create_service(client, enabled=False).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 8080})
        assert resp.status_code == 200
        mock_reconcile.assert_not_called()

    def test_update_config_to_same_value_does_not_schedule_reconcile(self, client):
        """A config field re-sent with its CURRENT value is not a real change, so
        it must NOT schedule a reconcile. Exercises the value-comparison arm of
        config_changed (``changes[field] != getattr(svc, field)``): dropping it
        would over-trigger a reconcile on every no-op edit."""
        svc_id = _create_service(client, upstream_port=80).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 80})
        assert resp.status_code == 200
        assert resp.json()["upstream_port"] == 80
        mock_reconcile.assert_not_called()

    def test_update_advanced_fields(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={
            "preserve_host_header": False,
            "custom_caddy_snippet": "log { output stdout }",
            "app_profile": "nextcloud",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["preserve_host_header"] is False
        assert data["custom_caddy_snippet"] == "log { output stdout }"
        assert data["app_profile"] == "nextcloud"

    def test_update_additional_networks(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={
            "additional_networks": [
                {"name": "opencloud_opencloud-net", "aliases": ["cloud.example.com"]},
            ],
        })
        assert resp.status_code == 200
        assert resp.json()["additional_networks"] == [
            {"name": "opencloud_opencloud-net", "aliases": ["cloud.example.com"]},
        ]

    def test_update_additional_networks_schedules_reconcile(self, client):
        svc_id = _create_service(client).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={
                "additional_networks": [
                    {"name": "opencloud_opencloud-net", "aliases": ["cloud.example.com"]},
                ],
            })
        assert resp.status_code == 200
        mock_reconcile.assert_called_once()

    def test_update_additional_networks_same_value_no_reconcile(self, client):
        additional_networks = [
            {"name": "opencloud_opencloud-net", "aliases": ["cloud.example.com"]},
        ]
        svc_id = _create_service(client, additional_networks=additional_networks).json()["id"]
        with patch("app.reconciler.reconcile_loop.reconcile_one") as mock_reconcile:
            resp = client.put(f"/api/services/{svc_id}", json={
                "additional_networks": additional_networks,
            })
        assert resp.status_code == 200
        mock_reconcile.assert_not_called()

    def test_update_nonexistent(self, client):
        resp = client.put("/api/services/svc_nonexistent", json={"name": "X"})
        assert resp.status_code == 404

    def test_partial_update_preserves_fields(self, client):
        svc_id = _create_service(client).json()["id"]
        client.put(f"/api/services/{svc_id}", json={"name": "Updated"})
        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["name"] == "Updated"
        assert data["upstream_port"] == 80
        assert data["hostname"] == "nextcloud.example.com"

    @pytest.mark.parametrize(
        "field",
        [
            "name",
            "upstream_scheme",
            "upstream_port",
            "hostname",
            "enabled",
            "preserve_host_header",
        ],
    )
    def test_update_rejects_null_for_non_nullable_fields(self, client, field):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={field: None})
        assert resp.status_code == 422


class TestUpdateHostnameValidation:
    """Hostname domain validation on update."""

    def test_update_hostname_valid_domain(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"

    def test_update_hostname_wrong_domain_rejected(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.wrong.com"})
        assert resp.status_code == 422
        assert "must end with" in resp.json()["detail"]

    def test_update_hostname_same_value_ok(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.example.com"})
        assert resp.status_code == 200

    def test_update_hostname_conflict_still_caught(self, client):
        _create_service(client, hostname="taken.example.com", name="First")
        svc_id = _create_service(client, hostname="other.example.com", name="Second").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "taken.example.com"})
        assert resp.status_code == 409

    def test_update_non_hostname_fields_unaffected(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_hostname_deep_subdomain_accepted(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "a.b.c.example.com"})
        assert resp.status_code == 200


class TestUpdateUpstreamPortValidation:
    """update_service should revalidate upstream port when it changes."""

    def test_update_port_revalidates(self, client):
        """Changing upstream_port should trigger _validate_upstream."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 9090})
            assert resp.status_code == 200
            mock_val.assert_called_once()
            args = mock_val.call_args[0]
            assert args[2] == 9090  # new port

    def test_update_port_invalid_rejected(self, client):
        """If the new port isn't exposed by the container, update should 422."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=422, detail="Port 9999 is not exposed"),
        ):
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 9999})
            assert resp.status_code == 422
            assert "9999" in resp.json()["detail"]

    def test_update_same_port_no_revalidation(self, client):
        """Setting port to the same value should not trigger revalidation."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 80})
            assert resp.status_code == 200
            mock_val.assert_not_called()

    def test_update_non_port_fields_no_revalidation(self, client):
        """Changing non-port fields should not trigger upstream revalidation."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch("app.routers.services._validate_upstream") as mock_val:
            resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
            assert resp.status_code == 200
            mock_val.assert_not_called()

    def test_update_docker_unreachable_503(self, client):
        """If Docker is unreachable during port revalidation, return 503."""
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=503, detail="Cannot connect to Docker"),
        ):
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 443})
            assert resp.status_code == 503

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_invalid_port_does_not_tear_down_hostname(
        self, mock_secret, mock_cleanup, client, db_session
    ):
        """A PUT that changes both hostname and an INVALID port must reject before
        any destructive hostname teardown: the old DNS record and cert dir must be
        left intact, and the hostname must not change."""

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        db_session.add(DnsRecord(service_id=svc_id, hostname="app.example.com", record_id="cf_rec"))
        db_session.commit()

        with patch(
            "app.routers.services._validate_upstream",
            side_effect=HTTPException(status_code=422, detail="Port 9999 is not exposed"),
        ):
            resp = client.put(
                f"/api/services/{svc_id}",
                json={"hostname": "new.example.com", "upstream_port": 9999},
            )
        assert resp.status_code == 422

        # Destructive teardown must NOT have run: DNS cleanup was never invoked,
        # the DnsRecord row survives, and the hostname is unchanged.
        mock_cleanup.assert_not_called()
        db_session.expire_all()
        assert client.get(f"/api/services/{svc_id}").json()["hostname"] == "app.example.com"
        assert db_session.get(DnsRecord, svc_id) is not None


class TestUpdateNameValidation:
    """update_service should enforce the same name constraints as create."""

    def test_update_empty_name_rejected(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": ""})
        assert resp.status_code == 422

    def test_update_overlong_name_rejected(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "x" * 129})
        assert resp.status_code == 422

    def test_update_valid_name_accepted(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "x" * 128})
        assert resp.status_code == 200
        assert resp.json()["name"] == "x" * 128

    def test_update_whitespace_only_name_rejected(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "   "})
        assert resp.status_code == 422

    def test_update_trims_surrounding_whitespace(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "  Renamed  "})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_max_length_enforced_after_strip(self, client):
        svc_id = _create_service(client).json()["id"]
        # Raw length 132 strips to exactly 128 — accepted only if max_length is post-strip.
        resp = client.put(f"/api/services/{svc_id}", json={"name": "  " + "x" * 128 + "  "})
        assert resp.status_code == 200
        assert resp.json()["name"] == "x" * 128


class TestUpdateHostnameBaseDomain:
    """Changing the hostname must keep base_domain a suffix of the hostname."""

    def test_hostname_change_recomputes_base_domain(self, client):
        svc_id = _create_service(
            client, name="App", hostname="x.a.example.com", base_domain="a.example.com"
        ).json()["id"]

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "b.example.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["hostname"] == "b.example.com"
        # base_domain must remain a suffix of the new hostname (create invariant).
        assert data["hostname"].endswith(f".{data['base_domain']}")
        assert data["base_domain"] == "example.com"

    def test_hostname_change_mixed_case_base_domain_accepted(self, client, db_session):
        # A legacy/direct-DB base_domain can hold a mixed-case value (the API
        # validator only lowercases on write); the hostname is lowercased by the
        # schema. The hostname-change suffix check MUST compare case-insensitively
        # (mirroring the create path), or a valid subdomain change is wrongly
        # rejected with 422 while create accepts the same hostname.
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        set_setting(db_session, "base_domain", "Example.COM")
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"


class TestUpdatePortValidationHoistedOutOfLock:
    """The upstream-port revalidation does a Docker round-trip. It must run
    BEFORE update_service takes _SERVICE_LIFECYCLE_MUTEX + the per-service
    reconcile lock, so a slow/unreachable Docker can't stall every other
    lifecycle op. Pre-fix the validation ran while holding both locks."""

    def test_validation_runs_before_lifecycle_lock(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        observed: dict = {}

        def fake_validate(db, container_id, port):
            # Probe the lifecycle mutex from a DIFFERENT thread: an RLock is
            # reentrant for the holder, so only a separate thread reveals whether
            # the request handler is currently holding it. Pre-fix (validation
            # under the lock) this non-blocking acquire fails; post-fix the lock
            # is still free because validation is hoisted ahead of it.
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
            resp = client.put(f"/api/services/{svc_id}", json={"upstream_port": 9090})

        assert resp.status_code == 200
        assert observed.get("acquired") is True, (
            "lifecycle mutex was held during upstream-port validation; "
            "validation must be hoisted out of the lock"
        )


class TestUpdateEventRedactsSnippet:
    """The dedicated service_snippet_changed audit event records the snippet
    delta as sha256+len precisely so the raw admin-injected snippet (a Caddy-
    config / SSRF tamper vector) never lands in the event log. The generic
    service_updated event must not undo that by persisting the raw snippet in
    its details."""

    def test_service_updated_details_redact_raw_snippet(self, client, db_session):
        snippet = "header X-Frame-Options DENY"
        svc_id = _create_service(client).json()["id"]
        resp = client.put(
            f"/api/services/{svc_id}",
            json={"name": "Renamed", "custom_caddy_snippet": snippet},
        )
        assert resp.status_code == 200

        updated = (
            db_session.query(Event)
            .filter(Event.kind == "service_updated", Event.service_id == svc_id)
            .one()
        )
        details = updated.details
        # The non-sensitive field is still recorded verbatim...
        assert details["name"] == "Renamed"
        # ...but the raw snippet must NOT be persisted in the generic event.
        assert details["custom_caddy_snippet"] == "<redacted: see service_snippet_changed>"
        assert snippet not in str(details)

        # The dedicated event still carries the sha256 audit trail.
        snippet_evt = (
            db_session.query(Event)
            .filter(Event.kind == "service_snippet_changed", Event.service_id == svc_id)
            .one()
        )
        assert snippet_evt.details["new_sha256"] == hashlib.sha256(snippet.encode()).hexdigest()
