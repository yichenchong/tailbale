"""Service-delete router tests for optional DNS cleanup."""

from unittest.mock import patch

from app.models.dns_record import DnsRecord
from app.settings_store import set_setting
from tests._services_helpers import _create_service


class TestDeleteWithDnsCleanup:
    def test_delete_without_cleanup(self, client):
        svc_id = _create_service(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}")
        assert resp.status_code == 204

    def test_delete_with_cleanup_dns_param(self, client):
        """cleanup_dns=true doesn't crash even without CF token configured."""
        svc_id = _create_service(client).json()["id"]
        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204

    @patch("app.adapters.dns_reconciler.delete_a_record")
    @patch("app.secrets.read_secret")
    def test_delete_calls_cleanup_when_configured(self, mock_secret, mock_delete, client, db_session):

        svc_id = _create_service(client).json()["id"]

        # Set up CF token and zone
        mock_secret.return_value = "cf-token"
        set_setting(db_session, "cf_zone_id", "zone1")

        # Create DNS record for this service
        dns = DnsRecord(service_id=svc_id, hostname="nextcloud.example.com", record_id="r1", value="100.64.0.1")
        db_session.add(dns)
        db_session.commit()

        resp = client.delete(f"/api/services/{svc_id}?cleanup_dns=true")
        assert resp.status_code == 204
        mock_delete.assert_called_once_with("cf-token", "zone1", "r1", timeout=10.0)
