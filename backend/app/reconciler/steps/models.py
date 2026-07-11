"""Shared data contracts passed between reconcile phases.

These two ``NamedTuple`` results are produced by one phase and consumed by a
later one (``_RuntimePaths`` by :mod:`~app.reconciler.steps.prepare` →
:mod:`~app.reconciler.steps.edge_step`; ``_ConfigStage`` by
:mod:`~app.reconciler.steps.config_step` →
:mod:`~app.reconciler.steps.reload_step`). They live in this leaf so the
producing/consuming phase modules share a single definition without importing
one another.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class _RuntimePaths(NamedTuple):
    """Resolved on-disk + host-mapped directories for one reconcile pass."""

    generated_dir: Path
    certs_dir: Path
    ts_state_dir: Path
    host_generated_dir: Path
    host_certs_dir: Path
    host_ts_state_dir: Path


class _ConfigStage(NamedTuple):
    """Result of staging the Caddyfile: what changed and the reload markers."""

    config_changed: bool
    reload_pending_path: Path
    cert_state_path: Path
    current_cert_fp: str | None
