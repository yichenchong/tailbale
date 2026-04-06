import secrets
from pathlib import Path

from pydantic_settings import BaseSettings


def _load_or_create_jwt_secret(data_dir: Path) -> str:
    """Read JWT secret from data dir, or generate and persist one on first run."""
    secret_file = data_dir / "secrets" / ".jwt_secret"
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip()
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(64)
    secret_file.write_text(secret, encoding="utf-8")
    return secret


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    # General
    base_domain: str = "example.com"
    acme_email: str = "you@example.com"
    reconcile_interval_seconds: int = 60
    cert_renewal_window_days: int = 30

    # Paths
    data_dir: Path = Path("./data")

    # Docker
    docker_socket: str = "unix:///var/run/docker.sock"

    # Auth
    jwt_secret: str = ""  # Auto-generated on first run; see _load_or_create_jwt_secret
    jwt_expiry_hours: int = 24
    cookie_secure: bool = False  # Set True in production with HTTPS

    # CORS
    cors_origins: str = "*"  # Comma-separated origins, or "*" for all

    # Server
    host: str = "0.0.0.0"
    port: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

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


settings = Settings()
if not settings.jwt_secret:
    settings.jwt_secret = _load_or_create_jwt_secret(settings.data_dir)
