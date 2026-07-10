"""Service-status phase vocabulary (AR2).

Single source for the phase severity ordering and the event level each phase
warrants — previously duplicated inline in ``reconciler/steps.py`` (the
``info``/``warning``/``error`` ternary) and ``reconciler/probe_retry.py`` (the
``_PHASE_RANK`` / ``_PHASE_LEVEL`` maps + the improved/degraded/changed verb).

This leaf owns ONLY the ``phase -> {rank, level, transition}`` policy so the
reconciler need not import the health package for it. Health-check
classification (which subchecks are critical vs warning) and the health
aggregation stay with the health checks themselves.
"""

from __future__ import annotations

# Health-derived phases in severity order (healthy < warning < error). Non-health
# phases (pending / disabled / failed / validating / deleted) are ranked WORST so
# a transition OUT of one reads as an improvement and a transition INTO one reads
# as a degradation — matching the reconciler's historical ``_UNKNOWN_PHASE_RANK``.
_PHASE_RANK: dict[str, int] = {"healthy": 0, "warning": 1, "error": 2}
_UNKNOWN_PHASE_RANK = 3


def phase_rank(phase: str) -> int:
    """Severity rank of *phase* (lower = healthier); unknown/non-health phases
    rank worst (``3``)."""
    return _PHASE_RANK.get(phase, _UNKNOWN_PHASE_RANK)


def phase_level(phase: str, *, unknown: str = "error") -> str:
    """Event level a phase warrants: ``healthy`` -> ``info``, ``warning`` ->
    ``warning``, ``error`` -> ``error``, and any OTHER (non-health) phase ->
    *unknown*.

    ``error`` is a known phase and always maps to ``error``; *unknown* applies
    only to non-health phases (pending / disabled / failed / ...). The default
    ``unknown="error"`` reproduces ``steps.py``'s ternary verbatim (its ``else``
    branch is ``error``); ``probe_retry`` used a ``warning`` fallback for its
    ``_PHASE_LEVEL.get(new_phase, "warning")`` lookup, so it passes
    ``unknown="warning"`` to stay byte-for-byte identical. (Both call sites only
    ever pass the three aggregate phases in practice, so the fallback is
    defensive.)
    """
    if phase == "healthy":
        return "info"
    if phase == "warning":
        return "warning"
    if phase == "error":
        return "error"
    return unknown


def transition_verb(old_phase: str, new_phase: str) -> str:
    """Direction of a phase change by severity rank: ``improved`` (healthier),
    ``degraded`` (worse), or ``changed`` (same rank, different phase)."""
    old_rank = phase_rank(old_phase)
    new_rank = phase_rank(new_phase)
    if new_rank < old_rank:
        return "improved"
    if new_rank > old_rank:
        return "degraded"
    return "changed"
