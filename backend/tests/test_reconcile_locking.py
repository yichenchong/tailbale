"""Per-service reconcile-lock registry: identity, reentrancy, parallelism, order.

Fix #1 replaced the single process-global ``_RECONCILE_MUTEX`` with a registry of
per-service reentrant locks (``reconcile_lock_for`` / ``service_reconcile_lock``)
plus a dedicated ``_GLOBAL_OPS_MUTEX`` for service-less operations. These tests
pin the registry's identity guarantees, its reentrancy, the per-service
parallelism that keeps a minutes-long cert issuance for one service from
stalling another, and the documented ``_SERVICE_LIFECYCLE_MUTEX`` -> reconcile
lock ordering.
"""

import threading

from app.locks import (
    _GLOBAL_OPS_MUTEX,
    _RECONCILE_LOCKS,
    _SERVICE_LIFECYCLE_MUTEX,
    forget_reconcile_lock,
    global_ops_lock,
    lifecycle_then_global_ops,
    reconcile_lock_for,
    service_reconcile_lock,
    try_service_reconcile_lock,
)
from app.models.service import Service
from app.reconciler.reconciler import reconcile_service


def test_distinct_service_ids_get_distinct_locks():
    assert reconcile_lock_for("svc_distinct_a") is not reconcile_lock_for("svc_distinct_b")


def test_same_service_id_returns_the_same_lock():
    first = reconcile_lock_for("svc_identity")
    second = reconcile_lock_for("svc_identity")
    assert first is second


def test_service_lock_is_reentrant_on_one_thread():
    # An RLock lets one thread nest acquisitions for the same service:
    # reconcile_service -> process_service_cert -> _persist_status all lock it.
    lock = reconcile_lock_for("svc_reentrant")
    with lock:
        assert lock.acquire(blocking=False)
        lock.release()


def test_context_manager_holds_the_registry_lock():
    # The context manager must hold the very object the registry hands out, so
    # callers and the reconciler serialize on one lock per service.
    lock = reconcile_lock_for("svc_ctx")
    with service_reconcile_lock("svc_ctx"):
        # Same thread, reentrant: succeeds only because it is the same RLock.
        assert lock.acquire(blocking=False)
        lock.release()


def test_distinct_ids_do_not_block_across_threads():
    # The point of Fix #1: one service holding its lock (e.g. a minutes-long cert
    # issuance) must NOT block reconcile/actions for a different service.
    other_acquired = threading.Event()
    release_other = threading.Event()

    def hold_other():
        with service_reconcile_lock("svc_par_b"):
            other_acquired.set()
            release_other.wait(2)

    with service_reconcile_lock("svc_par_a"):
        worker = threading.Thread(target=hold_other)
        worker.start()
        assert other_acquired.wait(1), "a different service's lock must be free"
        release_other.set()
        worker.join(2)
    assert not worker.is_alive()


def test_same_id_serializes_across_threads():
    same_acquired = threading.Event()

    def grab_same():
        # Blocks until the main thread releases the lock for this service.
        with service_reconcile_lock("svc_serial"):
            same_acquired.set()

    with service_reconcile_lock("svc_serial"):
        worker = threading.Thread(target=grab_same)
        worker.start()
        assert not same_acquired.wait(0.2), "same service must serialize across threads"
    # Lock released here; the worker can now acquire it.
    assert same_acquired.wait(2)
    worker.join(2)
    assert not worker.is_alive()


def test_lifecycle_then_reconcile_is_the_documented_order():
    # Documented global order: _SERVICE_LIFECYCLE_MUTEX FIRST, then the per-service
    # reconcile lock. Reconcile takes ONLY the inner per-service lock (never the
    # lifecycle mutex); a lifecycle op takes the lifecycle mutex FIRST then the
    # inner lock. Prove a lifecycle op blocks on the inner lock a reconcile holds,
    # then completes once it is released -- consistent order, no AB-BA deadlock.
    reconcile_holding = threading.Event()
    let_reconcile_finish = threading.Event()
    lifecycle_done = threading.Event()
    errors: list[Exception] = []

    def reconcile_only_inner():
        try:
            with service_reconcile_lock("svc_order"):
                reconcile_holding.set()
                let_reconcile_finish.wait(3)
        except Exception as exc:
            errors.append(exc)

    def lifecycle_outer_then_inner():
        try:
            with _SERVICE_LIFECYCLE_MUTEX, service_reconcile_lock("svc_order"):
                lifecycle_done.set()
        except Exception as exc:
            errors.append(exc)

    reconciler = threading.Thread(target=reconcile_only_inner)
    reconciler.start()
    assert reconcile_holding.wait(2)

    lifecycle = threading.Thread(target=lifecycle_outer_then_inner)
    lifecycle.start()
    # Blocked on the inner per-service lock the reconcile thread holds.
    assert not lifecycle_done.wait(0.3)

    let_reconcile_finish.set()
    assert lifecycle_done.wait(2)
    reconciler.join(2)
    lifecycle.join(2)
    assert errors == []


