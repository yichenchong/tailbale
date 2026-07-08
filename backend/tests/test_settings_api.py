"""Tests for the Settings API endpoints."""

import pytest

from app.secrets import (
    CLOUDFLARE_TOKEN,
    TAILSCALE_API_KEY,
    TAILSCALE_AUTH_KEY,
    read_secret,
)


def _configure_setup_prerequisites(client):
    client.post("/api/auth/setup-user", json={
        "username": "admin",
        "password": "securepassword123",
    })
    client.put("/api/settings/general", json={
        "base_domain": "example.com",
        "acme_email": "admin@example.com",
    })
    client.put("/api/settings/cloudflare", json={
        "zone_id": "zone123",
        "token": "cf-token",
    })
    client.put("/api/settings/tailscale", json={
        "auth_key": "tskey-auth-abc123",
        "api_key": "tskey-api-abc123",
    })
    client.put("/api/settings/docker", json={
        "socket_path": "unix:///var/run/docker.sock",
    })


class TestGetSettings:
    def test_returns_defaults(self, client):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()

        assert data["general"]["base_domain"] == "example.com"
        assert data["general"]["acme_email"] == "you@example.com"
        assert data["general"]["reconcile_interval_seconds"] == 3600
        assert data["general"]["cert_renewal_window_days"] == 30
        assert data["general"]["health_check_interval_seconds"] == 60
        assert data["general"]["event_retention_days"] == 30
        assert data["cloudflare"]["zone_id"] == ""
        assert data["cloudflare"]["token_configured"] is False
        assert data["tailscale"]["auth_key_configured"] is False
        assert data["tailscale"]["api_key_configured"] is False
        assert data["tailscale"]["control_url"] == "https://controlplane.tailscale.com"
        assert data["docker"]["socket_path"] == "unix:///var/run/docker.sock"
        assert data["general"]["developer_mode"] is False
        assert data["setup_complete"] is False

    def test_stale_invalid_numeric_settings_fail_loud(self, client, db_session):
        # Writes enforce ge=1, so a corrupt stored interval is data corruption
        # that could never have been written through the API. The settings
        # endpoint surfaces it loudly instead of masking it with a default.
        from app.settings_store import set_setting

        set_setting(db_session, "reconcile_interval_seconds", "not-an-int")
        db_session.commit()

        with pytest.raises(ValueError):
            client.get("/api/settings")


