"""Tests for the certificate manager module."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestRunLego:
    @patch("app.certs.cert_manager.subprocess.run")
    def test_runs_lego_with_cloudflare_env(self, mock_run, tmp_path):
        from app.certs.cert_manager import _run_lego

        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")

        _run_lego(
            ["--domains", "test.example.com", "--email", "a@b.com", "--accept-tos", "run"],
            cloudflare_token="cf-token-123",
            lego_dir=tmp_path,
        )

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        # Check CF token in env
        assert call_kwargs.kwargs["env"]["CF_DNS_API_TOKEN"] == "cf-token-123"
        # Check command args
        cmd = call_kwargs.args[0]
        assert "lego" in cmd[0]
        assert "--dns" in cmd
        assert "cloudflare" in cmd

    @patch("app.certs.cert_manager.subprocess.run")
    def test_raises_on_failure(self, mock_run, tmp_path):
        from app.certs.cert_manager import _run_lego

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="auth error")

        with pytest.raises(RuntimeError, match="lego failed"):
            _run_lego(["run"], cloudflare_token="bad", lego_dir=tmp_path)


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
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from app.certs.cert_manager import get_cert_expiry

        # Generate a test certificate
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
        expiry = datetime(2027, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime(2026, 1, 1, tzinfo=timezone.utc))
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
