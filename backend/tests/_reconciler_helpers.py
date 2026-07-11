"""Shared helpers and patch targets for reconciler unit tests."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from app.reconciler.steps import (
    cert_step,
    config_step,
    dns_step,
    edge_step,
    health_step,
    ip_step,
    network_step,
    prepare,
    probe_policy,
    reload_step,
)

# Patch at source modules: reconciler imports them via the module-reference
# pattern (e.g. ``secrets.read_secret``), so the attribute resolves on the source
# module at call time and patching the source still takes effect.
_P_SECRET = "app.secrets.read_secret"
_P_RENDER = "app.edge.config_renderer.render_caddyfile"
_P_WRITE = "app.edge.config_renderer.write_caddyfile"
_P_CERT = "app.certs.renewal_task.process_service_cert"
_P_NETWORK = "app.edge.network_manager.ensure_network"
_P_CREATE_EDGE = "app.edge.container_manager.create_edge_container"
_P_FIND_EDGE = "app.edge.container_session._find_edge_container"
_P_START = "app.edge.container_manager.start_edge"
_P_TS_IP = "app.edge.tailscale_ops.detect_tailscale_ip"
_P_RELOAD = "app.edge.caddy_admin.reload_caddy"
_P_HEALTH = "app.health.health_checker.run_health_checks"
_P_AGGREGATE = "app.health.health_checker.aggregate_status"
_P_DNS = "app.adapters.dns_reconciler.reconcile_dns"

# Per-phase step submodules (AR18 split of the former monolithic ``steps.py``).
# Each binds its own copy of the shared write helpers it uses (``_update_phase``
# / ``_persist_status`` / ``atomic_write_text``), so a single
# ``patch.object(steps, attr)`` no longer reaches them — ``patch_across`` fans
# the patch out to every submodule that defines ``attr``.
_STEP_MODULES = (
    prepare,
    network_step,
    cert_step,
    config_step,
    edge_step,
    ip_step,
    dns_step,
    reload_step,
    health_step,
    probe_policy,
)


@contextmanager
def patch_across(modules, attr, **kwargs):
    """Patch ``attr`` on every module in ``modules`` that binds it.

    Behavior-preserving replacement for the pre-split single
    ``patch.object(steps, attr, ...)``: the per-phase steps now each import their
    own copy of the shared helper, so the patch must fan out to every submodule
    that defines ``attr``. Modules lacking ``attr`` are skipped. ``kwargs`` are
    forwarded to each ``patch.object`` (e.g. ``side_effect=``).
    """
    with ExitStack() as stack:
        for module in modules:
            if hasattr(module, attr):
                stack.enter_context(patch.object(module, attr, **kwargs))
        yield
