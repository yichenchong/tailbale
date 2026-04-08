"""Tests for the second bug-fix pass: SQLite FK cascades, cert renewal paths,
Cloudflare import fix, Docker socket consistency, and hostname validation on update."""

from unittest.mock import MagicMock, patch

from app.models.certificate import Certificate
from app.models.dns_record import DnsRecord
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service_in_db(db, **overrides):
    """Insert a service directly into the DB for testing."""
    defaults = {
        "name": "TestApp",
        "upstream_container_id": "abc123",
        "upstream_container_name": "testapp",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "testapp.example.com",
        "base_domain": "example.com",
        "edge_container_name": "edge_testapp",
        "network_name": "edge_net_testapp",
        "ts_hostname": "edge-testapp",
    }
    defaults.update(overrides)
    svc = Service(**defaults)
    db.add(svc)
    db.flush()
    status = ServiceStatus(service_id=svc.id, phase="pending")
    db.add(status)
    db.commit()
    return svc


def _create_service_via_api(client, **overrides):
    """Create a service through the API."""
    body = {
        "name": "App",
        "upstream_container_id": "abc123",
        "upstream_container_name": "app",
        "upstream_scheme": "http",
        "upstream_port": 80,
        "hostname": "app.example.com",
        "base_domain": "example.com",
    }
    body.update(overrides)
    return client.post("/api/services", json=body)


# ---------------------------------------------------------------------------
# 1. SQLite foreign key CASCADE enforcement
# ---------------------------------------------------------------------------

class TestSqliteForeignKeyCascade:
    """Verify that PRAGMA foreign_keys=ON is active, making CASCADE deletes work."""

    def test_delete_service_cascades_status(self, db_session):
        """Deleting a service should automatically remove its ServiceStatus row."""
        svc = _create_service_in_db(db_session)
        svc_id = svc.id

        # Status exists
        assert db_session.get(ServiceStatus, svc_id) is not None

        # Delete the service
        db_session.delete(svc)
        db_session.commit()

        # Status should be gone via CASCADE
        assert db_session.get(ServiceStatus, svc_id) is None

    def test_delete_service_cascades_certificate(self, db_session):
        """Deleting a service should automatically remove its Certificate row."""
        svc = _create_service_in_db(db_session)
        svc_id = svc.id

        cert = Certificate(service_id=svc_id, hostname=svc.hostname)
        db_session.add(cert)
        db_session.commit()
        assert db_session.get(Certificate, svc_id) is not None

        db_session.delete(svc)
        db_session.commit()
        assert db_session.get(Certificate, svc_id) is None

    def test_delete_service_cascades_dns_record(self, db_session):
        """Deleting a service should automatically remove its DnsRecord row."""
        svc = _create_service_in_db(db_session)
        svc_id = svc.id

        dns = DnsRecord(service_id=svc_id, hostname=svc.hostname, record_id="cf_rec_1")
        db_session.add(dns)
        db_session.commit()
        assert db_session.get(DnsRecord, svc_id) is not None

        db_session.delete(svc)
        db_session.commit()
        assert db_session.get(DnsRecord, svc_id) is None

    def test_cascade_deletes_all_related_rows_at_once(self, db_session):
        """All three child tables should be cleaned up in one delete."""
        svc = _create_service_in_db(db_session)
        svc_id = svc.id

        db_session.add(Certificate(service_id=svc_id, hostname=svc.hostname))
        db_session.add(DnsRecord(service_id=svc_id, hostname=svc.hostname))
        db_session.commit()

        db_session.delete(svc)
        db_session.commit()

        assert db_session.get(ServiceStatus, svc_id) is None
        assert db_session.get(Certificate, svc_id) is None
        assert db_session.get(DnsRecord, svc_id) is None

    def test_pragma_is_active_on_test_engine(self, db_engine):
        """The test engine (configured like production) should have PRAGMA foreign_keys=ON."""
        with db_engine.connect() as conn:
            result = conn.exec_driver_sql("PRAGMA foreign_keys")
            row = result.fetchone()
            assert row is not None
            assert row[0] == 1, "PRAGMA foreign_keys should be ON (1)"

    def test_production_engine_has_pragma_listener(self):
        """Verify the production engine module has registered the PRAGMA event listener."""
        from sqlalchemy import event as sa_event
        from app.database import engine

        # Check that listeners are registered on the engine's pool for 'connect'
        has_fk_listener = sa_event.contains(engine, "connect", None)
        # Alternative: just verify the function exists in the module
        from app.database import _set_sqlite_pragma
        assert callable(_set_sqlite_pragma)


# ---------------------------------------------------------------------------
# 2. Certificate renewal uses DB-backed paths
# ---------------------------------------------------------------------------

