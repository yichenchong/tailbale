"""File-based secret storage.

Secrets are stored as individual files under data/secrets/.
The API never returns secret values — only whether they are configured.
"""

import os
import stat
from pathlib import Path

from app.config import settings

# Known secret names
CLOUDFLARE_TOKEN = "cloudflare_token"
TAILSCALE_AUTH_KEY = "tailscale_authkey"
TAILSCALE_API_KEY = "tailscale_api_key"

ALL_SECRETS = [CLOUDFLARE_TOKEN, TAILSCALE_AUTH_KEY, TAILSCALE_API_KEY]


def _secret_path(name: str) -> Path:
    return settings.secrets_dir / name


def write_secret(name: str, value: str) -> None:
    """Write a secret value to a file with restricted permissions."""
    path = _secret_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file, then rename for atomicity
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(value, encoding="utf-8")

    # Restrict permissions (owner read/write only) — best-effort on Windows
    try:
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    tmp_path.replace(path)


def read_secret(name: str) -> str | None:
    """Read a secret value from file. Returns None if not set."""
    path = _secret_path(name)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def secret_exists(name: str) -> bool:
    """Check if a secret file exists (without reading it)."""
    return _secret_path(name).is_file()


def delete_secret(name: str) -> bool:
    """Delete a secret file. Returns True if it existed."""
    path = _secret_path(name)
    if path.is_file():
        path.unlink()
        return True
    return False


def get_secret_presence() -> dict[str, bool]:
    """Return a dict of secret name -> whether it's configured."""
    return {name: secret_exists(name) for name in ALL_SECRETS}
