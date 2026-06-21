"""Helpers to read/write settings from the settings table."""
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.setting import Setting

# Default values for all settings keys
DEFAULTS = {
    "base_domain": "example.com",
    "acme_email": "you@example.com",
    "reconcile_interval_seconds": "60",
    "cert_renewal_window_days": "30",
    "cf_zone_id": "",
    "ts_control_url": "https://controlplane.tailscale.com",
    "ts_default_hostname_prefix": "edge",
    "docker_socket_path": "unix:///var/run/docker.sock",
    "generated_root": "",
    "cert_root": "",
    "tailscale_state_root": "",
    "timezone": "UTC",
    "developer_mode": "false",
    "setup_complete": "false",
}


def get_setting(db: Session, key: str) -> str:
    """Get a setting value, returning the default if not set."""
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    return DEFAULTS.get(key, "")

def get_positive_int_setting(db: Session, key: str) -> int:
    """Get a positive integer setting, falling back to the key's default."""
    fallback = int(DEFAULTS[key])
    try:
        value = int(get_setting(db, key))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def set_setting(db: Session, key: str, value: str) -> None:
    """Set a setting value (upsert). Caller must commit."""
    row = db.get(Setting, key)
    if row is not None:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def get_runtime_paths(db: Session) -> dict[str, str]:
    """Get runtime paths from DB settings, falling back to app.config.settings.

    Returns both "internal" paths (used by this process to read/write files)
    and "host" paths (used as Docker bind-mount sources when talking to the
    Docker daemon on the host).  When ``HOST_DATA_DIR`` is configured the
    host paths will differ from the internal ones; otherwise they are the same.
    """
    from app.config import settings as app_settings

    generated = get_setting(db, "generated_root")
    certs = get_setting(db, "cert_root")
    ts_state = get_setting(db, "tailscale_state_root")
    docker = get_setting(db, "docker_socket_path")

    # Paths as seen by this process (inside the container, if containerised).
    internal_generated = generated or str(app_settings.generated_dir)
    internal_certs = certs or str(app_settings.certs_dir)
    internal_ts_state = ts_state or str(app_settings.tailscale_state_dir)

    result = {
        "generated_dir": internal_generated,
        "certs_dir": internal_certs,
        "tailscale_state_dir": internal_ts_state,
        "docker_socket": docker or app_settings.docker_socket,
    }

    # Host-side equivalents for Docker bind mounts. Only paths under DATA_DIR can
    # be translated into HOST_DATA_DIR; custom absolute paths outside DATA_DIR are
    # already host-visible only if the operator deliberately mounted them there.
    host_data = app_settings.host_data_dir

    def _host_path(path_str: str) -> str:
        internal_path = Path(path_str).resolve()
        if host_data is None:
            return str(internal_path)
        try:
            relative = internal_path.relative_to(app_settings.data_dir.resolve())
        except ValueError:
            return str(internal_path)
        return str(Path(host_data) / relative)

    result["host_generated_dir"] = _host_path(internal_generated)
    result["host_certs_dir"] = _host_path(internal_certs)
    result["host_tailscale_state_dir"] = _host_path(internal_ts_state)

    return result


def get_all_settings(db: Session) -> dict[str, str]:
    """Get all settings as a dict, filling in defaults."""
    stored = {row.key: row.value for row in db.query(Setting).all()}
    result = dict(DEFAULTS)
    result.update(stored)
    return result
