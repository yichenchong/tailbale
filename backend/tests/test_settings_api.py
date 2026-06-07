"""Tests for the Settings API endpoints."""

from app.secrets import CLOUDFLARE_TOKEN, TAILSCALE_API_KEY, TAILSCALE_AUTH_KEY, read_secret, secret_exists


class TestGetSettings:
    def test_returns_defaults(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()

        assert data["general"]["base_domain"] == "example.com"
        assert data["general"]["acme_email"] == "you@example.com"
        assert data["general"]["reconcile_interval_seconds"] == 60
        assert data["general"]["cert_renewal_window_days"] == 30
        assert data["cloudflare"]["zone_id"] == ""
        assert data["cloudflare"]["token_configured"] is False
        assert data["tailscale"]["auth_key_configured"] is False
        assert data["tailscale"]["api_key_configured"] is False
        assert data["tailscale"]["control_url"] == "https://controlplane.tailscale.com"
        assert data["docker"]["socket_path"] == "unix:///var/run/docker.sock"
        assert data["general"]["developer_mode"] is False
        assert data["setup_complete"] is False


class TestUpdateGeneral:
    def test_update_base_domain(self, client):
        resp = client.put("/api/settings/general", json={"base_domain": "mysite.com"})
        assert resp.status_code == 200
        assert resp.json()["general"]["base_domain"] == "mysite.com"

    def test_update_multiple_fields(self, client):
        resp = client.put("/api/settings/general", json={
            "base_domain": "new.com",
            "acme_email": "admin@new.com",
            "reconcile_interval_seconds": 120,
            "cert_renewal_window_days": 14,
        })
        assert resp.status_code == 200
        data = resp.json()["general"]
        assert data["base_domain"] == "new.com"
        assert data["acme_email"] == "admin@new.com"
        assert data["reconcile_interval_seconds"] == 120
        assert data["cert_renewal_window_days"] == 14

    def test_partial_update_preserves_others(self, client):
        # Set initial values
        client.put("/api/settings/general", json={
            "base_domain": "first.com",
            "acme_email": "admin@first.com",
        })
        # Update only one field
        resp = client.put("/api/settings/general", json={"acme_email": "new@first.com"})
        data = resp.json()["general"]
        assert data["base_domain"] == "first.com"
        assert data["acme_email"] == "new@first.com"

    def test_null_fields_are_ignored(self, client):
        client.put("/api/settings/general", json={"base_domain": "keep.com"})
        resp = client.put("/api/settings/general", json={"base_domain": None})
        assert resp.json()["general"]["base_domain"] == "keep.com"


    def test_update_developer_mode(self, client):
        resp = client.put("/api/settings/general", json={"developer_mode": True})
        assert resp.status_code == 200
        assert resp.json()["general"]["developer_mode"] is True

class TestUpdateCloudflare:
    def test_update_zone_id(self, client):
        resp = client.put("/api/settings/cloudflare", json={"zone_id": "zone123"})
        assert resp.status_code == 200
        assert resp.json()["cloudflare"]["zone_id"] == "zone123"

    def test_update_token_writes_secret(self, client, tmp_data_dir):
        resp = client.put("/api/settings/cloudflare", json={"token": "cf_token_value"})
        assert resp.status_code == 200
        assert resp.json()["cloudflare"]["token_configured"] is True
        # Verify secret was written
        assert secret_exists(CLOUDFLARE_TOKEN)
        assert read_secret(CLOUDFLARE_TOKEN) == "cf_token_value"

    def test_token_never_returned(self, client, tmp_data_dir):
        client.put("/api/settings/cloudflare", json={"token": "secret_value"})
        resp = client.get("/api/settings")
        cf = resp.json()["cloudflare"]
        assert "token" not in cf or cf.get("token") is None
        assert cf["token_configured"] is True


class TestUpdateTailscale:
    def test_update_auth_key(self, client, tmp_data_dir):
        resp = client.put("/api/settings/tailscale", json={"auth_key": "tskey-auth-abc123"})
        assert resp.status_code == 200
        assert resp.json()["tailscale"]["auth_key_configured"] is True
        assert read_secret(TAILSCALE_AUTH_KEY) == "tskey-auth-abc123"

    def test_update_control_url(self, client):
        resp = client.put("/api/settings/tailscale", json={
            "control_url": "https://headscale.myserver.com"
        })
        assert resp.json()["tailscale"]["control_url"] == "https://headscale.myserver.com"

    def test_update_hostname_prefix(self, client):
        resp = client.put("/api/settings/tailscale", json={
            "default_ts_hostname_prefix": "myedge"
        })
        assert resp.json()["tailscale"]["default_ts_hostname_prefix"] == "myedge"

    def test_update_api_key(self, client, tmp_data_dir):
        resp = client.put("/api/settings/tailscale", json={"api_key": "tskey-api-abc123"})
        assert resp.status_code == 200
        assert resp.json()["tailscale"]["api_key_configured"] is True
        assert read_secret(TAILSCALE_API_KEY) == "tskey-api-abc123"

    def test_rejects_invalid_auth_key(self, client, tmp_data_dir):
        resp = client.put("/api/settings/tailscale", json={"auth_key": "tskey-api-wrong"})
        assert resp.status_code == 400
        assert "auth key" in resp.json()["detail"].lower()

    def test_rejects_invalid_api_key(self, client, tmp_data_dir):
        resp = client.put("/api/settings/tailscale", json={"api_key": "tskey-auth-wrong"})
        assert resp.status_code == 400
        assert "api key" in resp.json()["detail"].lower()


class TestUpdateDocker:
    def test_update_socket_path(self, client):
        resp = client.put("/api/settings/docker", json={
            "socket_path": "tcp://localhost:2375"
        })
        assert resp.status_code == 200
        assert resp.json()["docker"]["socket_path"] == "tcp://localhost:2375"


class TestUpdatePaths:
    def test_update_paths(self, client):
        resp = client.put("/api/settings/paths", json={
            "generated_root": "/custom/generated",
            "cert_root": "/custom/certs",
            "tailscale_state_root": "/custom/ts",
        })
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert paths["generated_root"] == "/custom/generated"
        assert paths["cert_root"] == "/custom/certs"
        assert paths["tailscale_state_root"] == "/custom/ts"


class TestSetupComplete:
    def test_mark_setup_complete_requires_tailscale_keys(self, client):
        resp = client.put("/api/settings/setup-complete")
        assert resp.status_code == 400
        assert "tailscale auth key" in resp.json()["detail"].lower()
        assert "api key" in resp.json()["detail"].lower()

    def test_mark_setup_complete(self, client, tmp_data_dir):
        client.put("/api/settings/tailscale", json={
            "auth_key": "tskey-auth-abc123",
            "api_key": "tskey-api-abc123",
        })

        resp = client.put("/api/settings/setup-complete")
        assert resp.status_code == 200
        assert resp.json()["setup_complete"] is True

        # Verify it persists
        resp = client.get("/api/settings")
        assert resp.json()["setup_complete"] is True


class TestDeveloperActions:
    def test_reset_setup_complete_requires_developer_mode(self, client):
        resp = client.post("/api/settings/developer/reset-setup-complete")
        assert resp.status_code == 403

    def test_reset_setup_complete_only_flips_setting(self, client):
        client.put("/api/settings/general", json={"developer_mode": True})
        client.put("/api/settings/tailscale", json={
            "auth_key": "tskey-auth-abc123",
            "api_key": "tskey-api-abc123",
        })
        client.put("/api/settings/setup-complete")

        resp = client.post("/api/settings/developer/reset-setup-complete")
        assert resp.status_code == 200

        settings = client.get("/api/settings").json()
        assert settings["setup_complete"] is False
        assert settings["general"]["developer_mode"] is True
        assert settings["tailscale"]["auth_key_configured"] is True
        assert settings["tailscale"]["api_key_configured"] is True

    def test_reset_all_clears_services_users_settings_and_secrets(self, client, db_session, tmp_data_dir):
        from app.models.user import User
        from app.secrets import write_secret

        client.put("/api/settings/general", json={"developer_mode": True, "base_domain": "custom.example"})
        client.put("/api/settings/cloudflare", json={"zone_id": "zone123"})
        client.put("/api/settings/tailscale", json={
            "auth_key": "tskey-auth-abc123",
            "api_key": "tskey-api-abc123",
        })
        client.put("/api/settings/setup-complete")
        db_session.add(User(id="usr_reset", username="resetme", password_hash="hash", role="admin"))
        db_session.commit()
        client.post("/api/services", json={
            "name": "Nextcloud",
            "upstream_container_id": "abc123def456",
            "upstream_container_name": "nextcloud",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "nextcloud.example.com",
            "base_domain": "example.com",
        })
        write_secret("cloudflare_token", "cf-token")

        resp = client.post("/api/settings/developer/reset-all")
        assert resp.status_code == 200

        settings = client.get("/api/settings").json()
        assert settings["setup_complete"] is False
        assert settings["general"]["developer_mode"] is False
        assert settings["general"]["base_domain"] == "example.com"
        assert settings["cloudflare"]["token_configured"] is False
        assert settings["tailscale"]["auth_key_configured"] is False
        assert settings["tailscale"]["api_key_configured"] is False

        assert db_session.query(User).count() == 0
        from app.models.service import Service
        assert db_session.query(Service).count() == 0


class TestConnectionTests:
    def test_tailscale_valid_key(self, client, tmp_data_dir):
        from app.secrets import write_secret, TAILSCALE_AUTH_KEY
        write_secret(TAILSCALE_AUTH_KEY, "tskey-auth-abc123def456")

        resp = client.post("/api/settings/test/tailscale")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_tailscale_invalid_key(self, client, tmp_data_dir):
        from app.secrets import write_secret, TAILSCALE_AUTH_KEY
        write_secret(TAILSCALE_AUTH_KEY, "not-a-valid-key")

        resp = client.post("/api/settings/test/tailscale")
        data = resp.json()
        assert data["success"] is False

    def test_tailscale_no_key(self, client, tmp_data_dir):
        resp = client.post("/api/settings/test/tailscale")
        data = resp.json()
        assert data["success"] is False
        assert "not configured" in data["message"]

    def test_cloudflare_no_token(self, client, tmp_data_dir):
        resp = client.post("/api/settings/test/cloudflare")
        data = resp.json()
        assert data["success"] is False
        assert "token not configured" in data["message"]

    def test_cloudflare_no_zone(self, client, tmp_data_dir):
        from app.secrets import write_secret, CLOUDFLARE_TOKEN
        write_secret(CLOUDFLARE_TOKEN, "cf_token_123")

        resp = client.post("/api/settings/test/cloudflare")
        data = resp.json()
        assert data["success"] is False
        assert "zone ID not configured" in data["message"]

    def test_docker_connection_failure(self, client):
        # Update to an invalid socket to force failure
        client.put("/api/settings/docker", json={"socket_path": "tcp://invalid:9999"})
        resp = client.post("/api/settings/test/docker")
        data = resp.json()
        assert data["success"] is False


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
