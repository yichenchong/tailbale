"""Certificate inspection helpers."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)


def get_cert_expiry(cert_path: Path) -> datetime | None:
    """Parse a PEM certificate file and return its expiry datetime.

    Returns None if the file doesn't exist or can't be parsed.
    """
    if not cert_path.exists():
        return None

    try:
        cert_data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_data)
        return cert.not_valid_after_utc
    except Exception:
        logger.warning("Failed to parse cert at %s", cert_path, exc_info=True)
        return None


def cert_key_pair_matches(cert_path: Path, key_path: Path) -> bool:
    """Return True if *cert_path*'s public key matches *key_path*'s private key.

    Defense-in-depth against a mismatched fullchain/privkey pair on disk.
    ``_atomic_copy_certs`` verifies the pair and publishes it via a single
    atomic ``current`` symlink swap, so a served pair is normally consistent;
    a mismatch detected here therefore signals on-disk corruption or external
    tampering. Caddy would otherwise serve a certificate whose key does not
    match and every TLS handshake would fail, with the expiry-based checks
    noticing nothing.

    Returns ``True`` when the pair matches OR when there is nothing to verify: the
    cert file is absent, or it is unreadable (in which case ``get_cert_expiry``
    already returns None and drives a re-issue, so reporting a mismatch too would
    be redundant). Returns ``False`` only when the cert is present and readable
    but the key is missing, unreadable, or carries a different public key.
    """
    if not cert_path.exists():
        return True
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        cert_pub = cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        return True
    if not key_path.exists():
        return False
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        key_pub = key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except Exception:
        return False
    return cert_pub == key_pub