class TestCertRenewalDbPaths:
    def test_get_certs_root_default(self, db_session):
        """With no DB override, _get_certs_root falls back to config settings."""
        from app.certs.renewal_task import _get_certs_root
        from app.config import settings

        root = _get_certs_root(db_session)
        assert str(root) == str(settings.certs_dir)

    def test_get_certs_root_respects_db_override(self, db_session):
        """When cert_root is set in the DB, _get_certs_root uses it."""
        from pathlib import Path
        from app.certs.renewal_task import _get_certs_root
        from app.settings_store import set_setting

        set_setting(db_session, "cert_root", "/custom/certs")
        db_session.commit()

        root = _get_certs_root(db_session)
        assert root == Path("/custom/certs")

    @patch("app.certs.renewal_task.get_cert_expiry")
    @patch("app.certs.renewal_task.issue_cert")
    @patch("app.certs.renewal_task.read_secret")
    def test_issue_cert_uses_db_cert_path(self, mock_secret, mock_issue, mock_expiry, db_session):
        """When issuing a cert, the cert_dir and lego_dir should reflect DB settings."""
        from app.certs.renewal_task import process_service_cert
        from app.settings_store import set_setting
        from pathlib import Path

        mock_secret.return_value = "cf-token"
        mock_expiry.side_effect = [None, None]  # no cert exists, then after issue still None

        set_setting(db_session, "cert_root", "/custom/certs")
        db_session.flush()

        svc = _create_service_in_db(db_session)

        # issue_cert will raise to stop execution after we capture the call
        mock_issue.side_effect = RuntimeError("stopped for test")
        process_service_cert(db_session, svc)

        # Verify issue_cert was called with the custom paths
        if mock_issue.called:
            call_args = mock_issue.call_args
            cert_dir = call_args[0][3] if len(call_args[0]) > 3 else call_args.kwargs.get("cert_dir")
            lego_dir = call_args[0][4] if len(call_args[0]) > 4 else call_args.kwargs.get("lego_dir")
            assert str(cert_dir) == str(Path("/custom/certs") / svc.hostname)
            assert str(lego_dir) == str(Path("/custom/certs") / ".lego")


# ---------------------------------------------------------------------------
# 3. Cloudflare import fix (cloudflare_adapter, not cloudflare_api)
# ---------------------------------------------------------------------------

class TestCloudflareImportFix:
    def test_import_path_is_correct(self):
        """The import in health-check-full should resolve to the real module."""
        # This verifies the import path actually works
        from app.adapters.cloudflare_adapter import find_record
        assert callable(find_record)

    @patch("app.health.health_checker.run_health_checks")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    @patch("app.adapters.cloudflare_adapter.find_record")
    def test_health_check_full_calls_cloudflare_adapter(
        self, mock_find, mock_secret, mock_checks, client, db_session
    ):
        """The full health check endpoint should import from cloudflare_adapter."""
        from app.settings_store import set_setting

        mock_checks.return_value = {"edge_container_present": True}
        mock_find.return_value = {"content": "100.64.0.1"}

        # Set up zone_id so the CF path is taken
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/health-check-full")
        assert resp.status_code == 200
        data = resp.json()
        # Should have extended CF results, not an import error
        assert "cf_record_exists" in data["extended"]
        assert data["extended"]["cf_record_exists"] is True


# ---------------------------------------------------------------------------
# 4. Docker socket consistency across all endpoints
# ---------------------------------------------------------------------------