def test_global_ops_lock_is_distinct_and_orders_after_lifecycle():
    # Service-less ops (orphan-DNS cleanup in jobs.py) serialize on the dedicated
    # _GLOBAL_OPS_MUTEX, which is not any per-service lock and is acquired AFTER
    # the lifecycle mutex -- the same outer->inner order, so acquiring both in
    # that order never deadlocks.
    assert _GLOBAL_OPS_MUTEX is not reconcile_lock_for("svc_global")
    with _SERVICE_LIFECYCLE_MUTEX, _GLOBAL_OPS_MUTEX:
        pass


def test_forget_then_reconcile_of_deleted_service_does_not_releak_lock(db_session):
    # Regression for the post-forget re-creation race: forget_reconcile_lock()
    # drops a deleted service's entry, but an outliving reconcile then calls
    # service_reconcile_lock(id) for the now-absent id, which reconcile_lock_for()
    # re-inserts. Without re-forgetting in the 'service gone' branch that entry
    # leaks for the whole process lifetime, breaking the 'stays bounded' guarantee.
    sid = "svc_forget_releak"
    ghost = Service(id=sid, name="ghost")  # transient: never persisted, so absent in DB

    # The delete path forgot the lock while holding the lifecycle mutex.
    forget_reconcile_lock(sid)
    assert sid not in _RECONCILE_LOCKS

    # An outliving reconcile now races in for the deleted id: acquiring the lock
    # re-creates the registry entry, then the 'service gone' branch must fire.
    result = reconcile_service(db_session, ghost)
    assert result["phase"] == "deleted"

    # The orphan entry must NOT survive -- the registry stays bounded by
    # live + in-flight ids, not every id ever reconciled after deletion.
    assert sid not in _RECONCILE_LOCKS


def _acquired_from_other_thread(lock) -> bool:
    """Whether a FRESH thread can non-blocking-acquire *lock* right now.

    The tier mutexes are RLocks (reentrant on the calling thread), so only a
    different thread reveals that a lock is currently held.
    """
    result: dict[str, bool] = {}

    def attempt():
        got = lock.acquire(blocking=False)
        result["got"] = got
        if got:
            lock.release()

    t = threading.Thread(target=attempt)
    t.start()
    t.join(timeout=5)
    return result["got"]


def test_global_ops_lock_cm_holds_the_global_ops_mutex():
    # ARC6: global_ops_lock() is the public CM wrapping _GLOBAL_OPS_MUTEX. Pure
    # encapsulation -- while held, no other thread can take that mutex; released
    # on exit.
    assert _acquired_from_other_thread(_GLOBAL_OPS_MUTEX)
    with global_ops_lock():
        assert not _acquired_from_other_thread(_GLOBAL_OPS_MUTEX)
    assert _acquired_from_other_thread(_GLOBAL_OPS_MUTEX)


def test_lifecycle_then_global_ops_holds_both_tiers():
    # ARC6: lifecycle_then_global_ops() wraps the documented tier-1 -> tier-2 pair
    # (_SERVICE_LIFECYCLE_MUTEX then _GLOBAL_OPS_MUTEX) -- the exact acquisition
    # the raw `with _SERVICE_LIFECYCLE_MUTEX, _GLOBAL_OPS_MUTEX:` made. While held,
    # BOTH are unavailable to other threads.
    with lifecycle_then_global_ops():
        assert not _acquired_from_other_thread(_SERVICE_LIFECYCLE_MUTEX)
        assert not _acquired_from_other_thread(_GLOBAL_OPS_MUTEX)
    assert _acquired_from_other_thread(_SERVICE_LIFECYCLE_MUTEX)
    assert _acquired_from_other_thread(_GLOBAL_OPS_MUTEX)


def test_try_service_reconcile_lock_acquires_when_free():
    # REC3: when the per-service lock is free the CM yields True and holds it for
    # the block, releasing on exit.
    sid = "svc_try_free"
    with try_service_reconcile_lock(sid) as acquired:
        assert acquired is True
        assert not _acquired_from_other_thread(reconcile_lock_for(sid))
    assert _acquired_from_other_thread(reconcile_lock_for(sid))


def test_try_service_reconcile_lock_skips_when_held_by_another_thread():
    # REC3 core: a lock held by ANOTHER thread yields False WITHOUT blocking; this
    # caller acquires/releases nothing, so the holder keeps the lock throughout.
    sid = "svc_try_contended"
    lock = reconcile_lock_for(sid)
    held = threading.Event()
    release = threading.Event()

    def holder():
        with lock:
            held.set()
            release.wait(timeout=5)

    worker = threading.Thread(target=holder)
    worker.start()
    assert held.wait(timeout=5)
    try:
        with try_service_reconcile_lock(sid) as acquired:
            assert acquired is False
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_try_service_reconcile_lock_is_reentrant_on_one_thread():
    # The per-service lock is an RLock, so the non-blocking acquire still succeeds
    # when the SAME thread already holds it -- the sweep holds it while
    # _persist_status / reconcile_one re-enter for the same service.
    sid = "svc_try_reentrant"
    with service_reconcile_lock(sid), try_service_reconcile_lock(sid) as acquired:
        assert acquired is True