class TestUpdateGeneral:
    def test_update_base_domain(self, client):
        resp = client.put("/api/settings/general", json={"base_domain": "mysite.com"})
        assert resp.status_code == 200
        assert resp.json()["general"]["base_domain"] == "mysite.com"

    def test_base_domain_normalized_to_lowercase(self, client):
        resp = client.put("/api/settings/general", json={"base_domain": "Example.COM"})
        assert resp.status_code == 200
        assert resp.json()["general"]["base_domain"] == "example.com"

    def test_rejects_invalid_base_domain(self, client):
        resp = client.put("/api/settings/general", json={"base_domain": "bad domain.com"})
        assert resp.status_code == 422

        settings = client.get("/api/settings").json()
        assert settings["general"]["base_domain"] == "example.com"

    def test_rejects_base_domain_change_when_services_exist(self, client):
        created = client.post("/api/services", json={
            "name": "Existing",
            "upstream_container_id": "abc123",
            "upstream_container_name": "existing",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "existing.example.com",
            "base_domain": "example.com",
        })
        assert created.status_code == 201

        resp = client.put("/api/settings/general", json={"base_domain": "new.example.com"})

        assert resp.status_code == 409
        assert client.get("/api/settings").json()["general"]["base_domain"] == "example.com"

    def test_base_domain_unchanged_is_allowed_when_services_exist(self, client):
        # The guard blocks only an actual change of base_domain. Re-submitting
        # the CURRENT value alongside other general edits (as the frontend does
        # when saving the form) must NOT 409, even with services present.
        created = client.post("/api/services", json={
            "name": "Existing",
            "upstream_container_id": "abc123",
            "upstream_container_name": "existing",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "existing.example.com",
            "base_domain": "example.com",
        })
        assert created.status_code == 201

        resp = client.put("/api/settings/general", json={
            "base_domain": "example.com",  # unchanged (matches current default)
            "acme_email": "admin@example.com",
        })

        assert resp.status_code == 200
        data = resp.json()["general"]
        assert data["base_domain"] == "example.com"
        assert data["acme_email"] == "admin@example.com"

    def test_base_domain_mixed_case_stored_not_flagged_as_change(self, client, db_session):
        # ST-R2-1 regression: the guard must compare base_domain
        # case-insensitively. A deployment predating the normalize_base_domain
        # validator can hold a mixed-case stored value; the frontend reads it
        # back and echoes it on save, the schema lowercases it, and a raw
        # comparison would see a spurious "change" and 409-lock the whole
        # /general section. DNS is case-insensitive, so re-submitting the same
        # domain (differing only in case) MUST 200, even with services present.
        from app.settings_store import set_setting

        created = client.post("/api/services", json={
            "name": "Existing",
            "upstream_container_id": "abc123",
            "upstream_container_name": "existing",
            "upstream_scheme": "http",
            "upstream_port": 80,
            "hostname": "existing.example.com",
            "base_domain": "example.com",
        })
        assert created.status_code == 201

        # Simulate a legacy mixed-case stored base_domain (pre-normalize).
        set_setting(db_session, "base_domain", "Example.COM")
        db_session.commit()

        resp = client.put("/api/settings/general", json={
            "base_domain": "example.com",  # same domain, schema-normalized
            "acme_email": "admin@example.com",
        })

        assert resp.status_code == 200
        data = resp.json()["general"]
        assert data["base_domain"] == "example.com"
        assert data["acme_email"] == "admin@example.com"

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


    def test_rejects_non_positive_intervals(self, client):
        resp = client.put("/api/settings/general", json={"reconcile_interval_seconds": 0})
        assert resp.status_code == 422

        resp = client.put("/api/settings/general", json={"cert_renewal_window_days": -1})
        assert resp.status_code == 422

        settings = client.get("/api/settings").json()
        assert settings["general"]["reconcile_interval_seconds"] == 3600
        assert settings["general"]["cert_renewal_window_days"] == 30

    @pytest.mark.parametrize("above_bound", [10001, 9_999_999])
    def test_rejects_cert_renewal_window_above_upper_bound(self, client, above_bound):
        # A cert_renewal_window_days larger than the schema ceiling (le=10000)
        # would feed timedelta(days=window) toward its OverflowError limit and
        # 500 the manual /renew-cert path, so the write is rejected up front.
        resp = client.put(
            "/api/settings/general", json={"cert_renewal_window_days": above_bound}
        )
        assert resp.status_code == 422

        # The out-of-range value is never persisted; the default is preserved.
        settings = client.get("/api/settings").json()
        assert settings["general"]["cert_renewal_window_days"] == 30

    @pytest.mark.parametrize("in_range", [30, 10000])
    def test_accepts_cert_renewal_window_within_bounds(self, client, in_range):
        # The inclusive upper bound (10000) and an ordinary value must still be
        # accepted and persisted — the new ceiling must not reject legal windows.
        resp = client.put(
            "/api/settings/general", json={"cert_renewal_window_days": in_range}
        )
        assert resp.status_code == 200
        assert resp.json()["general"]["cert_renewal_window_days"] == in_range

    def test_rejects_empty_general_strings(self, client):
        resp = client.put("/api/settings/general", json={"base_domain": ""})
        assert resp.status_code == 422

        resp = client.put("/api/settings/general", json={"timezone": "   "})
        assert resp.status_code == 422

        settings = client.get("/api/settings").json()
        assert settings["general"]["base_domain"] == "example.com"
        assert settings["general"]["timezone"] == "UTC"

    def test_update_new_int_settings(self, client):
        resp = client.put(
            "/api/settings/general",
            json={
                "reconcile_interval_seconds": 7200,
                "health_check_interval_seconds": 30,
                "event_retention_days": 14,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["general"]
        assert data["reconcile_interval_seconds"] == 7200
        assert data["health_check_interval_seconds"] == 30
        assert data["event_retention_days"] == 14

    @pytest.mark.parametrize(
        "field",
        [
            "reconcile_interval_seconds",
            "health_check_interval_seconds",
            "cert_renewal_window_days",
            "event_retention_days",
        ],
    )
    @pytest.mark.parametrize("bad", [0, -1, 1.5, "", "   "])
    def test_rejects_invalid_int_settings_at_write_time(self, client, field, bad):
        # Pydantic ge=1 + int typing rejects 0/negative/fractional/blank before
        # the handler runs, so a bad value is never persisted as a string.
        resp = client.put("/api/settings/general", json={field: bad})
        assert resp.status_code == 422

    @pytest.mark.parametrize("bad", ["notanemail", "a@b", "a b@c.com"])
    def test_rejects_invalid_acme_email(self, client, bad):
        # No '@', no dot/TLD, and embedded whitespace are obvious mistakes.
        resp = client.put("/api/settings/general", json={"acme_email": bad})
        assert resp.status_code == 422

        # The bad value is never persisted; the default is preserved.
        settings = client.get("/api/settings").json()
        assert settings["general"]["acme_email"] == "you@example.com"

    @pytest.mark.parametrize("good", ["admin@example.com", "a.b+tag@sub.example.co.uk"])
    def test_accepts_valid_acme_email(self, client, good):
        resp = client.put("/api/settings/general", json={"acme_email": good})
        assert resp.status_code == 200
        assert resp.json()["general"]["acme_email"] == good

    def test_acme_email_whitespace_is_stripped_before_storage(self, client):
        # The shared before-validator trims surrounding whitespace, so a padded
        # but otherwise valid address is accepted and persisted in trimmed form
        # (the lenient email regex would reject the untrimmed value otherwise).
        resp = client.put(
            "/api/settings/general", json={"acme_email": "  admin@example.com  "}
        )
        assert resp.status_code == 200
        assert resp.json()["general"]["acme_email"] == "admin@example.com"

        settings = client.get("/api/settings").json()
        assert settings["general"]["acme_email"] == "admin@example.com"

    def test_acme_email_whitespace_only_is_rejected(self, client):
        # Trims to empty -> fails the min_length=1 constraint, never persisted.
        resp = client.put("/api/settings/general", json={"acme_email": "   "})
        assert resp.status_code == 422
        assert client.get("/api/settings").json()["general"]["acme_email"] == "you@example.com"

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
        assert read_secret(CLOUDFLARE_TOKEN) == "cf_token_value"

    def test_token_never_returned(self, client, tmp_data_dir):
        client.put("/api/settings/cloudflare", json={"token": "secret_value"})
        resp = client.get("/api/settings")
        cf = resp.json()["cloudflare"]
        assert "token" not in cf or cf.get("token") is None
        assert cf["token_configured"] is True

    def test_rejects_empty_token(self, client):
        resp = client.put("/api/settings/cloudflare", json={"token": "   "})
        assert resp.status_code == 422

        settings = client.get("/api/settings").json()
        assert settings["cloudflare"]["token_configured"] is False

    def test_failed_db_write_leaves_no_orphaned_secret(self, client, tmp_data_dir, monkeypatch):
        """The token is persisted only after the DB write commits. A failing DB
        write must leave no orphaned secret on disk (a retry re-applies both)."""
        import app.routers.settings as settings_router

        def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(settings_router.settings_store, "set_setting", _boom)
        with pytest.raises(RuntimeError):
            client.put(
                "/api/settings/cloudflare",
                json={"zone_id": "zone123", "token": "cf_token_value"},
            )
        assert read_secret(CLOUDFLARE_TOKEN) is None



class TestUpdateTailscale:
    def test_update_auth_key(self, client, tmp_data_dir):
        resp = client.put("/api/settings/tailscale", json={"auth_key": "  tskey-auth-abc123\n"})
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
        resp = client.put("/api/settings/tailscale", json={"api_key": "\ntskey-api-abc123  "})
        assert resp.status_code == 200
        assert resp.json()["tailscale"]["api_key_configured"] is True
        assert read_secret(TAILSCALE_API_KEY) == "tskey-api-abc123"

    def test_rejects_invalid_auth_key(self, client, tmp_data_dir):
        resp = client.put("/api/settings/tailscale", json={"auth_key": "tskey-api-wrong"})
        assert resp.status_code == 400
        assert "auth key" in resp.json()["detail"].lower()

    def test_rejects_dead_reusable_prefix(self, client, tmp_data_dir):
        # 'tskey-reusable-' is not a real prefix; reusable keys use 'tskey-auth-'.
        resp = client.put("/api/settings/tailscale", json={"auth_key": "tskey-reusable-abc"})
        assert resp.status_code == 400
        assert "auth key" in resp.json()["detail"].lower()
        assert read_secret(TAILSCALE_AUTH_KEY) is None

    def test_rejects_invalid_api_key(self, client, tmp_data_dir):
        resp = client.put("/api/settings/tailscale", json={"api_key": "tskey-auth-wrong"})
        assert resp.status_code == 400
        assert "api key" in resp.json()["detail"].lower()

    def test_rejects_empty_auth_and_api_keys(self, client, tmp_data_dir):
        resp = client.put("/api/settings/tailscale", json={"auth_key": "   "})
        assert resp.status_code == 422

        resp = client.put("/api/settings/tailscale", json={"api_key": "\n\t"})
        assert resp.status_code == 422

        settings = client.get("/api/settings").json()
        assert settings["tailscale"]["auth_key_configured"] is False
        assert settings["tailscale"]["api_key_configured"] is False

    def test_failed_db_write_leaves_no_orphaned_secret(self, client, tmp_data_dir, monkeypatch):
        """Auth/API keys are persisted only after the DB write commits. A failing
        DB write must leave no orphaned secret on disk (a retry re-applies both)."""
        import app.routers.settings as settings_router

        def _boom(*args, **kwargs):
            raise RuntimeError("db down")

        monkeypatch.setattr(settings_router.settings_store, "set_setting", _boom)
        with pytest.raises(RuntimeError):
            client.put(
                "/api/settings/tailscale",
                json={
                    "auth_key": "tskey-auth-abc123",
                    "control_url": "https://headscale.example.com",
                },
            )
        assert read_secret(TAILSCALE_AUTH_KEY) is None


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

    def test_clear_paths_restores_default_sentinels(self, client):
        resp = client.put("/api/settings/paths", json={
            "generated_root": "/custom/generated",
            "cert_root": "/custom/certs",
            "tailscale_state_root": "/custom/ts",
        })
        assert resp.status_code == 200

        resp = client.put("/api/settings/paths", json={
            "generated_root": "",
            "cert_root": "   ",
            "tailscale_state_root": "\n\t",
        })
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert paths["generated_root"] == ""
        assert paths["cert_root"] == ""
        assert paths["tailscale_state_root"] == ""


class TestSetupComplete:
    def test_mark_setup_complete_requires_all_prerequisites(self, client):
        resp = client.put("/api/settings/setup-complete")
        assert resp.status_code == 400
        detail = resp.json()["detail"].lower()
        assert "user" in detail
        assert "tailscale auth key" in detail
        assert "api key" in detail

    def test_mark_setup_complete_rejects_blank_secret_files(self, client, tmp_data_dir):
        from app.secrets import write_secret

        write_secret(TAILSCALE_AUTH_KEY, "   ")
        write_secret(TAILSCALE_API_KEY, "tskey-api-abc123")

        resp = client.put("/api/settings/setup-complete")
        assert resp.status_code == 400

        settings = client.get("/api/settings").json()
        assert settings["setup_complete"] is False
        assert settings["tailscale"]["auth_key_configured"] is False
        assert settings["tailscale"]["api_key_configured"] is True

    def test_mark_setup_complete(self, client, tmp_data_dir):
        _configure_setup_prerequisites(client)

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

    def test_reset_all_requires_developer_mode(self, client):
        # /developer/* must be gated even for the destructive reset-all route.
        resp = client.post("/api/settings/developer/reset-all")
        assert resp.status_code == 403

    def test_reset_setup_complete_only_flips_setting(self, client):
        _configure_setup_prerequisites(client)
        client.put("/api/settings/general", json={"developer_mode": True})
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

        _configure_setup_prerequisites(client)
        client.put("/api/settings/general", json={"developer_mode": True, "base_domain": "custom.example"})
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


    def test_reset_all_clears_dns_orphan_cleanup_jobs(self, client, db_session):
        from app.models.job import Job

        client.put("/api/settings/general", json={"developer_mode": True})
        db_session.add(
            Job(
                kind="dns_orphan_cleanup",
                status="pending",
                message="Orphaned DNS record",
                details={
                    "record_id": "cf_rec_reset",
                    "hostname": "app.example.com",
                    "zone_id": "zone123",
                },
            )
        )
        db_session.add(Job(kind="old_setup_job", status="pending"))
        db_session.commit()

        resp = client.post("/api/settings/developer/reset-all")
        assert resp.status_code == 200

        db_session.expire_all()
        assert db_session.query(Job).count() == 0

    def test_main_logs_requires_developer_mode(self, client):
        resp = client.get("/api/settings/developer/main-logs")
        assert resp.status_code == 403

    def test_main_logs_returns_tailbale_container_logs(self, client, monkeypatch):
        client.put("/api/settings/general", json={"developer_mode": True})

        class FakeContainer:
            name = "tailbale"

            def logs(self, stdout=True, stderr=True, tail=200, timestamps=False):
                assert stdout is True
                assert stderr is True
                assert tail == 25
                assert timestamps is True
                return b"2026-06-09 line one\nline two\n"

        class FakeContainers:
            def list(self, all=True, filters=None):
                assert all is True
                assert filters == {"label": "tailbale.main=true"}
                return [FakeContainer()]

        class FakeDockerClient:
            def __init__(self, base_url):
                assert base_url == "unix:///var/run/docker.sock"
                self.containers = FakeContainers()

            def close(self):
                pass

        monkeypatch.setattr("app.routers.settings.docker.DockerClient", FakeDockerClient)

        resp = client.get("/api/settings/developer/main-logs?tail=25")
        assert resp.status_code == 200
        data = resp.json()
        assert data["container"] == "tailbale"
        assert "line one" in data["logs"]

    def test_main_logs_falls_back_to_known_container_name_and_closes_client(
        self, client, monkeypatch
    ):
        from app.routers.settings import docker

        client.put("/api/settings/general", json={"developer_mode": True})
        state = {"closed": False, "lookups": []}

        class FakeContainer:
            name = "backend"

            def logs(self, stdout=True, stderr=True, tail=200, timestamps=False):
                assert timestamps is True
                return "fallback logs"

        class FakeContainers:
            def list(self, all=True, filters=None):
                return []

            def get(self, name):
                state["lookups"].append(name)
                if name == "backend":
                    return FakeContainer()
                raise docker.errors.NotFound("not found")

        class FakeDockerClient:
            def __init__(self, base_url):
                self.containers = FakeContainers()

            def close(self):
                state["closed"] = True

        monkeypatch.setattr("app.routers.settings.docker.DockerClient", FakeDockerClient)

        resp = client.get("/api/settings/developer/main-logs")

        assert resp.status_code == 200
        assert resp.json()["container"] == "backend"
        assert state["lookups"] == ["tailbale", "backend"]
        assert state["closed"] is True

    def test_main_logs_rejects_invalid_tail_before_docker_lookup(
        self, client, monkeypatch
    ):
        client.put("/api/settings/general", json={"developer_mode": True})

        def fail_if_called(base_url):
            raise AssertionError("Docker client should not be created for invalid tail")

        monkeypatch.setattr("app.routers.settings.docker.DockerClient", fail_if_called)

        resp = client.get("/api/settings/developer/main-logs?tail=0")
        assert resp.status_code == 422

    def test_main_logs_404_when_no_container_found(self, client, monkeypatch):
        # No labeled container and every fallback name misses -> the endpoint
        # surfaces a 404 (not a 502): the container genuinely does not exist.
        from app.routers.settings import docker

        client.put("/api/settings/general", json={"developer_mode": True})

        class FakeContainers:
            def list(self, all=True, filters=None):
                return []

            def get(self, name):
                raise docker.errors.NotFound("no such container")

        class FakeDockerClient:
            def __init__(self, base_url):
                self.containers = FakeContainers()

            def close(self):
                pass

        monkeypatch.setattr("app.routers.settings.docker.DockerClient", FakeDockerClient)

        resp = client.get("/api/settings/developer/main-logs")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_main_logs_502_when_log_read_fails(self, client, monkeypatch):
        # The container is found but reading its logs raises (daemon hiccup): the
        # failure must map to 502 with a descriptive message, not a bare 500.
        client.put("/api/settings/general", json={"developer_mode": True})

        class FakeContainer:
            name = "tailbale"

            def logs(self, stdout=True, stderr=True, tail=200, timestamps=False):
                raise RuntimeError("docker log stream broke")

        class FakeContainers:
            def list(self, all=True, filters=None):
                return [FakeContainer()]

        class FakeDockerClient:
            def __init__(self, base_url):
                self.containers = FakeContainers()

            def close(self):
                pass

        monkeypatch.setattr("app.routers.settings.docker.DockerClient", FakeDockerClient)

        resp = client.get("/api/settings/developer/main-logs")
        assert resp.status_code == 502
        detail = resp.json()["detail"]
        assert "could not read" in detail.lower()
        # AR-R3-2: the underlying str(exc) is logged server-side, not leaked to
        # the client-facing detail.
        assert "docker log stream broke" not in detail

class TestConnectionTests:
    def test_tailscale_valid_key(self, client, tmp_data_dir):
        from app.secrets import TAILSCALE_AUTH_KEY, write_secret
        write_secret(TAILSCALE_AUTH_KEY, "tskey-auth-abc123def456")

        resp = client.post("/api/settings/test/tailscale")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_tailscale_invalid_key(self, client, tmp_data_dir):
        from app.secrets import TAILSCALE_AUTH_KEY, write_secret
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
        from app.secrets import CLOUDFLARE_TOKEN, write_secret
        write_secret(CLOUDFLARE_TOKEN, "cf_token_123")

        resp = client.post("/api/settings/test/cloudflare")
        data = resp.json()
        assert data["success"] is False
        assert "zone ID not configured" in data["message"]

    def test_docker_connection_success_closes_client(self, client, monkeypatch):
        state = {"closed": False}

        class FakeDockerClient:
            def __init__(self, base_url):
                assert base_url == "unix:///var/run/docker.sock"

            def ping(self):
                pass

            def info(self):
                return {"ServerVersion": "25.0.0"}

            def close(self):
                state["closed"] = True

        monkeypatch.setattr("app.routers.settings.docker.DockerClient", FakeDockerClient)

        resp = client.post("/api/settings/test/docker")
        data = resp.json()

        assert resp.status_code == 200
        assert data == {"success": True, "message": "Connected to Docker 25.0.0"}
        assert state["closed"] is True

    def test_docker_uses_from_env_when_socket_path_blank(self, client, monkeypatch):
        # When the operator clears docker_socket_path (to rely on DOCKER_HOST),
        # the client must fall back to from_env() rather than
        # DockerClient(base_url="") which ignores DOCKER_HOST. Mirrors the
        # services/discovery socket-resolution convention.
        import app.edge.docker_client as dc_mod

        orig_get_setting = dc_mod.get_setting
        monkeypatch.setattr(
            dc_mod,
            "get_setting",
            lambda db, key, *a, **k: ""
            if key == "docker_socket_path"
            else orig_get_setting(db, key, *a, **k),
        )

        calls = {"base_url": False, "from_env": False}

        class FakeDockerClient:
            def __init__(self, base_url=None):
                calls["base_url"] = True

            @classmethod
            def from_env(cls):
                calls["from_env"] = True
                return cls.__new__(cls)

            def ping(self):
                pass

            def info(self):
                return {"ServerVersion": "27.0.0"}

            def close(self):
                pass

        monkeypatch.setattr("app.routers.settings.docker.DockerClient", FakeDockerClient)

        resp = client.post("/api/settings/test/docker")

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert calls["from_env"] is True
        assert calls["base_url"] is False

    def test_docker_connection_failure(self, client):
        # Update to an invalid socket to force failure
        client.put("/api/settings/docker", json={"socket_path": "tcp://invalid:9999"})
        resp = client.post("/api/settings/test/docker")
        data = resp.json()
        assert data["success"] is False

    @staticmethod
    def _patch_cloudflare(monkeypatch, payload):
        # The endpoint now verifies the zone through the Cloudflare adapter
        # (verify_zone -> _request -> httpx2.get), so mock the adapter's sync GET
        # and let the real response-checking machinery translate the payload.
        class _FakeCFResp:
            status_code = 200
            text = ""

            def json(self):
                return payload

        monkeypatch.setattr(
            "app.adapters.cloudflare_adapter.httpx2.get",
            lambda *a, **k: _FakeCFResp(),
        )

    def _configure_cloudflare(self, client):
        from app.secrets import CLOUDFLARE_TOKEN, write_secret

        write_secret(CLOUDFLARE_TOKEN, "cf-token")
        client.put("/api/settings/cloudflare", json={"zone_id": "zone123"})

    def test_cloudflare_success_returns_zone_name(self, client, tmp_data_dir, monkeypatch):
        self._configure_cloudflare(client)
        self._patch_cloudflare(monkeypatch, {"success": True, "result": {"name": "example.com"}})

        resp = client.post("/api/settings/test/cloudflare")
        data = resp.json()
        assert data["success"] is True
        assert "example.com" in data["message"]

    def test_cloudflare_reports_api_error_message(self, client, tmp_data_dir, monkeypatch):
        self._configure_cloudflare(client)
        self._patch_cloudflare(
            monkeypatch,
            {"success": False, "errors": [{"code": 9109, "message": "Invalid API token"}]},
        )

        resp = client.post("/api/settings/test/cloudflare")
        data = resp.json()
        assert data["success"] is False
        assert data["message"] == "Invalid API token"

    def test_cloudflare_success_without_zone_name_is_not_reported_as_failure(
        self, client, tmp_data_dir, monkeypatch
    ):
        # Regression: a successful API response whose `result` lacks `name` used
        # to raise KeyError, get swallowed, and surface as success=False with the
        # cryptic message "'name'" — falsely reporting a working connection as broken.
        self._configure_cloudflare(client)
        self._patch_cloudflare(monkeypatch, {"success": True, "result": {}})

        resp = client.post("/api/settings/test/cloudflare")
        data = resp.json()
        assert data["success"] is True
        assert data["message"] != "'name'"

    def test_cloudflare_non_dict_response_is_handled(self, client, tmp_data_dir, monkeypatch):
        self._configure_cloudflare(client)
        self._patch_cloudflare(monkeypatch, ["unexpected"])

        resp = client.post("/api/settings/test/cloudflare")
        data = resp.json()
        assert data["success"] is False
        assert "Unexpected" in data["message"]


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
