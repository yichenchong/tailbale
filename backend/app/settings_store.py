"""Helpers to read/write settings from the settings table."""
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.setting import Setting

# Default values for all settings keys
DEFAULTS = {
    "base_domain": "example.com",
    "acme_email": "you@example.com",
    "reconcile_interval_seconds": "3600",
    "health_check_interval_seconds": "60",
    "cert_renewal_window_days": "30",
    "event_retention_days": "30",
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
    """Get a positive integer setting, raising on a corrupt stored value.

    Writes go through ``ge=1`` validation, so a stored value is normally a clean
    positive-integer string, and an unset key resolves to the key's
    positive-integer default. A stored value that is not a valid integer, or
    that parses to a non-positive integer (``< 1``), could never have passed
    write validation: it is data corruption. Rather than silently masking it
    with a fallback, this fails loud.

    Raises:
        ValueError: when the stored value is not an integer, or parses to a
            non-positive integer (``< 1``).
    """
    raw = get_setting(db, key)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Setting {key!r} has a non-integer value {raw!r}") from exc
    if value < 1:
        raise ValueError(f"Setting {key!r} has a non-positive value {value}")
    return value


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
    generated = get_setting(db, "generated_root")
    certs = get_setting(db, "cert_root")
    ts_state = get_setting(db, "tailscale_state_root")

    # Paths as seen by this process (inside the container, if containerised).
    internal_generated = generated or str(app_settings.generated_dir)
    internal_certs = certs or str(app_settings.certs_dir)
    internal_ts_state = ts_state or str(app_settings.tailscale_state_dir)

    result = {
        "generated_dir": internal_generated,
        "certs_dir": internal_certs,
        "tailscale_state_dir": internal_ts_state,
    }

    # Host-side equivalents for Docker bind mounts. Only paths under DATA_DIR can
    # be translated into HOST_DATA_DIR; custom absolute paths outside DATA_DIR are
    # already host-visible only if the operator deliberately mounted them there.
    host_data = app_settings.host_data_dir

    def _host_path(path_str: str) -> str:
        # Without HOST_DATA_DIR the host path IS the internal path; return it
        # verbatim so the two stay byte-for-byte equal (resolve() would diverge
        # them for relative roots or paths containing symlinks / '..').
        if host_data is None:
            return path_str
        internal_path = Path(path_str).resolve()
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
