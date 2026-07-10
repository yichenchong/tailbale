import contextlib
import os
import secrets
import stat
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from app.fsutil import atomic_write_text


def _load_or_create_jwt_secret(data_dir: Path) -> str:
    """Read JWT secret from data dir, or generate and persist one on first run."""
    secret_file = data_dir / "secrets" / ".jwt_secret"
    try:
        existing = secret_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        with contextlib.suppress(OSError):
            os.chmod(secret_file, stat.S_IRUSR | stat.S_IWUSR)
        return existing
    # A persisted-but-empty/corrupt file (e.g. truncated by a botched backup
    # restore or a 0-byte volume mount) would yield an empty HMAC key, making
    # every JWT trivially forgeable. Reading directly and catching
    # FileNotFoundError (rather than an exists() pre-check) also closes the
    # TOCTOU window a concurrent first-run writer would open. Fall through to
    # regenerate instead.
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    # Lock the secrets dir to owner-only the instant it is created so it is never
    # world-listable in the window between this import-time creation and the later
    # ensure_dirs() chmod. Best-effort: an exotic/overlay FS that rejects chmod
    # must not crash startup.
    with contextlib.suppress(OSError):
        os.chmod(secret_file.parent, 0o700)
    secret = secrets.token_urlsafe(64)
    atomic_write_text(secret_file, secret, mode=stat.S_IRUSR | stat.S_IWUSR)
    return secret


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # Paths
    data_dir: Path = Path("./data")
    # HOST_DATA_DIR: the path to the data directory **on the Docker host**.
    # When tailBale runs inside a container that mounts the Docker socket,
    # bind-mount source paths must be expressed in the host's filesystem.
    # If unset, defaults to data_dir (fine for non-containerised installs).
    host_data_dir: Path | None = None

    # Auth
    jwt_secret: str = ""  # Auto-generated on first run; see _load_or_create_jwt_secret
    # ge=1: a 0/negative expiry makes every issued token already-expired on
    # arrival (exp == now), silently breaking ALL logins. Unlike the rate-limit
    # knobs below (clamped via max(1, ...) in the limiter) this value feeds JWT
    # exp and the cookie max-age directly, with no downstream guard — so reject
    # a non-positive setting loudly at startup.
    jwt_expiry_hours: int = Field(default=24, ge=1)
    cookie_secure: bool = False  # Force Secure flag even over HTTP. Auto-enabled when the request arrives over HTTPS (incl. X-Forwarded-Proto).

    # Login brute-force protection. After `login_max_failures` consecutive
    # failed logins from a client, further attempts are rejected with HTTP 429
    # for `login_lockout_seconds`. A successful login resets the client's count.
    login_max_failures: int = 5
    login_lockout_seconds: int = 60

    # CORS. Empty disables CORS; explicit origins allow credentialed cross-origin requests.
    cors_origins: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("host_data_dir", mode="before")
    @classmethod
    def _blank_host_data_dir_is_unset(cls, value: object) -> object:
        # An empty/whitespace HOST_DATA_DIR must mean "unset" — identical to
        # omitting it — so get_runtime_paths keeps host paths equal to internal
        # ones. This is a real deployment shape: a bare ``HOST_DATA_DIR=`` line
        # in .env, or a docker-compose ``${HOST_DATA_DIR:-}`` expansion of an
        # unset variable, both arrive as "". Without this, pydantic coerces ""
        # to ``Path('.')`` (non-None), and _host_path then remaps every Docker
        # bind-mount source to a bogus RELATIVE path (e.g. "generated"), which
        # the daemon treats as a named volume — silently breaking edge
        # container creation.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Derived paths
    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "tailbale.db"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def generated_dir(self) -> Path:
        return self.data_dir / "generated"

    @property
    def certs_dir(self) -> Path:
        return self.data_dir / "certs"

    @property
    def tailscale_state_dir(self) -> Path:
        return self.data_dir / "tailscale"

    def ensure_dirs(self) -> None:
        """Create all required data subdirectories."""
        for d in [
            self.db_path.parent,
            self.secrets_dir,
            self.generated_dir,
            self.certs_dir,
            self.tailscale_state_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)
        # mkdir's mode is umask-masked, so the secrets dir can land at ~0755,
        # letting a local host user enumerate which secret files exist (their
        # contents stay 0600). Force owner-only on it with an explicit chmod.
        # Best-effort like fsync_directory: an exotic/overlay FS that rejects
        # the chmod must not crash startup. Other dirs are left untouched as
        # they may be mounted broader for Caddy.
        with contextlib.suppress(OSError):
            os.chmod(self.secrets_dir, 0o700)


settings = Settings()


def ensure_jwt_secret() -> None:
    """Populate ``settings.jwt_secret`` on first run if not supplied via env.

    Extracted out of module import (AR12) so ``import app.config`` performs NO
    filesystem side effects — importing no longer writes the secret file or
    creates the secrets dir. The app lifespan (``startup.prepare_database``) and
    the test harness invoke this explicitly before any JWT is signed/verified; a
    ``JWT_SECRET`` env value short-circuits it (no file is touched).
    """
    if not settings.jwt_secret.strip():
        settings.jwt_secret = _load_or_create_jwt_secret(settings.data_dir)
