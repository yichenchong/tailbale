"""Failure, locking, and durability tests for the reconciler."""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.locks import _RECONCILE_LOCKS, forget_reconcile_lock, reconcile_lock_for
from app.models.event import Event
from app.models.service import Service
from app.models.service_status import ServiceStatus
from app.reconciler import steps
from app.reconciler.reconciler import reconcile_service
from tests._reconciler_helpers import (
    _P_AGGREGATE,
    _P_CERT,
    _P_CREATE_EDGE,
    _P_FIND_EDGE,
    _P_HEALTH,
    _P_NETWORK,
    _P_RELOAD,
    _P_RENDER,
    _P_SECRET,
    _P_START,
    _P_TS_IP,
    _P_WRITE,
)
from tests._services_helpers import _create_service_in_db as _create_service


class TestReconcileFailuresAndLocks:
    @patch(_P_SECRET)
    def test_fails_without_ts_authkey(self, mock_secret, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        mock_secret.return_value = None  # no auth key

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert "auth key" in result["error"].lower()

        status = db_session.get(ServiceStatus, svc.id)
        assert status.phase == "failed"

        events = db_session.query(Event).filter(Event.kind == "reconcile_failed").all()
        assert len(events) == 1

    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
    @patch(_P_SECRET)
    def test_handles_network_failure(self, mock_secret, mock_network, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_network.side_effect = RuntimeError("Docker not available")

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "failed"
        assert result["error"] is not None

    @patch(_P_SECRET)
    def test_marks_service_failed_after_locked_status_update(self, mock_secret, db_session, tmp_data_dir):
        svc = _create_service(db_session)
        service_id = svc.id
        mock_secret.return_value = "ts-key"

        original_flush = db_session.flush
        raised = False

        def flush_with_lock(*args, **kwargs):
            nonlocal raised
            if not raised and (db_session.dirty or db_session.new):
                raised = True
                raise OperationalError(
                    "UPDATE service_status SET phase=? WHERE service_id=?",
                    ("validating", service_id),
                    Exception("database is locked"),
                )
            return original_flush(*args, **kwargs)

        db_session.flush = flush_with_lock
        try:
            result = reconcile_service(db_session, svc)
        finally:
            db_session.flush = original_flush

        assert result["phase"] == "failed"
        assert "database is locked" in result["error"]

        status = db_session.get(ServiceStatus, service_id)
        assert status is not None
        assert status.phase == "failed"
        assert "database is locked" in (status.message or "")

        events = db_session.query(Event).filter(Event.kind == "reconcile_failed").all()
        assert len(events) == 1

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_serializes_overlapping_reconciles(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        tmp_data_dir,
    ):
        db_path = tmp_data_dir / "reconcile-overlap.sqlite"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        Base.metadata.create_all(bind=engine)
        TestSession = sessionmaker(bind=engine)

        seed_db = TestSession()
        try:
            svc = _create_service(seed_db)
            service_id = svc.id
        finally:
            seed_db.close()

        mock_secret.return_value = "ts-key"
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "edge_id"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        active_calls = 0
        max_active_calls = 0
        call_lock = threading.Lock()

        def slow_network(*args, **kwargs):
            nonlocal active_calls, max_active_calls
            with call_lock:
                active_calls += 1
                max_active_calls = max(max_active_calls, active_calls)
            try:
                time.sleep(0.1)
                return "net123", args[1]
            finally:
                with call_lock:
                    active_calls -= 1
        mock_network.side_effect = slow_network

        errors: list[Exception] = []
        results: list[dict] = []

        def run_reconcile():
            db = TestSession()
            try:
                svc = db.get(Service, service_id)
                assert svc is not None
                results.append(reconcile_service(db, svc))
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close()

        first = threading.Thread(target=run_reconcile)
        second = threading.Thread(target=run_reconcile)
        first.start()
        second.start()
        first.join()
        second.join()

        engine.dispose()

        assert errors == []
        assert len(results) == 2
        assert max_active_calls == 1
        assert all(result["phase"] == "healthy" for result in results)


class TestForgetReconcileLock:
    """Registry lifecycle: forget_reconcile_lock keeps _RECONCILE_LOCKS bounded so
    deleted services no longer leak per-service RLock entries forever."""

    def test_forget_removes_entry_and_allows_fresh_lock(self):

        sid = "svc_forget_x"
        try:
            lock1 = reconcile_lock_for(sid)
            assert _RECONCILE_LOCKS.get(sid) is lock1

            forget_reconcile_lock(sid)
            assert sid not in _RECONCILE_LOCKS

            # A later acquire creates a brand-new entry, not the dropped object.
            lock2 = reconcile_lock_for(sid)
            assert _RECONCILE_LOCKS.get(sid) is lock2
            assert lock2 is not lock1
        finally:
            _RECONCILE_LOCKS.pop(sid, None)

    def test_forget_unknown_id_is_noop(self):

        sid = "svc_never_seen_q"
        assert sid not in _RECONCILE_LOCKS
        # Must not raise KeyError for an id that was never registered.
        forget_reconcile_lock(sid)
        assert sid not in _RECONCILE_LOCKS


class TestMarkerWriteDurability:
    """AR8: the crash-desync guard markers (.reload_pending / .cert_loaded) must
    be published through fsutil.atomic_write_text (fsync + atomic rename), not a
    bare write_text that a power-loss could lose."""

    @patch(_P_HEALTH)
    @patch(_P_AGGREGATE)
    @patch(_P_RELOAD)
    @patch(_P_TS_IP)
    @patch(_P_START)
    @patch(_P_FIND_EDGE)
    @patch(_P_CREATE_EDGE)
    @patch(_P_NETWORK, return_value=("net123", "upstream123"))
    @patch(_P_CERT)
    @patch(_P_WRITE)
    @patch(_P_RENDER)
    @patch(_P_SECRET)
    def test_markers_written_via_atomic_write_text(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health,
        db_session, tmp_data_dir,
    ):


        svc = _create_service(db_session)
        mock_secret.return_value = "ts-key"
        mock_render.return_value = "changed config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"
        mock_reload.return_value = "ok"

        cert_dir = Path(tmp_data_dir) / "certs" / svc.hostname / "current"
        cert_dir.mkdir(parents=True)
        (cert_dir / "fullchain.pem").write_text("CERT-V1")

        real_atomic = steps.atomic_write_text
        written: list[str] = []

        def _spy(path, text, **kwargs):
            written.append(Path(path).name)
            return real_atomic(path, text, **kwargs)

        with patch.object(steps, "atomic_write_text", side_effect=_spy):
            result = reconcile_service(db_session, svc)

        assert result["caddy_reloaded"] is True
        # New config -> .reload_pending written durably; successful reload ->
        # .cert_loaded fingerprint recorded durably.
        assert ".reload_pending" in written
        assert ".cert_loaded" in written
        gen_dir = Path(tmp_data_dir) / "generated" / svc.id
        assert (gen_dir / ".cert_loaded").read_text(encoding="utf-8").strip() != ""
