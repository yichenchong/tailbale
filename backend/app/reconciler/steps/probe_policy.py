"""Best-effort: schedule a background HTTPS-probe retry when only the probe failed."""

from __future__ import annotations

import logging

from app.health.health_checker import CRITICAL_CHECKS
from app.reconciler import probe_retry

logger = logging.getLogger(__name__)


def maybe_schedule_probe_retry(
    checks: dict, phase: str, service_id: str, socket_path: str | None
) -> None:
    """Best-effort: schedule a background HTTPS-probe retry when only the probe failed.

    Never raises into the caller: the periodic sweep re-runs health checks
    regardless, so a failure to START the retry thread (thread exhaustion, etc.)
    MUST NOT flip an already-committed reconcile outcome to failed. The
    ``probe_retry`` import is hoisted to module top so an unresolvable module
    surfaces loudly at startup instead of being silently swallowed here.
    """
    if not checks.get("https_probe_ok") and phase in ("warning", "error"):
        # Mirror aggregate_status's "missing key = not-failing" rule (a check is
        # failing only when present and falsy): default a missing critical key to
        # True so this gate can't disagree with the phase aggregation. In practice
        # every checks dict is built from ALL_CHECK_NAMES, so all keys are present.
        critical_ok = all(checks.get(check, True) for check in CRITICAL_CHECKS)
        if critical_ok:
            # Only the thread START is best-effort: schedule_probe_retry re-raises
            # if Thread.start() fails, and that must NOT corrupt the already-
            # committed status. The import lives at module top (validated at
            # startup), so it is deliberately OUTSIDE this try.
            try:
                probe_retry.schedule_probe_retry(service_id, socket_path)
            except Exception:
                logger.warning(
                    "Failed to schedule probe retry for %s",
                    service_id,
                    exc_info=True,
                )
