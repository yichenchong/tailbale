"""Tests for bug-fix pass 3:
- String-vs-Path in recreate-edge/update-edge (container_manager accepts str|Path)
- Edge image version-aware rebuild (ensure_edge_image checks label)
- Hostname change cleans up old DNS record + cert files + cert metadata
- Disable sets status to "disabled" (no stale "healthy")
"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

from app.models.certificate import Certificate
from app.models.dns_record import DnsRecord
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service_in_db(db, **overrides):
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
    status = ServiceStatus(service_id=svc.id, phase="healthy", message="All checks passed")
    db.add(status)
    db.commit()
    return svc


def _create_service_via_api(client, **overrides):
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
# 1. String-vs-Path: container_manager accepts str | Path
# ---------------------------------------------------------------------------


class TestStringPathAcceptance:
    """create_edge_container and recreate_edge should accept both str and Path."""

    @patch("app.edge.container_manager.ensure_edge_image")
    @patch("app.edge.container_manager._get_client")
    def test_create_accepts_strings(self, mock_get_client, mock_ensure, tmp_path):
        from app.edge.container_manager import create_edge_container

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.containers.create.return_value = MagicMock(id="c1")

        svc = MagicMock()
        svc.id = "svc_1"
        svc.hostname = "app.example.com"
        svc.edge_container_name = "edge_app"
        svc.network_name = "edge_net_app"
        svc.ts_hostname = "edge-app"

        # Pass strings (as get_runtime_paths returns), not Path objects
        result = create_edge_container(
            svc, "tskey-test",
            str(tmp_path / "gen"), str(tmp_path / "certs"), str(tmp_path / "ts"),
        )
        assert result == "c1"
        mock_client.containers.create.assert_called_once()

    @patch("app.edge.container_manager.ensure_edge_image")
    @patch("app.edge.container_manager._get_client")
    def test_create_accepts_paths(self, mock_get_client, mock_ensure, tmp_path):
        from app.edge.container_manager import create_edge_container

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.containers.create.return_value = MagicMock(id="c2")

        svc = MagicMock()
        svc.id = "svc_2"
        svc.hostname = "app.example.com"
        svc.edge_container_name = "edge_app"
        svc.network_name = "edge_net_app"
        svc.ts_hostname = "edge-app"

        # Pass Path objects (traditional usage)
        result = create_edge_container(
            svc, "tskey-test",
            tmp_path / "gen", tmp_path / "certs", tmp_path / "ts",
        )
        assert result == "c2"

    @patch("app.edge.container_manager.start_edge")
    @patch("app.edge.container_manager.create_edge_container", return_value="c3")
    @patch("app.edge.container_manager.remove_edge")
    def test_recreate_accepts_strings(self, mock_rm, mock_create, mock_start, tmp_path):
        from app.edge.container_manager import recreate_edge

        svc = MagicMock()
        svc.id = "svc_3"
        svc.edge_container_name = "edge_app"

        # Pass strings
        result = recreate_edge(
            svc, "tskey-test",
            str(tmp_path / "gen"), str(tmp_path / "certs"), str(tmp_path / "ts"),
        )
        assert result == "c3"

    def test_type_hints_accept_str_or_path(self):
        """Verify the function signatures accept str | Path."""
        import inspect
        from app.edge.container_manager import create_edge_container, recreate_edge

        sig_create = inspect.signature(create_edge_container)
        sig_recreate = inspect.signature(recreate_edge)
        for param_name in ("generated_dir", "certs_dir", "tailscale_state_dir"):
            annotation = str(sig_create.parameters[param_name].annotation)
            assert "str" in annotation and "Path" in annotation, \
                f"create_edge_container.{param_name} should accept str | Path"
            annotation_r = str(sig_recreate.parameters[param_name].annotation)
            assert "str" in annotation_r and "Path" in annotation_r, \
                f"recreate_edge.{param_name} should accept str | Path"


# ---------------------------------------------------------------------------
# 2. Edge image version-aware rebuild
# ---------------------------------------------------------------------------


class TestEdgeImageVersionRebuild:
    """ensure_edge_image should rebuild when the image version label is stale."""

    @patch("app.edge.image_builder.build_edge_image")
    @patch("app.edge.image_builder._get_client")
    def test_rebuilds_when_version_mismatches(self, mock_get_client, mock_build):
        from app.edge.image_builder import ensure_edge_image

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_image = MagicMock()
        mock_image.labels = {"tailbale.version": "0.0.0-stale"}
        mock_client.images.get.return_value = mock_image

        ensure_edge_image()
        mock_build.assert_called_once()

    @patch("app.edge.image_builder.build_edge_image")
    @patch("app.edge.image_builder._get_client")
    def test_skips_rebuild_when_version_matches(self, mock_get_client, mock_build):
        from app.edge.image_builder import ensure_edge_image
        from app.version import __version__

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_image = MagicMock()
        mock_image.labels = {"tailbale.version": __version__}
        mock_client.images.get.return_value = mock_image

        ensure_edge_image()
        mock_build.assert_not_called()

    @patch("app.edge.image_builder.build_edge_image")
    @patch("app.edge.image_builder._get_client")
    def test_rebuilds_when_no_version_label(self, mock_get_client, mock_build):
        from app.edge.image_builder import ensure_edge_image

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_image = MagicMock()
        mock_image.labels = {}
        mock_client.images.get.return_value = mock_image

        ensure_edge_image()
        mock_build.assert_called_once()

    @patch("app.edge.image_builder.build_edge_image")
    @patch("app.edge.image_builder._get_client")
    def test_builds_when_image_missing(self, mock_get_client, mock_build):
        from app.edge.image_builder import ensure_edge_image

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.images.get.side_effect = docker.errors.ImageNotFound("nope")

        ensure_edge_image()
        mock_build.assert_called_once()


# ---------------------------------------------------------------------------
# 3. Hostname change cleans up old DNS + cert material
# ---------------------------------------------------------------------------


class TestHostnameChangeCleanup:
    """Changing hostname should clean up old DNS record, cert files, and cert metadata."""

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_hostname_change_deletes_old_dns(self, mock_secret, mock_cleanup, client, db_session):
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        assert resp.json()["hostname"] == "new.example.com"

        # cleanup_dns_record should have been called for the OLD hostname
        mock_cleanup.assert_called_once()

    @patch("app.adapters.dns_reconciler.cleanup_dns_record")
    @patch("app.secrets.read_secret", return_value=None)
    def test_hostname_change_without_cf_token_still_succeeds(self, mock_secret, mock_cleanup, client):
        """Even without CF credentials, hostname change should go through."""
        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200
        mock_cleanup.assert_not_called()

    @patch("app.adapters.dns_reconciler.cleanup_dns_record", side_effect=Exception("CF unreachable"))
    @patch("app.secrets.read_secret", return_value="cf-token")
    def test_hostname_change_updates_dns_record_hostname_on_failed_cleanup(
        self, mock_secret, mock_cleanup, client, db_session
    ):
        """If DNS cleanup fails, the surviving DnsRecord row should still get its hostname updated."""
        from app.settings_store import set_setting

        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]

        # Create a DnsRecord row that will survive the failed cleanup
        dns = DnsRecord(service_id=svc_id, hostname="app.example.com", record_id="cf_rec_old")
        db_session.add(dns)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200

        db_session.expire_all()
        updated_dns = db_session.get(DnsRecord, svc_id)
        assert updated_dns is not None
        assert updated_dns.hostname == "new.example.com"

    def test_hostname_change_updates_cert_hostname(self, client, db_session):
        """Certificate.hostname should be updated to the new hostname."""
        svc_id = _create_service_via_api(client).json()["id"]

        # Create a cert record for the service
        cert = Certificate(service_id=svc_id, hostname="app.example.com")
        db_session.add(cert)
        db_session.commit()

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200

        db_session.expire_all()
        updated_cert = db_session.get(Certificate, svc_id)
        assert updated_cert is not None
        assert updated_cert.hostname == "new.example.com"

    def test_hostname_change_removes_old_cert_dir(self, client, db_session, tmp_data_dir):
        """Old cert directory should be cleaned up on hostname change."""
        from app.settings_store import set_setting

        # Set custom cert root to tmp dir
        custom_certs = str(tmp_data_dir / "certs")
        set_setting(db_session, "cert_root", custom_certs)
        db_session.commit()

        svc_id = _create_service_via_api(client).json()["id"]

        # Create old cert directory with files
        old_cert_dir = Path(custom_certs) / "app.example.com"
        old_cert_dir.mkdir(parents=True)
        (old_cert_dir / "fullchain.pem").write_text("old-cert")
        (old_cert_dir / "privkey.pem").write_text("old-key")

        resp = client.put(f"/api/services/{svc_id}", json={"hostname": "new.example.com"})
        assert resp.status_code == 200

        # Old cert dir should be gone
        assert not old_cert_dir.exists()

    def test_same_hostname_no_cleanup(self, client):
        """Setting hostname to same value shouldn't trigger cleanup."""
        svc_id = _create_service_via_api(client).json()["id"]
        with patch("app.adapters.dns_reconciler.cleanup_dns_record") as mock_cleanup:
            resp = client.put(f"/api/services/{svc_id}", json={"hostname": "app.example.com"})
            assert resp.status_code == 200
            mock_cleanup.assert_not_called()

    def test_non_hostname_update_no_cleanup(self, client):
        """Changing non-hostname fields shouldn't trigger DNS/cert cleanup."""
        svc_id = _create_service_via_api(client).json()["id"]
        with patch("app.adapters.dns_reconciler.cleanup_dns_record") as mock_cleanup:
            resp = client.put(f"/api/services/{svc_id}", json={"name": "Renamed"})
            assert resp.status_code == 200
            mock_cleanup.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Disable sets phase to "disabled"
