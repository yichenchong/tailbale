"""Cross-cutting service-router plumbing tests: action-endpoint 404s, status/response shape, cert-log listing, unused-import guards, generic error-detail mapping, and full-health-check socket resolution.

Feature-specific tests live in test_services_crud_{create,update,lifecycle}.py / test_services_edge_ops.py / test_services_cert_ops.py."""

import ast
import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import docker
import requests
from sqlalchemy import text

from app.models.certificate import Certificate
from app.models.event import Event
from app.models.service_status import ServiceStatus
from app.settings_store import set_setting
from tests._services_helpers import (
    _create_service,
    _create_service_in_db,
)


class TestStubActionEndpoints:
    def test_stub_404_for_nonexistent_service(self, client):
        resp = client.post("/api/services/svc_nonexistent/reload")
        assert resp.status_code == 404
        resp = client.post("/api/services/svc_nonexistent/restart-edge")
        assert resp.status_code == 404
        resp = client.post("/api/services/svc_nonexistent/reconcile")
        assert resp.status_code == 404
        resp = client.get("/api/services/svc_nonexistent/logs/edge")
        assert resp.status_code == 404


class TestStatusResponseFields:
    def test_status_includes_health_checks_and_cert(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        status = db_session.get(ServiceStatus, svc_id)
        status.health_checks = {"edge_container_running": True, "cert_present": False}
        db_session.commit()

        cert = Certificate(service_id=svc_id, hostname="nextcloud.example.com")
        cert.expires_at = datetime(2026, 8, 1)
        db_session.add(cert)
        db_session.commit()

        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["status"]["health_checks"] == {"edge_container_running": True, "cert_present": False}
        assert data["status"]["cert_expires_at"] == "2026-08-01T00:00:00"

    def test_status_without_cert_or_health_checks(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["status"]["health_checks"] is None
        assert data["status"]["cert_expires_at"] is None

    def test_list_includes_cert_expiry(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        cert = Certificate(service_id=svc_id, hostname="nextcloud.example.com")
        cert.expires_at = datetime(2026, 7, 15)
        db_session.add(cert)
        db_session.commit()

        resp = client.get("/api/services")
        svc = resp.json()["services"][0]
        assert svc["status"]["cert_expires_at"] == "2026-07-15T00:00:00"

    def test_update_response_includes_cert_expiry(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        cert = Certificate(service_id=svc_id, hostname="nextcloud.example.com")
        cert.expires_at = datetime(2026, 9, 1)
        db_session.add(cert)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["status"]["cert_expires_at"] == "2026-09-01T00:00:00"

    def test_disable_response_includes_cert_expiry(self, client, db_session):
        svc_id = _create_service(client).json()["id"]
        cert = Certificate(service_id=svc_id, hostname="nextcloud.example.com")
        cert.expires_at = datetime(2026, 9, 2)
        db_session.add(cert)
        db_session.commit()

        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        assert resp.json()["status"]["cert_expires_at"] == "2026-09-02T00:00:00"


class TestCertLogs:
    """The cert-log endpoint must tolerate malformed event details (mirroring the
    events-list hardening) instead of 500-ing the whole response."""

    def test_malformed_cert_event_details_do_not_break_listing(self, client, db_session):
        svc = _create_service_in_db(db_session)
        bad = Event(
            service_id=svc.id, kind="cert_failed", level="error",
            message="Bad details", details={"placeholder": True},
        )
        db_session.add(bad)
        db_session.add(Event(
            service_id=svc.id, kind="cert_issued", level="info",
            message="Good details", details={"hostname": svc.hostname},
        ))
        db_session.commit()
        # Simulate a corrupt legacy row by writing raw non-JSON text directly,
        # bypassing the column's bind-param encoder.
        db_session.execute(
            text("UPDATE events SET details = :d WHERE id = :id"),
            {"d": "{not json", "id": bad.id},
        )
        db_session.commit()

        resp = client.get(f"/api/services/{svc.id}/logs/cert")
        assert resp.status_code == 200
        events = resp.json()["events"]
        assert len(events) == 2
        by_kind = {e["kind"]: e for e in events}
        assert by_kind["cert_failed"]["details"] is None
        assert by_kind["cert_issued"]["details"] == {"hostname": svc.hostname}


class TestUnusedImportCleanup:
    """Verify stale imports have been removed from endpoints."""

    @staticmethod
    def _endpoint_node(name):
        source = Path(__file__).parent.parent / "app" / "routers" / "service_actions.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # Match sync OR async defs: an endpoint switching between the two
            # (full_health_check was async, is now a plain def) must not silently
            # turn this guard into a vacuous pass.
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"endpoint {name!r} not found in routers/service_actions.py")

    @staticmethod
    def _assert_no_config_settings_import(node, fname):
        for child in ast.walk(node):
            if isinstance(child, ast.ImportFrom) and child.module and "config" in child.module:
                names = [alias.name for alias in child.names]
                assert "settings" not in names, f"app_settings is imported but unused in {fname}"

    def test_health_check_full_no_unused_imports(self):
        self._assert_no_config_settings_import(
            self._endpoint_node("full_health_check"), "full_health_check"
        )

    def test_update_edge_no_unused_imports(self):
        self._assert_no_config_settings_import(
            self._endpoint_node("update_edge_endpoint"), "update_edge_endpoint"
        )


class TestActionEndpointErrorDetailGeneric:
    """A 500 from an action endpoint must NOT leak str(exc) to the client, but
    MUST record the full exception (with traceback) server-side."""

    def test_renew_cert_500_is_generic_and_logged(self, client, caplog):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        secret = "SENSITIVE /var/secret/key.pem boom"
        with patch(
            "app.certs.renewal_task.process_service_cert",
            side_effect=RuntimeError(secret),
        ), patch("app.secrets.read_secret", return_value="cf-token"), caplog.at_level(
            logging.ERROR, logger="app.routers.service_actions"
        ):
            resp = client.post(f"/api/services/{svc_id}/renew-cert")

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Failed to renew certificate"
        assert secret not in resp.json()["detail"]
        assert secret in caplog.text

    @patch("app.edge.caddy_admin.reload_caddy")
    def test_reload_500_is_generic_and_logged(self, mock_reload, client, caplog):
        secret = "SENSITIVE docker.sock denied at /run/x"
        mock_reload.side_effect = RuntimeError(secret)
        svc_id = _create_service(client, name="App2", hostname="app2.example.com").json()["id"]

        with caplog.at_level(logging.ERROR, logger="app.routers.service_actions"):
            resp = client.post(f"/api/services/{svc_id}/reload")

        assert resp.status_code == 500
        assert resp.json()["detail"] == "Failed to reload Caddy config"
        assert secret not in resp.json()["detail"]
        assert secret in caplog.text

    @patch("app.edge.caddy_admin.reload_caddy")
    def test_reload_docker_unreachable_503_is_generic_and_logged(self, mock_reload, client, caplog):
        secret = "unix:///run/leaky-docker.sock connection refused"
        mock_reload.side_effect = docker.errors.DockerException(secret)
        svc_id = _create_service(client, name="App2", hostname="app2.example.com").json()["id"]

        with caplog.at_level(logging.ERROR, logger="app.routers.service_actions"):
            resp = client.post(f"/api/services/{svc_id}/reload")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Docker is unavailable"
        assert secret not in resp.json()["detail"]
        assert secret in caplog.text

    @patch("app.edge.container_manager.restart_edge")
    def test_restart_edge_docker_unreachable_503_is_generic_and_logged(self, mock_restart, client, caplog):
        secret = "Connection refused to unix:///run/leaky-docker.sock"
        mock_restart.side_effect = requests.exceptions.ConnectionError(secret)
        svc_id = _create_service(client, name="App3", hostname="app3.example.com").json()["id"]

        with caplog.at_level(logging.ERROR, logger="app.routers.service_actions"):
            resp = client.post(f"/api/services/{svc_id}/restart-edge")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Docker is unavailable"
        assert secret not in resp.json()["detail"]
        assert secret in caplog.text

    @patch("app.secrets.read_secret", return_value="ts-key")
    @patch("app.edge.container_manager.recreate_edge")
    def test_recreate_edge_docker_unreachable_503_is_generic_and_logged(
        self, mock_recreate, mock_secret, client, caplog,
    ):
        secret = "DOCKER_HOST tcp://10.0.0.5:2375 unreachable"
        mock_recreate.side_effect = docker.errors.DockerException(secret)
        svc_id = _create_service(client, name="App4", hostname="app4.example.com").json()["id"]

        with caplog.at_level(logging.ERROR, logger="app.routers.service_actions"):
            resp = client.post(f"/api/services/{svc_id}/recreate-edge")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Docker is unavailable"
        assert secret not in resp.json()["detail"]
        assert secret in caplog.text


class TestHealthCheckFullDockerSocket:
    """full-health-check must resolve the Docker socket the same way the reconciler
    and probe-retry do (resolve_socket: the configured path, or None to honor
    DOCKER_HOST via from_env). Hard-defaulting to the unix socket instead would
    silently probe a DIFFERENT daemon than the rest of the app whenever
    docker_socket_path is cleared to use DOCKER_HOST."""

    def test_full_health_check_uses_canonical_docker_socket(self, client):
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        sentinel = "tcp://canonical-daemon:2375"
        with (
            patch("app.services.edge_ops.resolve_socket", return_value=sentinel),
            patch(
                "app.health.health_checker.run_health_checks", return_value={"ok": True}
            ) as mock_rhc,
        ):
            resp = client.post(f"/api/services/{svc_id}/health-check-full")
        assert resp.status_code == 200
        mock_rhc.assert_called_once()
        # socket_path is the 5th positional arg to run_health_checks.
        assert mock_rhc.call_args.args[4] == sentinel

    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.health.health_checker.run_health_checks", return_value={})
    def test_full_health_check_cf_error_is_generic_and_logged(
        self, mock_rhc, mock_secret, mock_find, client, db_session, caplog,
    ):
        """cf_error must not leak the Cloudflare request URL (embeds cf_zone_id)
        into the 200 body; the real error is logged server-side."""

        set_setting(db_session, "cf_zone_id", "zone-abc123")
        db_session.commit()
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        secret = "GET https://api.cloudflare.com/client/v4/zones/zone-abc123/dns_records failed"
        mock_find.side_effect = RuntimeError(secret)

        with caplog.at_level(logging.ERROR, logger="app.health.health_checker"):
            resp = client.post(f"/api/services/{svc_id}/health-check-full")

        assert resp.status_code == 200
        cf_error = resp.json()["extended"]["cf_error"]
        assert "zone-abc123" not in cf_error
        assert secret not in cf_error
        assert cf_error == "Cloudflare verification failed (RuntimeError)"
        assert secret in caplog.text

    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.health.health_checker.run_health_checks", return_value={})
    def test_full_health_check_null_ip_does_not_false_match(
        self, mock_rhc, mock_secret, mock_find, client, db_session,
    ):
        """SC3: with no tailscale_ip yet (current_ip is None) and a live CF record
        whose content is also None, cf_ip_matches_tailscale must be False — not a
        None == None false positive. Mirrors the current_ip guard dns_matches_ip
        already uses on the sibling line."""

        set_setting(db_session, "cf_zone_id", "zone-abc123")
        db_session.commit()
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]
        # A live record exists but reports no content -> .get("content") is None.
        mock_find.return_value = {"id": "rec1"}

        resp = client.post(f"/api/services/{svc_id}/health-check-full")

        assert resp.status_code == 200
        body = resp.json()
        # Precondition: no tailscale IP detected yet, so current_ip is None.
        assert body["tailscale_ip"] is None
        extended = body["extended"]
        assert extended["cf_record_exists"] is True
        assert extended["cf_record_ip"] is None
        assert extended["cf_ip_matches_tailscale"] is False

    def test_full_health_check_nonexistent_service_returns_404(self, client):
        # Guards existence (get_service_for_edge_query) before any health work, so
        # a missing service is a clean 404 rather than a 500 from probing a
        # None service or a misleading empty 200 checks payload.
        resp = client.post("/api/services/svc_nonexistent/health-check-full")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Service not found"


class TestFullHealthCheckLiveTailscaleIp:
    """The manual full-health-check must verify live Cloudflare DNS against the
    service's LIVE Tailscale IP, not the persisted ServiceStatus.tailscale_ip.

    A tailnet IP change lags in ServiceStatus until the next reconcile, so
    comparing the live Cloudflare A record against the STORED IP produced a false
    dns_matches_ip / cf_ip_matches_tailscale. edge_ops now sources the live IP via
    health_checker.get_live_tailscale_ip (the same connect -> _check_edge ->
    _check_tailscale path run_health_checks' live_dns predicate uses), falling
    back to the stored IP only when the edge/Docker is unreachable."""

    @patch("app.health.health_checker.get_live_tailscale_ip", return_value="100.64.0.2")
    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.health.health_checker.run_health_checks", return_value={})
    def test_matches_against_live_ip_not_stored(
        self, mock_rhc, mock_secret, mock_find, mock_live_ip, client, db_session,
    ):
        set_setting(db_session, "cf_zone_id", "zone-abc123")
        db_session.commit()
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        # Stored IP is STALE (last reconcile); the tailnet reassigned the edge a
        # new IP, and the live CF A record already tracks that new IP.
        status = db_session.get(ServiceStatus, svc_id)
        status.tailscale_ip = "100.64.0.1"
        db_session.commit()
        mock_find.return_value = {"id": "rec1", "content": "100.64.0.2"}

        resp = client.post(f"/api/services/{svc_id}/health-check-full")
        assert resp.status_code == 200
        body = resp.json()
        # Pre-fix: compared the live CF record against the stored 100.64.0.1 and
        # reported a mismatch. Post-fix: compares against the live 100.64.0.2.
        assert body["tailscale_ip"] == "100.64.0.2"
        assert body["checks"]["dns_matches_ip"] is True
        assert body["extended"]["cf_record_ip"] == "100.64.0.2"
        assert body["extended"]["cf_ip_matches_tailscale"] is True

    @patch("app.health.health_checker.get_live_tailscale_ip", return_value=None)
    @patch("app.adapters.cloudflare_adapter.find_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    @patch("app.health.health_checker.run_health_checks", return_value={})
    def test_falls_back_to_stored_ip_when_edge_unreachable(
        self, mock_rhc, mock_secret, mock_find, mock_live_ip, client, db_session,
    ):
        set_setting(db_session, "cf_zone_id", "zone-abc123")
        db_session.commit()
        svc_id = _create_service(client, name="App", hostname="app.example.com").json()["id"]

        # Edge/Docker unreachable -> no live IP; the stored IP is the best-known
        # value, so the check still runs against it rather than None.
        status = db_session.get(ServiceStatus, svc_id)
        status.tailscale_ip = "100.64.0.1"
        db_session.commit()
        mock_find.return_value = {"id": "rec1", "content": "100.64.0.1"}

        resp = client.post(f"/api/services/{svc_id}/health-check-full")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tailscale_ip"] == "100.64.0.1"
        assert body["checks"]["dns_matches_ip"] is True
        assert body["extended"]["cf_ip_matches_tailscale"] is True
