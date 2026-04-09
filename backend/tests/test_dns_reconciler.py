"""Tests for DNS reconciliation logic and drift detection."""

from unittest.mock import patch

from app.models.dns_record import DnsRecord
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus


def _create_service(db, **overrides):
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
    db.add(ServiceStatus(service_id=svc.id, phase="pending"))
    db.commit()
    return svc


class TestReconcileDns:
    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.find_record")
    def test_creates_record_when_absent(self, mock_find, mock_create, db_session):
        from app.adapters.dns_reconciler import reconcile_dns

        mock_find.return_value = None
        mock_create.return_value = {"id": "new_r1"}

        svc = _create_service(db_session)
        result = reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert result.record_id == "new_r1"
        assert result.value == "100.64.0.1"
        mock_create.assert_called_once_with("cf-token", "zone1", "testapp.example.com", "100.64.0.1")

        events = db_session.query(Event).filter(Event.kind == "dns_created").all()
        assert len(events) == 1

    @patch("app.adapters.dns_reconciler.update_a_record")
    @patch("app.adapters.dns_reconciler.find_record")
    def test_updates_record_when_ip_changed(self, mock_find, mock_update, db_session):
        from app.adapters.dns_reconciler import reconcile_dns

        mock_find.return_value = {"id": "r1", "content": "100.64.0.1"}
        mock_update.return_value = {"id": "r1", "content": "100.64.0.2"}

        svc = _create_service(db_session)
        result = reconcile_dns(db_session, svc, "100.64.0.2", "cf-token", "zone1")

        assert result.value == "100.64.0.2"
        mock_update.assert_called_once_with("cf-token", "zone1", "r1", "100.64.0.2")

        events = db_session.query(Event).filter(Event.kind == "dns_updated").all()
        assert len(events) == 1
        assert "100.64.0.1" in events[0].message  # old IP mentioned
        assert "100.64.0.2" in events[0].message  # new IP mentioned

    @patch("app.adapters.dns_reconciler.find_record")
    def test_noop_when_ip_matches(self, mock_find, db_session):
        from app.adapters.dns_reconciler import reconcile_dns

        mock_find.return_value = {"id": "r1", "content": "100.64.0.1"}

        svc = _create_service(db_session)
        result = reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        assert result.record_id == "r1"
        assert result.value == "100.64.0.1"

        # No create/update events
        events = db_session.query(Event).filter(
            Event.kind.in_(["dns_created", "dns_updated"])
        ).all()
        assert len(events) == 0

    @patch("app.adapters.dns_reconciler.create_a_record")
    @patch("app.adapters.dns_reconciler.find_record")
    def test_creates_dns_record_entry(self, mock_find, mock_create, db_session):
        from app.adapters.dns_reconciler import reconcile_dns

        mock_find.return_value = None
        mock_create.return_value = {"id": "r1"}

        svc = _create_service(db_session)
        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        dns = db_session.get(DnsRecord, svc.id)
        assert dns is not None
        assert dns.hostname == "testapp.example.com"
        assert dns.record_type == "A"

    @patch("app.adapters.dns_reconciler.find_record")
    def test_updates_existing_dns_record_entry(self, mock_find, db_session):
        from app.adapters.dns_reconciler import reconcile_dns

        svc = _create_service(db_session)

        # Pre-create a dns_record
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="old_r", value="100.64.0.9")
        db_session.add(dns)
        db_session.commit()

        mock_find.return_value = {"id": "old_r", "content": "100.64.0.1"}

        reconcile_dns(db_session, svc, "100.64.0.1", "cf-token", "zone1")

        dns = db_session.get(DnsRecord, svc.id)
        assert dns.value == "100.64.0.1"


class TestDetectDnsDrift:
    def test_no_drift_when_matching(self, db_session):
        from app.adapters.dns_reconciler import detect_dns_drift

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1")
        db_session.add(dns)
        db_session.commit()

        result = detect_dns_drift(db_session, svc, "100.64.0.1")
        assert result["dns_record_present"] is True
        assert result["dns_matches_ip"] is True
        assert result["drifted"] is False

    def test_drift_when_ip_changed(self, db_session):
        from app.adapters.dns_reconciler import detect_dns_drift

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1")
        db_session.add(dns)
        db_session.commit()

        result = detect_dns_drift(db_session, svc, "100.64.0.2")
        assert result["dns_record_present"] is True
        assert result["dns_matches_ip"] is False
        assert result["drifted"] is True
        assert result["stored_ip"] == "100.64.0.1"
        assert result["current_ip"] == "100.64.0.2"

    def test_no_record(self, db_session):
        from app.adapters.dns_reconciler import detect_dns_drift

        svc = _create_service(db_session)

        result = detect_dns_drift(db_session, svc, "100.64.0.1")
        assert result["dns_record_present"] is False
        assert result["dns_matches_ip"] is False
        assert result["stored_ip"] is None

    def test_no_tailscale_ip(self, db_session):
        from app.adapters.dns_reconciler import detect_dns_drift

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="r1", value="100.64.0.1")
        db_session.add(dns)
        db_session.commit()

        result = detect_dns_drift(db_session, svc, None)
        assert result["dns_matches_ip"] is False
        assert result["current_ip"] is None


