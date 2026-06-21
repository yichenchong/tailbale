"""Tests for the certificate manager module."""

import subprocess
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


def _fake_lego_proc(lines, returncode=0, wait_side_effect=None):
    """Build a fake Popen process for streaming _run_lego tests."""
    proc = MagicMock()
    proc.stdout = iter(lines)
    if wait_side_effect is not None:
        proc.wait.side_effect = wait_side_effect
    else:
        proc.wait.return_value = returncode
    proc.returncode = returncode
    return proc


class TestRunLego:
    @patch("app.certs.cert_manager.subprocess.Popen")
    def test_runs_lego_with_cloudflare_env(self, mock_popen, tmp_path):
        from app.certs.cert_manager import LEGO_TIMEOUT_SECONDS, _run_lego

        mock_popen.return_value = _fake_lego_proc(["ok\n"], returncode=0)

        _run_lego(
            ["--domains", "test.example.com", "--email", "a@b.com", "--accept-tos", "run"],
            cloudflare_token="cf-token-123",
            lego_dir=tmp_path,
        )

        mock_popen.assert_called_once()
        call = mock_popen.call_args
        # Check CF token in env
        assert call.kwargs["env"]["CF_DNS_API_TOKEN"] == "cf-token-123"
        # Check command args
        cmd = call.args[0]
        assert "lego" in cmd[0]
        assert "--dns" in cmd
        assert "cloudflare" in cmd
        assert "--overall-request-limit" in cmd
        # Throttle ACME requests low to avoid tripping rate limits.
        from app.certs.cert_manager import LEGO_OVERALL_REQUEST_LIMIT
        limit_idx = cmd.index("--overall-request-limit") + 1
        assert cmd[limit_idx] == str(LEGO_OVERALL_REQUEST_LIMIT)
        assert LEGO_OVERALL_REQUEST_LIMIT <= 5
        # Timeout must comfortably exceed lego's DNS propagation wait (~60s).
        mock_popen.return_value.wait.assert_called_with(timeout=LEGO_TIMEOUT_SECONDS)
        assert LEGO_TIMEOUT_SECONDS >= 120

    @patch("app.certs.cert_manager.subprocess.Popen")
    def test_streams_each_output_line_to_log(self, mock_popen, tmp_path, caplog):
        import logging

        from app.certs.cert_manager import _run_lego

        mock_popen.return_value = _fake_lego_proc(
            ["[INFO] acme: registering account\n", "[INFO] acme: obtaining cert\n"],
            returncode=0,
        )

        with caplog.at_level(logging.INFO, logger="app.certs.cert_manager"):
            _run_lego(["run"], cloudflare_token="cf-token", lego_dir=tmp_path)

        assert "[lego] [INFO] acme: registering account" in caplog.text
        assert "[lego] [INFO] acme: obtaining cert" in caplog.text

    @patch("app.certs.cert_manager.subprocess.Popen")
    def test_raises_on_failure(self, mock_popen, tmp_path):
        from app.certs.cert_manager import _run_lego

        mock_popen.return_value = _fake_lego_proc(["auth error\n"], returncode=1)

        with pytest.raises(RuntimeError, match="lego failed"):
            _run_lego(["run"], cloudflare_token="bad", lego_dir=tmp_path)

    @patch("app.certs.cert_manager.subprocess.Popen")
    def test_raises_on_timeout(self, mock_popen, tmp_path):
        from app.certs.cert_manager import _run_lego

        # First wait() (with timeout) raises; the post-kill wait() returns.
        proc = _fake_lego_proc(
            ["waiting for retry after 31h\n"],
            wait_side_effect=[
                subprocess.TimeoutExpired(cmd=["lego", "run"], timeout=300),
                0,
            ],
        )
        mock_popen.return_value = proc

        with pytest.raises(RuntimeError, match="lego timed out after"):
            _run_lego(["run"], cloudflare_token="cf-token", lego_dir=tmp_path)
        proc.kill.assert_called_once()


class TestIssueCert:
    @patch("app.certs.cert_manager._run_lego")
    def test_issues_and_copies_certs(self, mock_lego, tmp_path):
        from app.certs.cert_manager import issue_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"

        # Simulate lego producing cert files
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        (lego_certs / "test.example.com.crt").write_text("CERT DATA")
        (lego_certs / "test.example.com.key").write_text("KEY DATA")

        result = issue_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir,
        )

        assert result == cert_dir
        assert (cert_dir / "fullchain.pem").read_text() == "CERT DATA"
        assert (cert_dir / "privkey.pem").read_text() == "KEY DATA"
        mock_lego.assert_called_once()

    @patch("app.certs.cert_manager._run_lego")
    def test_raises_if_cert_files_missing(self, mock_lego, tmp_path):
        from app.certs.cert_manager import issue_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        lego_dir.mkdir(parents=True)

        with pytest.raises(RuntimeError, match="did not produce"):
            issue_cert("test.example.com", "a@b.com", "cf-token", cert_dir, lego_dir)

    @patch("app.certs.cert_manager._run_lego")
    def test_default_lego_dir(self, mock_lego, tmp_path):
        from app.certs.cert_manager import issue_cert

        cert_dir = tmp_path / "certs" / "test.example.com"

        # Create expected lego output
        lego_dir = cert_dir.parent / ".lego"
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        (lego_certs / "test.example.com.crt").write_text("CERT")
        (lego_certs / "test.example.com.key").write_text("KEY")

        issue_cert("test.example.com", "a@b.com", "cf-token", cert_dir)
        # lego_dir should default to cert_dir.parent / ".lego"
        assert mock_lego.called


