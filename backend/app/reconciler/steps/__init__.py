"""Reconcile step-helpers — the per-phase building blocks of one reconcile pass.

Each phase is a cohesive, independently-testable step over ``(db, service, ...)``
living in its own submodule; :func:`app.reconciler.reconciler._reconcile_service_locked`
wires them together in spec order. This package ``__init__`` re-exports the full
set so ``app.reconciler.steps.<fn>`` (and the ``_RuntimePaths`` / ``_ConfigStage``
data contracts) stay importable exactly as when this was a single ``steps.py``
module — the public surface is unchanged.

Each phase function's module-level dependencies (health_checker, container_session,
cert_manager, dns_reconciler, caddy_admin, tailscale_ops, network_manager,
settings_store, EventKind, ...) are imported in the SAME submodule that now
defines and uses that function, so a test patching the dependency's source module
still intercepts the call.

Status I/O (``_persist_status`` / ``_update_phase``) lives in the leaf module
``reconciler/status.py`` and the ``ReconcileError`` sentinel in
``reconciler/errors.py``; each phase submodule and ``reconciler.py`` import those
leaves directly, so there is no reconciler↔steps import cycle to reach across.
"""

from __future__ import annotations

from app.fsutil import atomic_write_text
from app.health.health_checker import CRITICAL_CHECKS
from app.reconciler.status import _persist_status, _update_phase
from app.reconciler.steps.additional_networks_step import ensure_additional_networks
from app.reconciler.steps.cert_step import ensure_cert
from app.reconciler.steps.config_step import _cert_fingerprint, render_and_stage_config
from app.reconciler.steps.dns_step import ensure_dns
from app.reconciler.steps.edge_step import ensure_edge
from app.reconciler.steps.health_step import run_and_persist_health
from app.reconciler.steps.ip_step import detect_and_persist_ip
from app.reconciler.steps.models import _ConfigStage, _RuntimePaths
from app.reconciler.steps.network_step import ensure_network
from app.reconciler.steps.prepare import validate_and_prepare
from app.reconciler.steps.probe_policy import maybe_schedule_probe_retry
from app.reconciler.steps.reload_step import reload_if_needed

__all__ = [
    "CRITICAL_CHECKS",
    "_ConfigStage",
    "_RuntimePaths",
    "_cert_fingerprint",
    "_persist_status",
    "_update_phase",
    "atomic_write_text",
    "detect_and_persist_ip",
    "ensure_additional_networks",
    "ensure_cert",
    "ensure_dns",
    "ensure_edge",
    "ensure_network",
    "maybe_schedule_probe_retry",
    "reload_if_needed",
    "render_and_stage_config",
    "run_and_persist_health",
    "validate_and_prepare",
]