class TestCleanupDnsRecord:
    """cleanup_dns_record should return structured result and only delete local row on success."""

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_success_deletes_both(self, mock_delete, db_session):
        from app.adapters.dns_reconciler import cleanup_dns_record

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_123", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result["deleted_remote"] is True
        assert result["deleted_local"] is True
        assert result["error"] is None

        # Local row should be gone (flush + expire to see the deletion)
        db_session.flush()
        db_session.expire_all()
        assert db_session.get(DnsRecord, svc.id) is None

    @patch("app.adapters.dns_reconciler.delete_a_record", side_effect=RuntimeError("API error"))
    def test_failure_preserves_local_row(self, mock_delete, db_session):
        from app.adapters.dns_reconciler import cleanup_dns_record

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_456", value="1.2.3.4")
        db_session.add(dns)
        db_session.commit()

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result["deleted_remote"] is False
        assert result["deleted_local"] is False
        assert result["error"] is not None
        assert "API error" in result["error"]

        # Local row should still exist
        row = db_session.get(DnsRecord, svc.id)
        assert row is not None
        assert row.record_id == "cf_456"

    def test_no_record_returns_noop(self, db_session):
        from app.adapters.dns_reconciler import cleanup_dns_record

        svc = _create_service(db_session)
        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result["deleted_remote"] is False
        assert result["deleted_local"] is False
        assert result["error"] is None

    def test_no_record_id_returns_noop(self, db_session):
        from app.adapters.dns_reconciler import cleanup_dns_record

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id=None)
        db_session.add(dns)
        db_session.commit()

        result = cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        assert result["deleted_remote"] is False
        assert result["deleted_local"] is False
        assert result["error"] is None

    @patch("app.adapters.dns_reconciler.delete_a_record", side_effect=Exception("timeout"))
    def test_failure_emits_warning_event(self, mock_delete, db_session):
        from app.adapters.dns_reconciler import cleanup_dns_record

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_789", value="5.6.7.8")
        db_session.add(dns)
        db_session.commit()

        cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        db_session.flush()

        events = db_session.query(Event).filter(Event.kind == "dns_cleanup_failed").all()
        assert len(events) == 1
        assert events[0].level == "warning"
        assert "timeout" in events[0].message.lower()

    @patch("app.adapters.dns_reconciler.delete_a_record")
    def test_success_emits_info_event(self, mock_delete, db_session):
        from app.adapters.dns_reconciler import cleanup_dns_record

        svc = _create_service(db_session)
        dns = DnsRecord(service_id=svc.id, hostname=svc.hostname, record_id="cf_aaa", value="1.1.1.1")
        db_session.add(dns)
        db_session.commit()

        cleanup_dns_record(db_session, svc, "cf-token", "zone123")
        db_session.flush()

        events = db_session.query(Event).filter(Event.kind == "dns_removed").all()
        assert len(events) == 1
        assert events[0].level == "info"


class TestDeleteWithDnsCleanup:
    """Test the service delete endpoint with cleanup_dns parameter."""

    def _create_via_api(self, client, **overrides):
        body = {
            "name": "Nextcloud",
            "upstream_container_id": "abc123def456",
            "upstream_container_name": "nextcloud",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "nextcloud.example.com",
            "base_domain": "example.com",
        }
        body.update(overrides)
        return client.post("/api/services", json=body)

    def test_delete_without_cleanup(self, client):
        svc_id = self._create_via_api(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204

    def test_delete_with_cleanup_dns_param(self, client):
        """cleanup_dns=true doesn't crash even without CF token configured."""
        svc_id = self._create_via_api(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.secrets.read_secret")
    def test_delete_calls_cleanup_when_configured(self, mock_secret, mock_delete, client, db_session):
        from app.settings_store import set_setting

        svc_id = self._create_via_api(client).json()["id"]

        # Set up CF token and zone
        mock_secret.return_value = "cf-token"
        set_setting(db_session, "cf_zone_id", "zone1")

        # Create DNS record for this service
        dns = DnsRecord(service_id=svc_id, hostname="nextcloud.example.com", record_id="r1", value="100.64.0.1")
        db_session.add(dns)
        db_session.commit()

        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204
        mock_delete.assert_called_once_with("cf-token", "zone1", "r1")