# ---------------------------------------------------------------------------


class TestDisableSetsPhase:
    """Disabling a service should update its status phase to 'disabled'."""

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_sets_phase_disabled(self, mock_stop, client):
        svc_id = _create_service_via_api(client).json()["id"]
        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is False
        assert data["status"]["phase"] == "disabled"
        assert data["status"]["message"] == "Service disabled by user"

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_clears_health_checks(self, mock_stop, client, db_session):
        """Stale health checks should be cleared on disable."""
        resp = _create_service_via_api(client)
        svc_id = resp.json()["id"]

        # Manually set health checks in the DB so we can verify they get cleared
        status = db_session.query(ServiceStatus).filter_by(service_id=svc_id).first()
        if status:
            import json
            status.health_checks = json.dumps({"edge_container_running": True})
            db_session.commit()

        resp = client.post(f"/api/services/{svc_id}/disable")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"]["health_checks"] is None

    @patch("app.edge.container_manager.stop_edge")
    def test_disable_does_not_leave_healthy_status(self, mock_stop, client, db_session):
        """After disable, the status must not show 'healthy'."""
        svc_id = _create_service_via_api(client).json()["id"]

        # Simulate reconciler having set status to "healthy"
        status = db_session.query(ServiceStatus).filter_by(service_id=svc_id).first()
        if status:
            status.phase = "healthy"
            status.message = "All checks passed"
            db_session.commit()

        resp = client.post(f"/api/services/{svc_id}/disable")
        data = resp.json()
        assert data["status"]["phase"] != "healthy"
        assert data["status"]["phase"] == "disabled"

    @patch("app.edge.container_manager.stop_edge")
    def test_get_disabled_service_shows_disabled(self, mock_stop, client):
        """GET of a disabled service should show phase=disabled."""
        svc_id = _create_service_via_api(client).json()["id"]
        client.post(f"/api/services/{svc_id}/disable")

        resp = client.get(f"/api/services/{svc_id}")
        data = resp.json()
        assert data["enabled"] is False
        assert data["status"]["phase"] == "disabled"
