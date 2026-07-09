"""Tests for edge Caddyfile config renderer."""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.edge import config_renderer
from app.edge.config_renderer import render_caddyfile, write_caddyfile


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
    svc.custom_caddy_snippet = overrides.get("custom_caddy_snippet")
    return svc


class TestRenderCaddyfile:
    def test_basic_caddyfile(self):

        svc = _make_service()
        result = render_caddyfile(svc)

        assert "auto_https off" in result
        assert "https://nextcloud.example.com" in result
        assert "tls /certs/current/fullchain.pem /certs/current/privkey.pem" in result
        assert "tls /certs/fullchain.pem" not in result
        assert "reverse_proxy nextcloud:80" in result
        assert "header_up X-Forwarded-Proto https" in result
        assert "header_up X-Real-IP {remote_host}" in result
        assert "X-Forwarded-Host" not in result

    def test_preserve_host_header_enabled(self):
        """When True, Caddy's default behavior preserves the original Host — no override emitted."""

        svc = _make_service(preserve_host_header=True)
        result = render_caddyfile(svc)

        assert "header_up Host {upstream_hostport}" not in result

    def test_preserve_host_header_disabled(self):
        """When False, rewrite Host to upstream address so the app sees its own name."""

        svc = _make_service(preserve_host_header=False)
        result = render_caddyfile(svc)

        assert "header_up Host {upstream_hostport}" in result

    def test_https_upstream_scheme(self):

        svc = _make_service(upstream_scheme="https", upstream_port=443)
        result = render_caddyfile(svc)

        assert "reverse_proxy https://nextcloud:443" in result

    def test_custom_caddy_snippet(self):

        snippet = "header X-Custom true\nlog { output stdout }"
        svc = _make_service(custom_caddy_snippet=snippet)
        result = render_caddyfile(svc)

        assert "header X-Custom true" in result
        assert "log { output stdout }" in result

    def test_whitespace_only_custom_snippet(self):
        """Document current behavior: a whitespace-only snippet is still truthy,
        so render_snippet_block runs but yields only a newline — no directives
        are emitted. The output gains blank padding but still closes correctly."""

        none = render_caddyfile(_make_service(custom_caddy_snippet=None))
        ws = render_caddyfile(_make_service(custom_caddy_snippet="   \n  \t "))

        # No snippet content leaks; only blank padding distinguishes the two.
        assert ws.endswith("\t}\n\n\n}\n")
        assert none.endswith("\t}\n}\n")
        assert ws == none[:-2] + "\n\n}\n"

    def test_no_custom_snippet(self):

        svc = _make_service(custom_caddy_snippet=None)
        result = render_caddyfile(svc)

        # Should not have extra empty blocks
        lines = result.strip().splitlines()
        assert lines[-1] == "}"

    def test_custom_port(self):

        svc = _make_service(upstream_port=8096)
        result = render_caddyfile(svc)

        assert "reverse_proxy nextcloud:8096" in result

    def test_different_hostname(self):

        svc = _make_service(hostname="jellyfin.mydomain.com")
        result = render_caddyfile(svc)

        assert "https://jellyfin.mydomain.com" in result

    def test_deterministic_output(self):
        """Same input should always produce same output."""

        svc = _make_service()
        result1 = render_caddyfile(svc)
        result2 = render_caddyfile(svc)
        assert result1 == result2

    def test_different_container_name(self):

        svc = _make_service(upstream_container_name="my-jellyfin-app")
        result = render_caddyfile(svc)

        assert "reverse_proxy my-jellyfin-app:80" in result

    def test_http_scheme_with_port_443_omits_scheme(self):
        """Caddy rejects http:// with port 443. Omitting scheme avoids the conflict."""

        svc = _make_service(upstream_scheme="http", upstream_port=443)
        result = render_caddyfile(svc)

        # No scheme prefix — Caddy defaults to HTTP, avoiding the conflict
        assert "reverse_proxy nextcloud:443" in result
        assert "http://nextcloud:443" not in result
        assert "https://nextcloud:443" not in result

    def test_uses_tab_indentation(self):
        """Caddy expects tab indentation (caddy fmt standard)."""

        svc = _make_service()
        result = render_caddyfile(svc)

        # Should use tabs, not spaces, for indentation
        assert "\tauto_https off" in result
        assert "\ttls /certs/current/fullchain.pem" in result
        assert "\treverse_proxy " in result
        assert "\t\theader_up X-Forwarded-Proto https" in result
        # No leading-space indentation
        for line in result.splitlines():
            stripped = line.lstrip("\t")
            if stripped and stripped != line:
                assert not stripped.startswith("  "), f"Mixed indent: {line!r}"

    def test_https_scheme_keeps_prefix(self):
        """When upstream is genuinely HTTPS, the scheme prefix is included."""

        svc = _make_service(upstream_scheme="https", upstream_port=8443)
        result = render_caddyfile(svc)

        assert "reverse_proxy https://nextcloud:8443" in result

    def test_https_scheme_port_80_uses_tls_transport(self):
        """Caddy rejects ``https://host:80`` (scheme/port conflict, mirroring
        ``http://host:443``). For an HTTPS upstream on port 80 the renderer must
        dial the bare address and force TLS via the transport directive instead
        of emitting the rejected scheme prefix."""

        svc = _make_service(upstream_scheme="https", upstream_port=80)
        result = render_caddyfile(svc)

        # The conflicting scheme prefix must NOT be emitted.
        assert "https://nextcloud:80" not in result
        # Dial the bare upstream address and force TLS via transport.
        assert "reverse_proxy nextcloud:80 {" in result
        assert "\t\ttransport http {" in result
        assert "\t\t\ttls" in result

    def test_transport_tls_block_scoped_to_https_port_80(self):
        """The ``transport http { tls }`` block must appear ONLY for an HTTPS
        upstream on port 80. Broadening it to all HTTPS upstreams (or to HTTP)
        would re-break the conventional ``https://host:443`` case and emit an
        invalid Caddyfile — the exact regression the port-80 special-case fixed."""

        https_443 = render_caddyfile(_make_service(upstream_scheme="https", upstream_port=443))
        assert "transport http {" not in https_443

        http_80 = render_caddyfile(_make_service(upstream_scheme="http", upstream_port=80))
        assert "transport http {" not in http_80

        https_80 = render_caddyfile(_make_service(upstream_scheme="https", upstream_port=80))
        assert "transport http {" in https_80


