"""Reloading-caddy step: reload when config/cert changed; classify failures."""

from __future__ import annotations

import logging

import docker
from sqlalchemy.orm import Session

from app.edge import caddy_admin
from app.events.types import EventKind
from app.fsutil import atomic_write_text
from app.models.service import Service
from app.reconciler.errors import ReconcileError
from app.reconciler.status import _persist_status, _update_phase
from app.reconciler.steps.models import _ConfigStage

logger = logging.getLogger(__name__)


def reload_if_needed(
    db: Session,
    service: Service,
    stage: _ConfigStage,
    socket_path: str | None,
    result: dict,
) -> None:
    """Reloading-caddy step: reload when config/cert changed; classify failures.

    Every reload failure raises ReconcileError so ``.reload_pending`` stays set
    and the reload is retried next reconcile. Post-reload bookkeeping (clearing
    the marker, recording the loaded-cert fingerprint, emitting the event) stays
    OUT of the try so its own errors are reported truthfully.
    """
    if not (stage.config_changed or stage.reload_pending_path.exists()):
        return

    service_id = service.id
    service_name = service.name
    _update_phase(db, service_id, "reloading_caddy", "Reloading Caddy")
    try:
        caddy_admin.reload_caddy(service_id, service.edge_container_name, socket_path)
    except RuntimeError as e:
        # Caddy rejected the config (bad Caddyfile / non-zero reload exit).
        logger.warning("Caddy reload failed for %s", service_id, exc_info=True)
        raise ReconcileError(f"Caddy reload failed: {e}") from e
    except (docker.errors.DockerException, ConnectionError) as e:
        # The edge container / Docker daemon was unreachable: exec_run re-raises a
        # docker.errors.APIError, or the admin-API socket refused the connection.
        logger.warning("Caddy reload failed for %s", service_id, exc_info=True)
        raise ReconcileError(f"Caddy reload failed: Docker/edge unavailable: {e}") from e
    except Exception as e:
        # Anything else is unexpected, but still a reload failure: raise
        # ReconcileError so the reload-pending marker survives for the next retry.
        logger.warning("Caddy reload failed for %s", service_id, exc_info=True)
        raise ReconcileError(f"Caddy reload failed (unexpected): {e}") from e
    result["caddy_reloaded"] = True
    stage.reload_pending_path.unlink(missing_ok=True)
    # Record the fingerprint captured before the reload as the cert now loaded
    # into Caddy. The per-service reconcile lock (held for the whole reconcile)
    # keeps cert_path immutable from that read through this reload, so it equals
    # the on-disk cert; recording the pre-reload value also avoids ever persisting
    # a cert that landed after Caddy read the old one — which would suppress the
    # next reload and serve a stale cert. Durable atomic write: this marker is a
    # crash-desync guard, so a bare write_text (no fsync) could lose it.
    if stage.current_cert_fp is not None:
        atomic_write_text(stage.cert_state_path, stage.current_cert_fp)
    _persist_status(
        db,
        service_id,
        event={
            "kind": EventKind.CADDY_RELOADED,
            "message": f"Caddy reloaded for '{service_name}'",
        },
    )