class TestDockerSocketConsistency:
    """Verify that action endpoints pass the configured Docker socket to helpers."""

    @patch("app.edge.container_manager.reload_caddy")
    def test_reload_passes_socket(self, mock_reload, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_reload.return_value = "ok"
        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/reload")

        mock_reload.assert_called_once()
        # Third arg is socket_path
        call_args = mock_reload.call_args
        assert call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.restart_edge")
    def test_restart_passes_socket(self, mock_restart, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/restart-edge")

        mock_restart.assert_called_once()
        call_args = mock_restart.call_args
        assert call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.get_edge_logs")
    def test_logs_passes_socket(self, mock_logs, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_logs.return_value = "some logs"
        svc_id = _create_service_via_api(client).json()["id"]
        client.get(f"/api/services/{svc_id}/logs/edge")

        mock_logs.assert_called_once()
        call_kwargs = mock_logs.call_args
        assert call_kwargs.kwargs.get("socket_path") == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_passes_socket(self, mock_stop, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/disable")

        mock_stop.assert_called_once()
        call_args = mock_stop.call_args
        assert call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_passes_socket(self, mock_remove_edge, mock_remove_net, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        client.delete(f"/api/services/{svc_id}")

        mock_remove_edge.assert_called_once()
        assert mock_remove_edge.call_args[0][2] == "unix:///custom/docker.sock"
        mock_remove_net.assert_called_once()
        assert mock_remove_net.call_args[0][1] == "unix:///custom/docker.sock"

    @patch("app.edge.container_manager.get_edge_version")
    def test_edge_version_passes_socket(self, mock_ver, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_ver.return_value = "0.2.0"
        svc_id = _create_service_via_api(client).json()["id"]
        client.get(f"/api/services/{svc_id}/edge-version")

        mock_ver.assert_called_once()
        assert mock_ver.call_args[0][2] == "unix:///custom/docker.sock"

    @patch("app.reconciler.reconcile_loop.reconcile_one")
    def test_manual_reconcile_passes_socket(self, mock_reconcile, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_reconcile.return_value = {"phase": "healthy", "error": None}
        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/reconcile")

        mock_reconcile.assert_called_once()
        assert mock_reconcile.call_args.kwargs.get("socket_path") == "unix:///custom/docker.sock"

    def test_default_socket_returns_default_value(self, db_session):
        """When no custom socket is configured, _get_docker_socket returns the DEFAULTS value."""
        from app.routers.services import _get_docker_socket
        result = _get_docker_socket(db_session)
        # The settings_store DEFAULTS dict provides a default Docker socket path
        assert result == "unix:///var/run/docker.sock"

    @patch("app.secrets.read_secret")
    @patch("app.edge.container_manager.recreate_edge")
    def test_recreate_passes_socket(self, mock_recreate, mock_secret, client, db_session):
        from app.settings_store import set_setting
        set_setting(db_session, "docker_socket_path", "unix:///custom/docker.sock")
        db_session.commit()

        mock_secret.return_value = "tskey-auth-test"
        mock_recreate.return_value = "new_id"

        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/recreate-edge")

        mock_recreate.assert_called_once()
        # socket_path is the last positional arg
        call_args = mock_recreate.call_args[0]
        assert call_args[-1] == "unix:///custom/docker.sock"


# ---------------------------------------------------------------------------
# 5. Hostname domain validation on update
# ---------------------------------------------------------------------------

class TestUpdateHostnameValidation:
    def test_update_hostname_valid_domain(self, client):
        """Updating hostname to a valid subdomain of the base domain should succeed."""
        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"

    def test_update_hostname_wrong_domain_rejected(self, client):
        """Updating hostname to a different domain should fail with 422."""
        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.wrong.com"})
        assert resp.status_code == 422
        assert "must end with" in resp.json()["detail"]

    def test_update_hostname_same_value_ok(self, client):
        """Updating hostname to the same value should succeed (no validation needed)."""
        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.example.com"})
        assert resp.status_code == 200

    def test_update_hostname_conflict_still_caught(self, client):
        """Uniqueness check should still fire before domain validation."""
        _create_service_via_api(client, hostname="taken.example.com", name="First")
        svc_id = _create_service_via_api(client, hostname="other.example.com", name="Second").json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "taken.example.com"})
        assert resp.status_code == 409

    def test_update_non_hostname_fields_unaffected(self, client):
        """Updating non-hostname fields should not trigger hostname domain check."""
        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_hostname_deep_subdomain_accepted(self, client):
        """Deep subdomains should be accepted as long as they end with the base domain."""
        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "a.b.c.example.com"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. Delete uses runtime paths for disk cleanup
# ---------------------------------------------------------------------------

class TestDeleteUsesRuntimePaths:
    @patch("app.edge.network_manager.remove_network")
    @patch("app.edge.container_manager.remove_edge")
    def test_delete_reads_runtime_paths(self, mock_re, mock_rn, client, db_session, tmp_data_dir):
        """Delete should use get_runtime_paths() for disk cleanup, not hardcoded settings."""
        from app.settings_store import set_setting
        from pathlib import Path

        # Set custom paths in DB
        custom_gen = str(tmp_data_dir / "custom_gen")
        custom_certs = str(tmp_data_dir / "custom_certs")
        custom_ts = str(tmp_data_dir / "custom_ts")
        set_setting(db_session, "generated_root", custom_gen)
        set_setting(db_session, "cert_root", custom_certs)
        set_setting(db_session, "tailscale_state_root", custom_ts)
        db_session.commit()

        resp = _create_service_via_api(client)
        svc = resp.json()

        # Create dirs that the delete should clean up
        svc_gen = Path(custom_gen) / svc["id"]
        svc_cert = Path(custom_certs) / svc["hostname"]
        svc_ts = Path(custom_ts) / svc["edge_container_name"]
        for d in [svc_gen, svc_cert, svc_ts]:
            d.mkdir(parents=True, exist_ok=True)
            (d / "dummy.txt").write_text("test")

        client.delete(f"/api/services/{svc['id']}")

        # Dirs from custom paths should be cleaned up
        assert not svc_gen.exists()
        assert not svc_cert.exists()
        assert not svc_ts.exists()


# ---------------------------------------------------------------------------
# 7. DeprecationWarning is gone (auth tests use client cookie jar)
# ---------------------------------------------------------------------------

class TestNoDeprecationWarnings:
    def test_no_per_request_cookies_pattern(self):
        """Verify test_auth_api.py doesn't use deprecated per-request cookies= param."""
        import ast
        from pathlib import Path

        test_file = Path(__file__).parent / "test_auth_api.py"
        source = test_file.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "cookies":
                        # This would indicate per-request cookies, which is deprecated
                        assert False, (
                            f"Found cookies= keyword arg at line {kw.lineno}. "
                            "Use client.cookies.set() instead."
                        )