class TestRenewCert:
    @patch("app.certs.cert_manager._run_lego")
    def test_renews_and_copies_certs(self, mock_lego, tmp_path):
        from app.certs.cert_manager import renew_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"

        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        (lego_certs / "test.example.com.crt").write_text("RENEWED CERT")
        (lego_certs / "test.example.com.key").write_text("RENEWED KEY")

        result = renew_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir, days=30,
        )

        assert result == cert_dir
        assert (cert_dir / "fullchain.pem").read_text() == "RENEWED CERT"
        assert (cert_dir / "privkey.pem").read_text() == "RENEWED KEY"

        # Verify lego was called with renew args
        call_args = mock_lego.call_args.args[0]
        assert "renew" in call_args
        assert "--days" in call_args


class TestAtomicCopyCerts:
    def test_atomic_copy(self, tmp_path):
        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "src_cert.pem"
        src_key = tmp_path / "src_key.pem"
        src_cert.write_text("CERT CONTENT")
        src_key.write_text("KEY CONTENT")

        dest_dir = tmp_path / "dest"
        _atomic_copy_certs(src_cert, src_key, dest_dir)

        assert (dest_dir / "fullchain.pem").read_text() == "CERT CONTENT"
        assert (dest_dir / "privkey.pem").read_text() == "KEY CONTENT"

    def test_no_tmp_files_left(self, tmp_path):
        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "src_cert.pem"
        src_key = tmp_path / "src_key.pem"
        src_cert.write_text("CERT")
        src_key.write_text("KEY")

        dest_dir = tmp_path / "dest"
        _atomic_copy_certs(src_cert, src_key, dest_dir)

        files = list(dest_dir.iterdir())
        assert not any(f.suffix == ".tmp" for f in files)
        assert len(files) == 2

    def test_creates_dest_directory(self, tmp_path):
        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "c.pem"
        src_key = tmp_path / "k.pem"
        src_cert.write_text("C")
        src_key.write_text("K")

        dest_dir = tmp_path / "nested" / "deep" / "dest"
        _atomic_copy_certs(src_cert, src_key, dest_dir)
        assert dest_dir.is_dir()

    def test_overwrites_existing(self, tmp_path):
        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "c.pem"
        src_key = tmp_path / "k.pem"
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        (dest_dir / "fullchain.pem").write_text("OLD CERT")
        (dest_dir / "privkey.pem").write_text("OLD KEY")

        src_cert.write_text("NEW CERT")
        src_key.write_text("NEW KEY")

        _atomic_copy_certs(src_cert, src_key, dest_dir)
        assert (dest_dir / "fullchain.pem").read_text() == "NEW CERT"
        assert (dest_dir / "privkey.pem").read_text() == "NEW KEY"

    def test_restores_existing_pair_if_second_replace_fails(self, tmp_path, monkeypatch):
        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "c.pem"
        src_key = tmp_path / "k.pem"
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        (dest_dir / "fullchain.pem").write_text("OLD CERT")
        (dest_dir / "privkey.pem").write_text("OLD KEY")
        src_cert.write_text("NEW CERT")
        src_key.write_text("NEW KEY")

        path_type = type(dest_dir)
        original_replace = path_type.replace

        def fail_privkey_replace(self, target):
            if getattr(target, "name", "") == "privkey.pem":
                raise OSError("privkey replace failed")
            return original_replace(self, target)

        monkeypatch.setattr(path_type, "replace", fail_privkey_replace)

        with pytest.raises(OSError, match="privkey replace failed"):
            _atomic_copy_certs(src_cert, src_key, dest_dir)

        assert (dest_dir / "fullchain.pem").read_text() == "OLD CERT"
        assert (dest_dir / "privkey.pem").read_text() == "OLD KEY"
        assert not any(path.name.endswith((".tmp", ".bak")) for path in dest_dir.iterdir())


class TestGetCertExpiry:
    def test_returns_none_for_missing_file(self, tmp_path):
        from app.certs.cert_manager import get_cert_expiry

        result = get_cert_expiry(tmp_path / "nonexistent.pem")
        assert result is None

    def test_returns_none_for_invalid_cert(self, tmp_path):
        from app.certs.cert_manager import get_cert_expiry

        bad_cert = tmp_path / "bad.pem"
        bad_cert.write_text("not a certificate")

        result = get_cert_expiry(bad_cert)
        assert result is None

    def test_parses_real_cert(self, tmp_path):
        """Generate a self-signed cert and verify expiry parsing."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        from app.certs.cert_manager import get_cert_expiry

        # Generate a test certificate
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
        expiry = datetime(2027, 6, 15, 12, 0, 0, tzinfo=UTC)

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
            .not_valid_after(expiry)
            .sign(key, hashes.SHA256())
        )

        cert_path = tmp_path / "fullchain.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        result = get_cert_expiry(cert_path)
        assert result is not None
        assert result.year == 2027
        assert result.month == 6
        assert result.day == 15
