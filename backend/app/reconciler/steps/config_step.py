"""Rendering-config step: render the Caddyfile, diff it, and set reload markers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from app.edge import config_renderer
from app.fsutil import atomic_write_text
from app.models.service import Service
from app.reconciler.status import _update_phase
from app.reconciler.steps.models import _ConfigStage


def _cert_fingerprint(cert_path: Path) -> str | None:
    """Return a stable fingerprint of the cert file, or None when it is absent.

    Lets the reconciler notice that a renewed certificate landed on disk and
    force a Caddy reload — Caddy never re-reads file-based certs on its own, and
    ``caddy reload`` skips them when the config text is unchanged.
    """
    try:
        return hashlib.sha256(cert_path.read_bytes()).hexdigest()
    except OSError:
        return None


def render_and_stage_config(
    db: Session, service: Service, generated_dir: Path, cert_path: Path
) -> _ConfigStage:
    """Rendering-config step: render the Caddyfile, diff it, and set reload markers.

    Returns the staging result (config_changed + reload/cert markers) consumed by
    the reload step.
    """
    _update_phase(db, service.id, "rendering_config", "Generating Caddy configuration")
    new_config = config_renderer.render_caddyfile(service)
    service_config_dir = generated_dir / service.id
    existing_path = service_config_dir / "Caddyfile"
    # ".reload_pending" marks that the on-disk Caddyfile changed but the running
    # Caddy has not yet successfully reloaded it. Set when a config change is
    # detected, cleared only after reload_caddy succeeds — so a reload failure is
    # retried on the next reconcile even though the file on disk already matches
    # desired (config_changed would otherwise be False, leaving Caddy serving
    # stale config while the service reports healthy).
    reload_pending_path = service_config_dir / ".reload_pending"
    # Caddy with file-based certs never re-reads a renewed cert on its own, and
    # ``caddy reload`` skips cert files when the config text is unchanged. Track
    # the fingerprint of the cert last loaded into Caddy so a renewal (by this
    # loop or the daily renewal task) forces a reload.
    cert_state_path = service_config_dir / ".cert_loaded"
    current_cert_fp = _cert_fingerprint(cert_path)
    loaded_cert_fp = (
        cert_state_path.read_text(encoding="utf-8").strip()
        if cert_state_path.exists()
        else None
    )
    cert_changed = current_cert_fp is not None and current_cert_fp != loaded_cert_fp
    config_changed = True
    if existing_path.exists():
        config_changed = existing_path.read_text(encoding="utf-8") != new_config
    if config_changed:
        # Mark reload pending BEFORE writing so a crash/failure between the two
        # can never leave a changed on-disk config with no pending-reload marker
        # (which is exactly the desync this guards against). Durable atomic write:
        # a bare write_text wouldn't fsync, so a power-loss could lose the marker
        # and reintroduce the very changed-config-without-pending-reload desync.
        atomic_write_text(reload_pending_path, "1")
        config_renderer.write_caddyfile(service, generated_dir)
    elif cert_changed:
        # Config unchanged but the cert on disk was renewed — force a reload so
        # the edge actually serves the new certificate.
        atomic_write_text(reload_pending_path, "1")
    return _ConfigStage(
        config_changed=config_changed,
        reload_pending_path=reload_pending_path,
        cert_state_path=cert_state_path,
        current_cert_fp=current_cert_fp,
    )
