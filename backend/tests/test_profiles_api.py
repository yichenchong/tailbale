"""Tests for the app profiles API endpoints."""

from app.profiles import detect_profile


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

    def test_detection_ignores_registry_host(self):
        assert detect_profile("registry.nextcloud.example.com/team/nginx:latest") is None

    def test_detection_does_not_substring_match_unrelated_component(self):
        assert detect_profile("ghcr.io/notnextcloud/postgres:latest") is None

    def test_detection_handles_digest_reference(self):
        assert detect_profile("ghcr.io/immich-app/immich-server@sha256:abc123") == "immich"

    def test_no_match(self):
        assert detect_profile("nginx:latest") is None

    def test_no_match_empty(self):
        assert detect_profile("") is None

    def test_detection_matches_hyphenated_suffix_component(self):
        # A '-<pattern>' suffix component matches (e.g. a forked/customized
        # image like "<vendor>/custom-nextcloud"). This exercises the
        # endswith("-<pattern>") arm of _repository_component_matches, distinct
        # from the exact-match and startswith("<pattern>-") arms above.
        assert detect_profile("myorg/custom-nextcloud:latest") == "nextcloud"


class TestProfilesAPI:
    def test_list_profiles(self, client):
        resp = client.get("/api/profiles")
        assert resp.status_code == 200
        profiles = resp.json()["profiles"]
        assert "nextcloud" in profiles
        assert "generic" in profiles
        assert profiles["nextcloud"]["recommended_port"] == 80
        assert profiles["nextcloud"]["image_patterns"] == ["nextcloud"]

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
