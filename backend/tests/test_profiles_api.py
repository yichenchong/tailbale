"""Tests for the app profiles API endpoints."""

from app.routers.profiles import detect_profile


class TestDetectProfile:
    def test_detect_nextcloud(self):
        assert detect_profile("linuxserver/nextcloud:latest") == "nextcloud"

    def test_detect_jellyfin(self):
        assert detect_profile("jellyfin/jellyfin:10.8") == "jellyfin"

    def test_detect_immich(self):
        assert detect_profile("ghcr.io/immich-app/immich-server:release") == "immich"

    def test_detect_calibre_web(self):
        assert detect_profile("linuxserver/calibre-web") == "calibre-web"

    def test_detect_home_assistant(self):
        assert detect_profile("ghcr.io/home-assistant/home-assistant:stable") == "home-assistant"

    def test_detect_vaultwarden(self):
        assert detect_profile("vaultwarden/server:latest") == "vaultwarden"

    def test_no_match(self):
        assert detect_profile("nginx:latest") is None

    def test_no_match_empty(self):
        assert detect_profile("") is None


class TestProfilesAPI:
    def test_list_profiles(self, client):
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        profiles = resp.json()["profiles"]
        assert "nextcloud" in profiles
        assert "generic" in profiles
        assert profiles["nextcloud"]["recommended_port"] == 80

    def test_detect_endpoint_match(self, client):
        resp = client.get("/api/profiles/detect?image=linuxserver/nextcloud:28")
        assert resp.status_code == 200
        data = resp.json()
        assert data["detected_profile"] == "nextcloud"
        assert data["profile"]["name"] == "Nextcloud"
        assert data["profile"]["post_setup_reminder"] is not None

    def test_detect_endpoint_no_match(self, client):
        resp = client.get("/api/profiles/detect?image=nginx:latest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["detected_profile"] is None
        assert data["profile"] is None
