"""Shared helpers for certificate manager tests."""

import functools
from datetime import UTC, datetime
from unittest.mock import MagicMock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


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


@functools.cache
def _real_pem_pair(tag: int = 0) -> tuple[bytes, bytes]:
    """Return ``(fullchain_pem, privkey_pem)`` for a matching self-signed pair."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2027, 6, 15, tzinfo=UTC))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def _write_cert_key_pair(cert_path, key_path, *, key=None, cert_key=None):
    """Write a self-signed cert + private key, optionally as a mismatched pair."""
    if key is None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if cert_key is None:
        cert_key = key
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(cert_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2027, 6, 15, tzinfo=UTC))
        .sign(cert_key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