class TestWriteCaddyfile:
    def test_writes_file(self, tmp_path):

        svc = _make_service(id="svc_test123")
        path = write_caddyfile(svc, tmp_path)

        assert path.exists()
        assert path.name == "Caddyfile"
        assert path.parent.name == "svc_test123"

        content = path.read_text(encoding="utf-8")
        assert "auto_https off" in content
        assert "https://nextcloud.example.com" in content

    def test_creates_service_directory(self, tmp_path):

        svc = _make_service(id="svc_newdir")
        write_caddyfile(svc, tmp_path)

        service_dir = tmp_path / "svc_newdir"
        assert service_dir.is_dir()

    def test_overwrites_existing(self, tmp_path):

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

        svc = _make_service(id="svc_atomic")
        write_caddyfile(svc, tmp_path)

        service_dir = tmp_path / "svc_atomic"
        files = list(service_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "Caddyfile"
        # No .tmp files left behind
        assert not any(f.suffix == ".tmp" for f in files)

    def test_uses_unique_temp_name(self, tmp_path, monkeypatch):
        """The temp file must be uniquely named (pid+thread+uuid), not a fixed
        'Caddyfile.tmp', so concurrent writers to one service dir cannot collide
        on the same temp path."""


        recorded = []
        original = Path.write_text

        def record(self, *args, **kwargs):
            recorded.append(self.name)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", record)

        svc = _make_service(id="svc_unique")
        config_renderer.write_caddyfile(svc, tmp_path)

        temp_names = [n for n in recorded if n != "Caddyfile"]
        assert len(temp_names) == 1
        tmp_name = temp_names[0]
        assert tmp_name != "Caddyfile.tmp"
        assert tmp_name.startswith(".Caddyfile.")
        assert tmp_name.endswith(".tmp")
        assert str(os.getpid()) in tmp_name
        # No stray temp remains after a successful write.
        leftovers = [p.name for p in (tmp_path / "svc_unique").iterdir() if p.name != "Caddyfile"]
        assert leftovers == []

    def test_accepts_str_generated_dir(self, tmp_path):
        """generated_dir may arrive as a str (e.g. from settings); it is coerced
        to Path so ``generated_dir / service.id`` doesn't TypeError."""

        svc = _make_service(id="svc_strpath")
        path = write_caddyfile(svc, str(tmp_path))

        assert path.exists()
        assert path.parent.name == "svc_strpath"
        assert path.name == "Caddyfile"

    def test_cleans_up_temp_on_write_failure(self, tmp_path, monkeypatch):
        """If a step AFTER the temp file is created fails (here the fsync), the
        partial temp file must be removed and the error re-raised — a failed
        render leaves neither a stray .tmp nor a half-written Caddyfile."""

        def boom(_path):
            raise OSError("disk full")

        monkeypatch.setattr(config_renderer, "fsync_file", boom)

        svc = _make_service(id="svc_failclean")
        with pytest.raises(OSError, match="disk full"):
            config_renderer.write_caddyfile(svc, tmp_path)

        service_dir = tmp_path / "svc_failclean"
        leftovers = [p.name for p in service_dir.iterdir()]
        assert leftovers == [], f"expected no files left behind, found {leftovers}"

    def test_sweeps_stale_temp_files_from_prior_crash(self, tmp_path):
        """A previous writer hard-killed (SIGKILL / power loss) between creating
        its temp and the atomic rename leaves a ``.Caddyfile.*.tmp`` orphan. The
        next write must reclaim those crash orphans (it runs under the per-service
        reconcile lock, so no concurrent writer owns an in-flight temp here)."""

        svc = _make_service(id="svc_staletmp")
        service_dir = tmp_path / "svc_staletmp"
        service_dir.mkdir(parents=True)
        # Simulate orphans left by crashed prior runs (different pid/thread/uuid).
        orphan_a = service_dir / ".Caddyfile.111.222.deadbeef.tmp"
        orphan_b = service_dir / ".Caddyfile.333.444.cafef00d.tmp"
        orphan_a.write_text("half-written", encoding="utf-8")
        orphan_b.write_text("half-written", encoding="utf-8")

        write_caddyfile(svc, tmp_path)

        leftovers = sorted(p.name for p in service_dir.iterdir())
        # Only the published Caddyfile survives; both crash orphans are gone.
        assert leftovers == ["Caddyfile"], f"stale temps not reclaimed: {leftovers}"
        assert not orphan_a.exists()
        assert not orphan_b.exists()

    def test_sweep_failure_does_not_block_write(self, tmp_path, monkeypatch):
        """The stale-temp sweep is best-effort: an unlink error (e.g. a temp
        vanishing or a permission glitch) must not abort the actual write."""


        svc = _make_service(id="svc_sweepfail")
        service_dir = tmp_path / "svc_sweepfail"
        service_dir.mkdir(parents=True)
        (service_dir / ".Caddyfile.9.9.abc.tmp").write_text("x", encoding="utf-8")

        original_unlink = Path.unlink

        def flaky_unlink(self, *args, **kwargs):
            if self.name.endswith(".tmp") and ".Caddyfile.9.9" in self.name:
                raise OSError("transient unlink failure")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        # Must not raise despite the sweep unlink failing.
        path = write_caddyfile(svc, tmp_path)
        assert path.exists()
        assert path.read_text(encoding="utf-8").startswith("{")
