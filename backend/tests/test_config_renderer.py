"""Tests for edge Caddyfile config renderer."""

from unittest.mock import MagicMock


def _make_service(**overrides):
    """Create a mock Service object with sensible defaults."""
    svc = MagicMock()
    svc.id = overrides.get("id", "svc_abc123")
    svc.name = overrides.get("name", "Nextcloud")
    svc.hostname = overrides.get("hostname", "nextcloud.example.com")
    svc.upstream_container_name = overrides.get("upstream_container_name", "nextcloud")
    svc.upstream_port = overrides.get("upstream_port", 80)
    svc.upstream_scheme = overrides.get("upstream_scheme", "http")
    svc.preserve_host_header = overrides.get("preserve_host_header", True)
    svc.custom_caddy_snippet = overrides.get("custom_caddy_snippet", None)
    return svc


class TestRenderCaddyfile:
    def test_basic_caddyfile(self):
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service()
        result = render_caddyfile(svc)

        assert "auto_https off" in result
        assert "https://nextcloud.example.com" in result
        assert "tls /certs/fullchain.pem /certs/privkey.pem" in result
        assert "reverse_proxy nextcloud:80" in result
        assert "header_up X-Forwarded-Proto https" in result
        assert "header_up X-Real-IP {remote_host}" in result
        assert "X-Forwarded-Host" not in result

    def test_preserve_host_header_enabled(self):
        """When True, Caddy's default behavior preserves the original Host — no override emitted."""
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(preserve_host_header=True)
        result = render_caddyfile(svc)

        assert "header_up Host {upstream_hostport}" not in result

    def test_preserve_host_header_disabled(self):
        """When False, rewrite Host to upstream address so the app sees its own name."""
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(preserve_host_header=False)
        result = render_caddyfile(svc)

        assert "header_up Host {upstream_hostport}" in result

    def test_https_upstream_scheme(self):
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(upstream_scheme="https", upstream_port=443)
        result = render_caddyfile(svc)

        assert "reverse_proxy https://nextcloud:443" in result

    def test_custom_caddy_snippet(self):
        from app.edge.config_renderer import render_caddyfile

        snippet = "header X-Custom true\nlog { output stdout }"
        svc = _make_service(custom_caddy_snippet=snippet)
        result = render_caddyfile(svc)

        assert "header X-Custom true" in result
        assert "log { output stdout }" in result

    def test_no_custom_snippet(self):
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(custom_caddy_snippet=None)
        result = render_caddyfile(svc)

        # Should not have extra empty blocks
        lines = result.strip().splitlines()
        assert lines[-1] == "}"

    def test_custom_port(self):
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(upstream_port=8096)
        result = render_caddyfile(svc)

        assert "reverse_proxy nextcloud:8096" in result

    def test_different_hostname(self):
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(hostname="jellyfin.mydomain.com")
        result = render_caddyfile(svc)

        assert "https://jellyfin.mydomain.com" in result

    def test_deterministic_output(self):
        """Same input should always produce same output."""
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service()
        result1 = render_caddyfile(svc)
        result2 = render_caddyfile(svc)
        assert result1 == result2

    def test_different_container_name(self):
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(upstream_container_name="my-jellyfin-app")
        result = render_caddyfile(svc)

        assert "reverse_proxy my-jellyfin-app:80" in result

    def test_http_scheme_with_port_443_omits_scheme(self):
        """Caddy rejects http:// with port 443. Omitting scheme avoids the conflict."""
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(upstream_scheme="http", upstream_port=443)
        result = render_caddyfile(svc)

        # No scheme prefix — Caddy defaults to HTTP, avoiding the conflict
        assert "reverse_proxy nextcloud:443" in result
        assert "http://nextcloud:443" not in result
        assert "https://nextcloud:443" not in result

    def test_https_scheme_keeps_prefix(self):
        """When upstream is genuinely HTTPS, the scheme prefix is included."""
        from app.edge.config_renderer import render_caddyfile

        svc = _make_service(upstream_scheme="https", upstream_port=8443)
        result = render_caddyfile(svc)

        assert "reverse_proxy https://nextcloud:8443" in result


class TestWriteCaddyfile:
    def test_writes_file(self, tmp_path):
        from app.edge.config_renderer import write_caddyfile

        svc = _make_service(id="svc_test123")
        path = write_caddyfile(svc, tmp_path)

        assert path.exists()
        assert path.name == "Caddyfile"
        assert path.parent.name == "svc_test123"

        content = path.read_text(encoding="utf-8")
        assert "auto_https off" in content
        assert "https://nextcloud.example.com" in content

    def test_creates_service_directory(self, tmp_path):
        from app.edge.config_renderer import write_caddyfile

        svc = _make_service(id="svc_newdir")
        write_caddyfile(svc, tmp_path)

        service_dir = tmp_path / "svc_newdir"
        assert service_dir.is_dir()

    def test_overwrites_existing(self, tmp_path):
        from app.edge.config_renderer import write_caddyfile

        svc = _make_service(id="svc_overwrite")
        path = write_caddyfile(svc, tmp_path)
        original = path.read_text()

        # Change config and rewrite
        svc.upstream_port = 9999
        path2 = write_caddyfile(svc, tmp_path)
        updated = path2.read_text()

        assert path == path2
        assert "9999" in updated
        assert "9999" not in original

    def test_atomic_write_no_tmp_leftover(self, tmp_path):
        from app.edge.config_renderer import write_caddyfile

        svc = _make_service(id="svc_atomic")
        write_caddyfile(svc, tmp_path)

        service_dir = tmp_path / "svc_atomic"
        files = list(service_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "Caddyfile"
        # No .tmp files left behind
        assert not any(f.suffix == ".tmp" for f in files)
