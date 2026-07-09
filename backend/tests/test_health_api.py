"""Full health-check API tests."""

from unittest.mock import patch

from app.settings_store import set_setting

from ._services_helpers import _create_service as create_service_via_api


class TestFullHealthCheck:
    @patch("app.services.edge_ops.health_checker.run_health_checks")
    @patch("app.secrets.read_secret", return_value=None)
    def test_full_health_check_without_cloudflare(self, _mock_secret, mock_checks, client):
        mock_checks.return_value = {"edge_container_present": True}
        service_id = create_service_via_api(client).json()["id"]

        response = client.post(f"/api/services/{service_id}/health-check-full")

        assert response.status_code == 200
        data = response.json()
        assert "checks" in data
        assert "extended" in data
        assert data["extended"]["cf_error"]

    def test_full_health_check_404_for_missing(self, client):
        response = client.post("/api/services/svc_nonexistent/health-check-full")

        assert response.status_code == 404

    @patch("app.services.edge_ops.health_checker.run_health_checks")
    @patch("app.secrets.read_secret", return_value="cf-token-123")
    @patch("app.adapters.cloudflare_adapter.find_record")
    def test_health_check_full_calls_cloudflare_adapter(
        self,
        mock_find_record,
        _mock_secret,
        mock_checks,
        client,
        db_session,
    ):
        mock_checks.return_value = {"edge_container_present": True}
        mock_find_record.return_value = {"content": "100.64.0.1"}
        set_setting(db_session, "cf_zone_id", "zone123")
        db_session.commit()
        service_id = create_service_via_api(client).json()["id"]

        response = client.post(f"/api/services/{service_id}/health-check-full")

        assert response.status_code == 200
        data = response.json()
        assert data["extended"]["cf_record_exists"] is True
