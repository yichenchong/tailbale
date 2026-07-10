"""Tests for certificate inspection helpers."""

from datetime import UTC, datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.certs.cert_manager import cert_key_pair_matches, get_cert_expiry
from tests._cert_helpers import _write_cert_key_pair


class TestGetCertExpiry:
    def test_returns_none_for_missing_file(self, tmp_path):

        result = get_cert_expiry(tmp_path / "nonexistent.pem")
        assert result is None

    def test_returns_none_for_invalid_cert(self, tmp_path):

        bad_cert = tmp_path / "bad.pem"
        bad_cert.write_text("not a certificate")

        result = get_cert_expiry(bad_cert)
        assert result is None

    def test_parses_real_cert(self, tmp_path):
        """Generate a self-signed cert and verify expiry parsing."""

        # Generate a test certificate
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
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

class TestCertKeyPairMatches:
    def test_true_for_matching_pair(self, tmp_path):

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        _write_cert_key_pair(cert_path, key_path)

        assert cert_key_pair_matches(cert_path, key_path) is True

    def test_false_for_mismatched_pair(self, tmp_path):
        """A cert signed by one key beside a *different* private key is the
        mismatched current/ pair that on-disk corruption or external tampering
        can leave; cert_key_pair_matches must catch it."""

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        # Cert's public key comes from cert_key; privkey.pem holds an unrelated key.
        _write_cert_key_pair(cert_path, key_path, cert_key=other_key)

        assert cert_key_pair_matches(cert_path, key_path) is False

    def test_false_when_key_missing(self, tmp_path):

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        _write_cert_key_pair(cert_path, tmp_path / "scratch.pem")

        assert cert_key_pair_matches(cert_path, key_path) is False

    def test_false_when_key_unreadable(self, tmp_path):

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        _write_cert_key_pair(cert_path, tmp_path / "scratch.pem")
        key_path.write_text("not a private key")

        assert cert_key_pair_matches(cert_path, key_path) is False

    def test_true_when_cert_missing(self, tmp_path):
        """Nothing to verify: get_cert_expiry already drives a re-issue."""

        assert cert_key_pair_matches(tmp_path / "absent.pem", tmp_path / "k.pem") is True

    def test_true_when_cert_unreadable(self, tmp_path):

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        cert_path.write_text("not a certificate")
        key_path.write_text("not a key")

        assert cert_key_pair_matches(cert_path, key_path) is True
