"""Shared retry/backoff vocabulary.

A single home for the "wait longer each time, but never past a ceiling" retry
shape that background subsystems otherwise reinvent constant-for-constant.
Centralising it gives every caller the same vocabulary — ``base``, ``cap``,
optional ``jitter`` — and one place to reason about thundering-herd avoidance.

Cadences across the codebase, in this vocabulary (documented here so the
policies live in one place even where the math is trivial):

- ``reconciler.probe_retry`` — capped exponential, ``base=15s``, ``cap=3600s``
  (15, 30, 60, ... 1h ceiling), up to ``MAX_RETRIES`` attempts. Jitter is OFF
  by default so the schedule stays deterministic (and test-pinned); it is
  opt-in / injectable per call.
- ``reconciler.reconcile_loop`` — fixed 30s backoff after a sweep error. A
  degenerate capped exponential where ``base == cap`` (no growth), routed
  through :func:`capped_exponential` so the policy reads in the same terms.
- ``certs.renewal_task`` — fixed cadences, not exponential: 6h retry interval
  after a per-cert failure, 86400s (24h) between full renewal scans. Stated in
  this vocabulary as ``base == cap`` fixed intervals; left as plain constants
  because they are durations stored/slept directly, with no per-attempt growth.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable


def capped_exponential(
    attempt: int,
    *,
    base: float,
    cap: float,
    jitter: float = 0.0,
    rng: random.Random | None = None,
) -> float:
    """Backoff delay for a zero-indexed ``attempt``.

    The base delay doubles each attempt — ``base * 2 ** attempt`` — clamped to
    ``cap``. With ``base=15, cap=3600`` the schedule is 15, 30, 60, 120, ...
    saturating at 3600. ``attempt`` below 0 is treated as 0; ``base == cap``
    (or ``cap < base``) yields a fixed ``cap`` every time.

    With ``jitter > 0`` the clamped delay is scaled by a random factor in
    ``[1 - jitter, 1 + jitter]`` to de-synchronise concurrent retries. Jitter
    is OFF by default, so the result is deterministic; pass ``rng`` (a
    :class:`random.Random`) to control the randomness source, e.g. in tests.
    A ``jitter >= 1`` (e.g. "full jitter") can drive that factor to/below zero,
    so the jittered result is floored at ``0.0`` — a delay is a duration and a
    negative one is meaningless (a ``sleep`` would reject it, a scheduled
    "retry at now+delay" would land in the past).
    """
    if attempt < 0:
        attempt = 0

    if base <= 0:
        delay: float = 0.0
    elif cap <= base:
        # No room to grow — a fixed interval expressed in backoff terms.
        delay = cap
    else:
        # Past this many doublings the result is just the cap; clamp the
        # exponent so an absurd ``attempt`` never builds a huge intermediate.
        max_attempt = math.ceil(math.log2(cap / base))
        delay = min(base * (2 ** min(attempt, max_attempt)), cap)

    if jitter:
        source = rng if rng is not None else random
        delay = max(0.0, delay * (1.0 + source.uniform(-jitter, jitter)))

    return delay


_module_logger = logging.getLogger(__name__)


async def run_periodic(
    *,
    name: str,
    startup_delay: float,
    interval_fn: Callable[[], float],
    work: Callable[[], Awaitable[object]],
    on_error: Callable[[BaseException], float] | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Run ``work`` forever on a periodic cadence with uniform loop plumbing.

    Owns the ``sleep(startup) -> while True: try work; sleep(interval) except
    cancel: re-raise; except Exception: log + backoff`` skeleton that every
    background loop otherwise reimplements. Each caller supplies:

    - ``name`` — used verbatim in the ``"<name> started"`` / ``"<name>
      cancelled"`` / ``"Error in <name>"`` log lines.
    - ``startup_delay`` — seconds to sleep before the first iteration so the app
      is fully ready.
    - ``interval_fn`` — called after each successful ``work`` to get the next
      sleep, so a loop can read a *dynamic* interval (e.g. from settings) every
      pass. It must not raise.
    - ``work`` — the async callable doing one iteration's work (including its own
      per-iteration logging).
    - ``on_error`` — optional ``exc -> backoff_seconds``. When ``work`` raises a
      non-cancellation error it is logged (with traceback) and the loop sleeps
      the returned backoff before retrying. When omitted, the backoff defaults
      to ``interval_fn()`` (the fixed-cadence loops keep retrying on their normal
      interval; the reconcile/health loops pass a shorter error backoff).

    Cancellation (``asyncio.CancelledError``) is logged once and re-raised so the
    task terminates cleanly on shutdown — never swallowed by the error branch.
    """
    log = logger if logger is not None else _module_logger
    await asyncio.sleep(startup_delay)
    log.info("%s started", name)

    while True:
        try:
            await work()
            await asyncio.sleep(interval_fn())
        except asyncio.CancelledError:
            log.info("%s cancelled", name)
            raise
        except Exception as exc:
            log.error("Error in %s", name, exc_info=True)
            backoff = on_error(exc) if on_error is not None else interval_fn()
            await asyncio.sleep(backoff)
