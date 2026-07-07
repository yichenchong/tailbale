"""Reconciler error sentinels — a leaf module with no reconciler-package deps.

Extracted from ``reconciler.py`` so both ``reconciler.py`` and ``steps.py`` can
raise/catch the same ``ReconcileError`` by importing this leaf, instead of
``steps`` reaching back into ``reconciler`` at call time (which formed a real
two-way import cycle). See ``reconciler/status.py`` for the companion status-I/O
leaf.
"""

from __future__ import annotations


class ReconcileError(Exception):
    """Raised when reconciliation hits a non-recoverable failure."""
