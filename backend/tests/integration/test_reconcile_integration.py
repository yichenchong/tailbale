"""End-to-end reconcile integration tests (AR12 safety net).

These drive the real ``router -> service -> reconciler -> edge/DNS`` chain
against the injected fake Docker client (see this package's ``conftest``) plus
tmp cert/generated dirs, and assert on OBSERVABLE outcomes — the ServiceStatus
row/phase, the ``.reload_pending`` / ``.cert_loaded`` markers, the generated
Caddyfile, and emitted events — rather than mock call counts.

They are the wiring safety net the per-piece unit tests (test_reconciler.py,
test_services_api.py) cannot provide: everything below runs the actual
network_manager / container_manager / health_checker code paths.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from app import secrets, settings_store
from app.models.dns_record import DnsRecord
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler.reconciler import reconcile_service

from .conftest import write_valid_cert

# Matches the fake daemon's exec-reported Tailscale IP (conftest FakeExecMixin).
FAKE_TS_IP = "100.64.0.5"
# A configured socket path routes docker_client.connect() through
# DockerClient(base_url=...), which the fake_docker fixture patches.
FAKE_SOCKET = "unix:///fake/docker.sock"

_CREATE_BODY = {
    "name": "Nextcloud",
    "upstream_container_id": "upstream123",
    "upstream_container_name": "nextcloud",
    "upstream_scheme": "http",
    "upstream_port": 80,
    "hostname": "nextcloud.example.com",
    "base_domain": "example.com",
}


def _events_by_kind(db, service_id: str) -> dict[str, int]:
    rows = db.query(Event).filter(Event.service_id == service_id).all()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    return counts


def _prepare_infra(fake_docker, tmp_data_dir, hostname: str) -> None:
    """Seed the auth-key secret, the on-disk cert, and the upstream container."""
    secrets.write_secret(secrets.TAILSCALE_AUTH_KEY, "tskey-auth-integration-xyz")
    write_valid_cert(tmp_data_dir / "certs", hostname)
    fake_docker.register_upstream("upstream123", "nextcloud")


class TestApiToEdgeReconcile:
    """Drive create + manual /reconcile through the HTTP router."""

    def test_create_then_reconcile_converges_to_warning_without_dns(
        self, client, db_session, fake_docker, tmp_data_dir
    ):
        _prepare_infra(fake_docker, tmp_data_dir, "nextcloud.example.com")

        # Create via the REAL router: upstream validation runs against the fake
        # daemon (not stubbed), and the post-create background reconcile fires
        # the real reconcile pipeline.
        resp = client.post("/api/services", json=_CREATE_BODY)
        assert resp.status_code == 201, resp.text
        svc_id = resp.json()["id"]

        # Explicit manual reconcile so the assertion point is deterministic.
        rec = client.post(f"/api/services/{svc_id}/reconcile")
        assert rec.status_code == 200, rec.text
        body = rec.json()
        assert body["success"] is True
        # All critical checks pass (upstream+edge+tailscale+cert+config); only
        # the DNS warning-checks fail because no DnsRecord was seeded.
        assert body["phase"] == "warning"
        assert body["error"] is None

        # --- Observable DB state ---
        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc_id)
        assert status.phase == "warning"
        assert status.tailscale_ip == FAKE_TS_IP
        assert status.edge_container_id is not None
        assert status.health_checks["edge_container_running"] is True
        assert status.health_checks["cert_present"] is True
        assert status.health_checks["https_probe_ok"] is True
        assert status.health_checks["dns_record_present"] is False

        # --- Observable fake-daemon graph ---
        edge = fake_docker.edge_container()
        assert edge is not None
        assert edge.status == "running"
        assert edge.labels["tailbale.service_id"] == svc_id
        assert "edge_net_nextcloud" in fake_docker.created_networks

    def test_create_then_reconcile_converges_to_healthy_with_dns(
        self, client, db_session, fake_docker, tmp_data_dir
    ):
        _prepare_infra(fake_docker, tmp_data_dir, "nextcloud.example.com")

        resp = client.post("/api/services", json=_CREATE_BODY)
        assert resp.status_code == 201, resp.text
        svc_id = resp.json()["id"]

        # Seed a DNS record matching the live Tailscale IP so the DNS
        # warning-checks pass and the aggregate reaches "healthy".
        db_session.add(
            DnsRecord(
                service_id=svc_id,
                record_id="cf_rec_1",
                hostname="nextcloud.example.com",
                record_type="A",
                value=FAKE_TS_IP,
            )
        )
        db_session.commit()

        rec = client.post(f"/api/services/{svc_id}/reconcile")
        assert rec.status_code == 200, rec.text
        assert rec.json()["phase"] == "healthy"

        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc_id)
        assert status.phase == "healthy"
        assert status.health_checks["dns_record_present"] is True
        assert status.health_checks["dns_matches_ip"] is True
        assert all(status.health_checks.values())

    def test_reconcile_missing_authkey_fails_and_creates_no_edge(
        self, client, db_session, fake_docker, tmp_data_dir
    ):
        # Deliberately DO NOT write the Tailscale auth key; cert + upstream are
        # present, so the failure is specifically the missing-secret guard.
        write_valid_cert(tmp_data_dir / "certs", "nextcloud.example.com")
        fake_docker.register_upstream("upstream123", "nextcloud")

        resp = client.post("/api/services", json=_CREATE_BODY)
        assert resp.status_code == 201, resp.text
        svc_id = resp.json()["id"]

        rec = client.post(f"/api/services/{svc_id}/reconcile")
        assert rec.status_code == 200, rec.text
        body = rec.json()
        assert body["phase"] == "failed"
        assert "auth key" in body["error"].lower()

        db_session.expire_all()
        status = db_session.get(ServiceStatus, svc_id)
        assert status.phase == "failed"
        # Reconcile bailed at the validate step: no edge container was created.
        assert fake_docker.edge_container() is None
        assert _events_by_kind(db_session, svc_id).get("reconcile_failed", 0) >= 1


class TestDirectReconcileMarkersAndEvents:
    """Call reconcile_service directly to assert markers + event stream precisely."""

    def _make_service(self, db) -> Service:
        svc = Service(
            name="Direct",
            upstream_container_id="upstream123",
            upstream_container_name="nextcloud",
            upstream_scheme="http",
            upstream_port=80,
            hostname="direct.example.com",
            base_domain="example.com",
            edge_container_name="edge_direct",
            network_name="edge_net_direct",
            ts_hostname="edge-direct",
        )
        db.add(svc)
        db.flush()
        db.add(ServiceStatus(service_id=svc.id, phase="pending"))
        db.commit()
        return svc

    def test_reconcile_writes_markers_config_and_events(
        self, db_session, fake_docker, tmp_data_dir
    ):
        secrets.write_secret(secrets.TAILSCALE_AUTH_KEY, "tskey-auth-integration-xyz")
        write_valid_cert(tmp_data_dir / "certs", "direct.example.com")
        fake_docker.register_upstream("upstream123", "nextcloud")
        svc = self._make_service(db_session)

        result = reconcile_service(db_session, svc, socket_path=FAKE_SOCKET)

        assert result["phase"] == "warning"  # no DNS seeded
        assert result["tailscale_ip"] == FAKE_TS_IP
        assert result["caddy_reloaded"] is True
        assert result["error"] is None

        paths = settings_store.get_runtime_paths(db_session)
        service_dir = Path(paths["generated_dir"]) / svc.id

        # Caddyfile was rendered + written for real.
        caddyfile = service_dir / "Caddyfile"
        assert caddyfile.exists()
        assert caddyfile.read_text(encoding="utf-8").strip() != ""

        # reload_pending cleared after a successful reload.
        assert not (service_dir / ".reload_pending").exists()

        # cert_loaded records the fingerprint of the served cert.
        cert_state = service_dir / ".cert_loaded"
        assert cert_state.exists()
        cert_file = Path(paths["certs_dir"]) / svc.hostname / "current" / "fullchain.pem"
        expected_fp = hashlib.sha256(cert_file.read_bytes()).hexdigest()
        assert cert_state.read_text(encoding="utf-8").strip() == expected_fp

        # Event stream reflects the lifecycle side effects.
        kinds = _events_by_kind(db_session, svc.id)
        assert kinds.get("edge_started", 0) >= 1
        assert kinds.get("tailscale_ip_acquired", 0) == 1
        assert kinds.get("caddy_reloaded", 0) == 1
        assert kinds.get("reconcile_completed", 0) >= 1

    def test_second_reconcile_is_idempotent_no_reload_no_churn(
        self, db_session, fake_docker, tmp_data_dir
    ):
        secrets.write_secret(secrets.TAILSCALE_AUTH_KEY, "tskey-auth-integration-xyz")
        write_valid_cert(tmp_data_dir / "certs", "direct.example.com")
        fake_docker.register_upstream("upstream123", "nextcloud")
        svc = self._make_service(db_session)

        first = reconcile_service(db_session, svc, socket_path=FAKE_SOCKET)
        assert first["caddy_reloaded"] is True

        second = reconcile_service(db_session, svc, socket_path=FAKE_SOCKET)

        # Nothing changed on disk: config identical, cert fingerprint recorded,
        # so no reload is issued the second time.
        assert second["caddy_reloaded"] is False
        assert second["phase"] == "warning"

        kinds = _events_by_kind(db_session, svc.id)
        # One-shot lifecycle events fired exactly once across BOTH passes.
        assert kinds.get("tailscale_ip_acquired", 0) == 1
        assert kinds.get("caddy_reloaded", 0) == 1
        # Pass one emits edge_started twice (create -> "created", then start ->
        # "running"); pass two finds it already running and adds none.
        assert kinds.get("edge_started", 0) == 2
        # reconcile_completed is emitted once per pass.
        assert kinds.get("reconcile_completed", 0) == 2

    def test_hostname_change_reprovisions_new_cert_config_and_reload(
        self, db_session, fake_docker, tmp_data_dir
    ):
        # AR-R3-13 integration seam: after a service's hostname changes, the next
        # reconcile must re-provision for the NEW hostname — create its cert dir,
        # re-render the Caddyfile against the new host, notice the config change
        # and reload, and record the new served-cert fingerprint. Previously this
        # re-provision path was only exercised end-to-end via the services API test.
        secrets.write_secret(secrets.TAILSCALE_AUTH_KEY, "tskey-auth-integration-xyz")
        write_valid_cert(tmp_data_dir / "certs", "direct.example.com")
        fake_docker.register_upstream("upstream123", "nextcloud")
        svc = self._make_service(db_session)

        first = reconcile_service(db_session, svc, socket_path=FAKE_SOCKET)
        assert first["caddy_reloaded"] is True

        paths = settings_store.get_runtime_paths(db_session)
        service_dir = Path(paths["generated_dir"]) / svc.id
        certs_dir = Path(paths["certs_dir"])
        assert "direct.example.com" in (service_dir / "Caddyfile").read_text(encoding="utf-8")
        old_fp = (service_dir / ".cert_loaded").read_text(encoding="utf-8").strip()

        # Re-provision: the operator changed the hostname and a fresh valid cert
        # landed on disk under the new hostname (cert issuance itself is out of
        # scope here, as elsewhere in this file which pre-seeds via write_valid_cert).
        write_valid_cert(certs_dir, "renamed.example.com")
        svc.hostname = "renamed.example.com"
        db_session.commit()

        second = reconcile_service(db_session, svc, socket_path=FAKE_SOCKET)

        assert second["error"] is None
        # New host -> rendered config differs -> a reload is issued.
        assert second["caddy_reloaded"] is True
        assert (certs_dir / "renamed.example.com").is_dir()
        new_caddyfile = (service_dir / "Caddyfile").read_text(encoding="utf-8")
        assert "renamed.example.com" in new_caddyfile
        assert "direct.example.com" not in new_caddyfile
        # The served-cert fingerprint now tracks the new hostname's certificate.
        new_cert = certs_dir / "renamed.example.com" / "current" / "fullchain.pem"
        new_fp = (service_dir / ".cert_loaded").read_text(encoding="utf-8").strip()
        assert new_fp == hashlib.sha256(new_cert.read_bytes()).hexdigest()
        assert new_fp != old_fp


class _FakeCFResponse:
    """Minimal stand-in for an ``httpx2.Response`` (only what cloudflare_adapter reads)."""

    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class TestReconcileExercisesRealDnsPath:
    """RC-R2-2: close the seam's DNS gap.

    The other integration tests never set CLOUDFLARE_TOKEN, so ``_ensure_dns``
    short-circuits and the REAL ``reconcile_dns`` create/update path — plus the
    new ``global_ops_lock`` wrapping around it — is never exercised by the seam
    despite its docstring advertising a full ``router -> service -> reconciler ->
    edge/DNS`` chain. This drives the actual DNS reconcile by patching ONLY the
    httpx2 HTTP edge (the true external boundary, exactly as the seam patches only
    the Docker client), and asserts the OBSERVABLE result: the persisted DnsRecord
    row and the emitted dns_created event.
    """

    def test_reconcile_creates_cloudflare_record_and_persists_row(
        self, db_session, fake_docker, tmp_data_dir
    ):
        secrets.write_secret(secrets.TAILSCALE_AUTH_KEY, "tskey-auth-integration-xyz")
        secrets.write_secret(secrets.CLOUDFLARE_TOKEN, "cf-token-integration")
        settings_store.set_setting(db_session, "cf_zone_id", "zone-int-1")
        db_session.commit()
        write_valid_cert(tmp_data_dir / "certs", "direct.example.com")
        fake_docker.register_upstream("upstream123", "nextcloud")

        svc = Service(
            name="DnsDirect",
            upstream_container_id="upstream123",
            upstream_container_name="nextcloud",
            upstream_scheme="http",
            upstream_port=80,
            hostname="direct.example.com",
            base_domain="example.com",
            edge_container_name="edge_dnsdirect",
            network_name="edge_net_dnsdirect",
            ts_hostname="edge-dnsdirect",
        )
        db_session.add(svc)
        db_session.flush()
        db_session.add(ServiceStatus(service_id=svc.id, phase="pending"))
        db_session.commit()

        # Fake Cloudflare edge: no existing A record (empty list) -> reconcile_dns
        # takes the create branch. Only the httpx2 verbs are patched.
        created_id = "cf-rec-created-1"

        def _fake_get(url, **kwargs):
            # list_a_records: no records yet for this hostname.
            return _FakeCFResponse({"success": True, "result": [], "result_info": {"total_count": 0}})

        def _fake_post(url, **kwargs):
            # create_a_record: echo back a record carrying our IP + comment.
            body = kwargs.get("json") or {}
            return _FakeCFResponse(
                {
                    "success": True,
                    "result": {
                        "id": created_id,
                        "content": body.get("content"),
                        "comment": body.get("comment"),
                        "name": body.get("name"),
                    },
                }
            )

        with (
            patch("httpx2.get", side_effect=_fake_get),
            patch("httpx2.post", side_effect=_fake_post),
        ):
            result = reconcile_service(db_session, svc, socket_path=FAKE_SOCKET)

        # DNS made the aggregate reach healthy (record present + matches IP).
        assert result["phase"] == "healthy", result
        assert result["tailscale_ip"] == FAKE_TS_IP

        # --- Observable: the real reconcile_dns create path persisted a row ---
        db_session.expire_all()
        dns_row = db_session.get(DnsRecord, svc.id)
        assert dns_row is not None
        assert dns_row.record_id == created_id
        assert dns_row.value == FAKE_TS_IP
        assert dns_row.hostname == "direct.example.com"

        # --- Observable: the create emitted a dns_created event ---
        kinds = _events_by_kind(db_session, svc.id)
        assert kinds.get("dns_created", 0) == 1

        status = db_session.get(ServiceStatus, svc.id)
        assert status.health_checks["dns_record_present"] is True
        assert status.health_checks["dns_matches_ip"] is True
