"""Tests for the Settings API endpoints."""

from app.secrets import CLOUDFLARE_TOKEN, TAILSCALE_AUTH_KEY, read_secret, secret_exists


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
        assert data["tailscale"]["control_url"] == "https://controlplane.tailscale.com"
        assert data["docker"]["socket_path"] == "unix:///var/run/docker.sock"
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
    def test_mark_setup_complete(self, client):
        resp = client.put("/api/settings/setup-complete")
        assert resp.status_code == 200
        assert resp.json()["setup_complete"] is True

        # Verify it persists
        resp = client.get("/api/settings")
        assert resp.json()["setup_complete"] is True


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
