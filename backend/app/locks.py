"""Process-global lock ordering — the single source of truth for tailbale's
cross-cutting concurrency invariant.

Every place in the backend that holds more than one of these locks at once MUST
acquire them in the fixed tier order below. Reversing the order is the AB-BA
cycle this module exists to prevent.

DEADLOCK INVARIANT (whole process) — acquire OUTERMOST first, release in the
reverse order:

    tier 1   _SERVICE_LIFECYCLE_MUTEX                              FIRST
             (process-global; serializes service create/update/disable/
             delete/reset against each other and against reconcile_service)
    tier 2   TWO co-held sub-tiers, always acquired 2a -> 2b:      SECOND
             tier 2a  per-service reconcile lock
                      (service_reconcile_lock / reconcile_lock_for)
             tier 2b  _GLOBAL_OPS_MUTEX
                      (global_ops_lock; the service-less reconcile slot)
    tier 3   database._DB_WRITE_MUTEX                              INNERMOST
             (database.db_write_lock / db_write_section)

NEVER acquire a tier-2 lock and THEN the tier-1 lifecycle mutex; NEVER acquire
the tier-3 DB write lock and THEN a tier-1/2 lock.

Tier 2 is NOT a single mutually-exclusive slot: it is two ordered sub-tiers that
provably CO-HOLD. Most operations take just ONE of them — either the per-service
reconcile lock (tier 2a, the common reconcile / lifecycle / edge-action path) or
_GLOBAL_OPS_MUTEX (tier 2b, service-less reconcile-adjacent ops whose service is
already gone, e.g. orphan-DNS cleanup). But the DNS step (reconciler._ensure_dns)
holds BOTH at once, ALWAYS in the fixed order 2a -> 2b (per-service reconcile lock
FIRST, then _GLOBAL_OPS_MUTEX), to serialize a DNS create/update against the
orphan-DNS-cleanup job. That fixed 2a -> 2b order is what prevents an AB-BA cycle
between the two sub-tiers, and it is deadlock-free: the only other _GLOBAL_OPS_MUTEX
(tier 2b) holder — routers/jobs.py orphan cleanup, via lifecycle_then_global_ops —
never also takes a per-service reconcile lock (tier 2a), so the reverse 2b -> 2a
order never occurs. A caller that needs BOTH sub-tiers MUST take 2a before 2b.

LEAF locks live in their own modules and are NEVER held while taking a
tier-1/2 lock, so they cannot take part in a deadlock cycle:
  * lego_runner._LEGO_MUTEX     — serializes lego/ACME shared account+store.
  * image_builder._BUILD_LOCK  — serializes edge-image rebuilds.

The reconcile lock is PER SERVICE (not one process-global mutex) so a single
service's certificate issuance — lego DNS-01 can hold the lock for minutes — no
longer stalls reconcile, the periodic sweep, or operator actions on OTHER
services. Each per-service lock is reentrant (RLock) because one thread nests
acquisitions for the same service:
    reconcile_service -> process_service_cert -> _persist_status.
"""

from __future__ import annotations

import contextlib
import threading

# --- Tier 1: service-lifecycle mutex ---------------------------------------
#
# Acquired FIRST in the global order. A reconcile lock (tier 2) may be taken
# only AFTER this mutex, never before it.
_SERVICE_LIFECYCLE_MUTEX = threading.RLock()

# --- Tier 2a: per-service reconcile locks ----------------------------------
_RECONCILE_LOCKS: dict[str, threading.RLock] = {}
_RECONCILE_LOCKS_MUTEX = threading.Lock()

# --- Tier 2b: service-less global-ops mutex --------------------------------
#
# Service-less / global reconcile-adjacent operations (e.g. orphaned-DNS cleanup
# that runs AFTER its service is already deleted, so there is no per-service lock
# to take) serialize on this lock. It is the SECOND tier-2 sub-tier (2b): when an
# op co-holds both, the per-service reconcile lock (2a) is taken FIRST, then this
# one — always AFTER _SERVICE_LIFECYCLE_MUTEX, NEVER before it, and NEVER 2b -> 2a.
_GLOBAL_OPS_MUTEX = threading.RLock()


def reconcile_lock_for(service_id: str) -> threading.RLock:
    """Return the reentrant reconcile lock for *service_id*, creating it on first use.

    The tiny registry meta-lock is held only long enough to fetch-or-create the
    per-service lock; it is never held while the per-service lock (or any other
    lock) is acquired, so it cannot take part in a deadlock cycle.
    """
    with _RECONCILE_LOCKS_MUTEX:
        lock = _RECONCILE_LOCKS.get(service_id)
        if lock is None:
            lock = threading.RLock()
            _RECONCILE_LOCKS[service_id] = lock
        return lock


