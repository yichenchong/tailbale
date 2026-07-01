"""File-based secret storage.

Secrets are stored as individual files under data/secrets/.
The API never returns secret values — only whether they are configured.
"""

import logging
import stat
from pathlib import Path

from app.config import settings
from app.fsutil import atomic_write_text

logger = logging.getLogger(__name__)

# Known secret names
CLOUDFLARE_TOKEN = "cloudflare_token"
TAILSCALE_AUTH_KEY = "tailscale_authkey"
TAILSCALE_API_KEY = "tailscale_api_key"

ALL_SECRETS = [CLOUDFLARE_TOKEN, TAILSCALE_AUTH_KEY, TAILSCALE_API_KEY]


# Tailscale key prefixes — single source of truth for validation and messages.
TS_AUTHKEY_PREFIX = "tskey-auth-"
TS_APIKEY_PREFIX = "tskey-api-"


def is_valid_ts_auth_key(value: str | None) -> bool:
    """True if ``value`` is a non-empty Tailscale auth key (``tskey-auth-…``)."""
    return bool(value and value.startswith(TS_AUTHKEY_PREFIX))


def is_valid_ts_api_key(value: str | None) -> bool:
    """True if ``value`` is a non-empty Tailscale API key (``tskey-api-…``)."""
    return bool(value and value.startswith(TS_APIKEY_PREFIX))


def _secret_path(name: str) -> Path:
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        raise ValueError("Secret name must be a single, non-hidden path component")
    path = settings.secrets_dir / name
    try:
        path.resolve().relative_to(settings.secrets_dir.resolve())
    except ValueError as exc:
        raise ValueError("Secret path must stay inside the secrets directory") from exc
    return path


def _write_private_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, value, mode=stat.S_IRUSR | stat.S_IWUSR)


def write_secret(name: str, value: str) -> None:
    """Write a secret value to a file with restricted permissions."""
    _write_private_atomic(_secret_path(name), value)


def read_secret(name: str) -> str | None:
    """Read a secret value from file. Returns None if not set.

    Mirrors delete_secret: reading directly and catching FileNotFoundError
    avoids the TOCTOU window an ``is_file()`` pre-check would open against a
    concurrent/multi-process delete, which would otherwise surface as an
    uncaught FileNotFoundError to the caller.
    """
    path = _secret_path(name)
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def delete_secret(name: str) -> bool:
    """Delete a secret file. Returns True if it existed.

    unlink() is itself atomic, so the prior is_file() pre-check only added a
    TOCTOU window against a concurrent (or multi-process) delete. A vanished
    file simply yields False.
    """
    path = _secret_path(name)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        logger.debug("Secret %s already absent on delete", name)
        return False
