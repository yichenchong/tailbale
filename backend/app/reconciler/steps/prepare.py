"""Validating step: check secrets, resolve runtime paths, create per-service dirs."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app import secrets, settings_store
from app.models.service import Service
from app.reconciler.errors import ReconcileError
from app.reconciler.status import _update_phase
from app.reconciler.steps.models import _RuntimePaths


def validate_and_prepare(db: Session, service: Service) -> tuple[str, _RuntimePaths]:
    """Validating step: check secrets, resolve runtime paths, create per-service dirs.

    Returns the Tailscale auth key and the resolved runtime paths. Raises
    ReconcileError when the auth key is not configured.
    """
    service_id = service.id
    _update_phase(db, service_id, "validating", "Checking settings and secrets")

    ts_authkey = secrets.read_secret(secrets.TAILSCALE_AUTH_KEY)
    if not ts_authkey:
        raise ReconcileError("Tailscale auth key not configured")

    runtime = settings_store.get_runtime_paths(db)
    paths = _RuntimePaths(
        generated_dir=Path(runtime["generated_dir"]),
        certs_dir=Path(runtime["certs_dir"]),
        ts_state_dir=Path(runtime["tailscale_state_dir"]),
        host_generated_dir=Path(runtime["host_generated_dir"]),
        host_certs_dir=Path(runtime["host_certs_dir"]),
        host_ts_state_dir=Path(runtime["host_tailscale_state_dir"]),
    )

    (paths.generated_dir / service_id).mkdir(parents=True, exist_ok=True)
    (paths.certs_dir / service.hostname).mkdir(parents=True, exist_ok=True)
    (paths.ts_state_dir / service.edge_container_name).mkdir(parents=True, exist_ok=True)
    return ts_authkey, paths