def forget_reconcile_lock(service_id: str) -> None:
    """Drop a DELETED service's reconcile-lock entry so the registry stays bounded.

    Caller contract: call ONLY once the service row is known gone — either a
    delete the caller committed (the lifecycle delete path, holding
    _SERVICE_LIFECYCLE_MUTEX) or a load that found the row absent (the
    reconcile / probe / health-sweep "service gone" paths, holding only the
    per-service reconcile lock). Hold one of those locks so the pop is
    serialized against this id's re-creation; NEVER take _SERVICE_LIFECYCLE_MUTEX
    from a reconcile-lock-holding path to satisfy this — that inverts the
    documented tier-1 -> tier-2 order and risks an AB-BA deadlock. After the row
    is gone, any later reconcile_lock_for(service_id) creates a fresh lock that
    can only guard work on an already-absent service (reconcile_one loads the
    row, finds it gone, no-ops), so there is no lost-mutual-exclusion window.
    The meta-lock is a LEAF: no other lock is ever acquired while it is held, so
    it cannot take part in a deadlock cycle even where it is acquired WHILE a
    per-service reconcile lock is held (the post-delete reconcile/probe paths do
    exactly that), preserving the deadlock-free invariant.
    """
    with _RECONCILE_LOCKS_MUTEX:
        _RECONCILE_LOCKS.pop(service_id, None)


@contextlib.contextmanager
def service_reconcile_lock(service_id: str):
    """Hold the per-service reconcile lock for *service_id* for the block.

    Honors the global lock order: a caller that also needs the lifecycle mutex
    MUST acquire ``_SERVICE_LIFECYCLE_MUTEX`` FIRST and only then enter this
    context — never the reverse.
    """
    with reconcile_lock_for(service_id):
        yield


@contextlib.contextmanager
def try_service_reconcile_lock(service_id: str):
    """Try to hold *service_id*'s per-service reconcile lock WITHOUT blocking.

    Non-blocking counterpart of :func:`service_reconcile_lock`: it does a single
    ``acquire(blocking=False)`` on the same per-service lock and yields whether
    it won it —

      * ``True``  — the lock was free; this block holds it (released on exit).
      * ``False`` — the lock is already held (a reconcile / op is in progress for
        this service); nothing was acquired, so nothing is released.

    The periodic health sweep uses this so a service whose lock is held by a
    minutes-long op (e.g. lego DNS-01) is SKIPPED this round instead of stalling
    the whole sweep — its status is being actively managed by the in-progress op,
    so it stays fresh; the next sweep retries it. Honors the same global lock
    order as :func:`service_reconcile_lock`.
    """
    lock = reconcile_lock_for(service_id)
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


@contextlib.contextmanager
def lifecycle_then_reconcile(service_id: str):
    """Acquire the documented tier-1 -> tier-2 pair for *service_id*.

    Convenience for the common service-lifecycle pattern: take
    ``_SERVICE_LIFECYCLE_MUTEX`` FIRST, then the per-service reconcile lock — the
    exact global order documented above. Equivalent to::

        with _SERVICE_LIFECYCLE_MUTEX, service_reconcile_lock(service_id):
            ...
    """
    with _SERVICE_LIFECYCLE_MUTEX, service_reconcile_lock(service_id):
        yield


@contextlib.contextmanager
def global_ops_lock():
    """Hold the service-less ``_GLOBAL_OPS_MUTEX`` (tier 2b) for the block.

    For reconcile-adjacent operations with no per-service lock to take (e.g.
    orphaned-DNS cleanup whose service is already deleted). This is the SECOND
    tier-2 sub-tier (2b), NOT a slot mutually exclusive with the per-service
    reconcile lock (2a): an op that co-holds both takes 2a FIRST, then this. A
    caller that also needs the lifecycle mutex MUST take ``_SERVICE_LIFECYCLE_MUTEX``
    FIRST — see :func:`lifecycle_then_global_ops`.
    """
    with _GLOBAL_OPS_MUTEX:
        yield


@contextlib.contextmanager
def lifecycle_then_global_ops():
    """Acquire the documented tier-1 -> tier-2 pair for a service-less op.

    Service-less counterpart of :func:`lifecycle_then_reconcile`: take
    ``_SERVICE_LIFECYCLE_MUTEX`` FIRST, then ``_GLOBAL_OPS_MUTEX`` — the exact
    global order documented above. Equivalent to::

        with _SERVICE_LIFECYCLE_MUTEX, _GLOBAL_OPS_MUTEX:
            ...
    """
    with _SERVICE_LIFECYCLE_MUTEX, _GLOBAL_OPS_MUTEX:
        yield


@contextlib.contextmanager
def lifecycle_lock():
    """Hold only the tier-1 ``_SERVICE_LIFECYCLE_MUTEX`` for the block.

    For service-lifecycle ops that serialize against create/update/disable/
    delete/reconcile but take no tier-2 lock of their own (e.g. the developer
    reset-all sweep, which deletes services via ``_delete_service_record`` that
    acquires its own per-service locks). Equivalent to::

        with _SERVICE_LIFECYCLE_MUTEX:
            ...
    """
    with _SERVICE_LIFECYCLE_MUTEX:
        yield
