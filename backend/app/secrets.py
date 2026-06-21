"""File-based secret storage.

Secrets are stored as individual files under data/secrets/.
The API never returns secret values — only whether they are configured.
"""

import contextlib
import os
import stat
import tempfile
from pathlib import Path

from app.config import settings

# Known secret names
CLOUDFLARE_TOKEN = "cloudflare_token"
TAILSCALE_AUTH_KEY = "tailscale_authkey"
TAILSCALE_API_KEY = "tailscale_api_key"

ALL_SECRETS = [CLOUDFLARE_TOKEN, TAILSCALE_AUTH_KEY, TAILSCALE_API_KEY]


def _secret_path(name: str) -> Path:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("Secret name must be a single path component")
    path = settings.secrets_dir / name
    try:
        path.resolve().relative_to(settings.secrets_dir.resolve())
    except ValueError as exc:
        raise ValueError("Secret path must stay inside the secrets directory") from exc
    return path


def _write_private_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        tmp_path.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def write_secret(name: str, value: str) -> None:
    """Write a secret value to a file with restricted permissions."""
    _write_private_atomic(_secret_path(name), value)


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
