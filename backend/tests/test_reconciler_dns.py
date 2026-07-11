"""DNS orchestration tests for the reconciler engine."""

import threading
from unittest.mock import MagicMock, patch

from app.locks import _GLOBAL_OPS_MUTEX
from app.models.service_status import ServiceStatus
from app.reconciler.reconciler import reconcile_service
from app.settings_store import set_setting
from tests._reconciler_helpers import (
    _P_AGGREGATE,
    _P_CERT,
    _P_CREATE_EDGE,
    _P_DNS,
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


class TestReconcileDnsOrchestration:
    @patch(_P_DNS)
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
    def test_dns_failure_does_not_block(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health, mock_dns,
        db_session, tmp_data_dir,
    ):

        svc = _create_service(db_session)

        mock_secret.side_effect = lambda name: {"tailscale_authkey": "ts-key", "cloudflare_token": "cf-tok"}.get(name)
        set_setting(db_session, "cf_zone_id", "zone1")

        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_dns.side_effect = RuntimeError("CF API down")
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        result = reconcile_service(db_session, svc)

        # Should still complete despite DNS failure
        assert result["phase"] == "healthy"

    @patch(_P_DNS)
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
    def test_dns_reconcile_runs_under_global_ops_lock(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health, mock_dns,
        db_session, tmp_data_dir,
    ):
        # RC-R2-1 regression: the reconcile DNS step MUST call reconcile_dns while
        # holding _GLOBAL_OPS_MUTEX (tier 2b), so a concurrent orphaned-DNS cleanup
        # retry (jobs.py, which holds the same mutex) can never delete a record
        # this reconcile is mid-flight creating. This invariant was introduced by
        # the AR16 step extraction (steps.ensure_dns wraps reconcile_dns in
        # global_ops_lock); nothing else pins it, so a future edit could silently
        # drop the lock and reintroduce the delete-mid-create race with the whole
        # suite still green. Fails if the global_ops_lock() wrapper is removed.

        svc = _create_service(db_session)
        mock_secret.side_effect = lambda name: {
            "tailscale_authkey": "ts-key", "cloudflare_token": "cf-tok",
        }.get(name)
        set_setting(db_session, "cf_zone_id", "zone1")
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = "100.64.0.1"
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        # _GLOBAL_OPS_MUTEX is a reentrant lock, so a same-thread acquire always
        # succeeds and can't tell whether reconcile holds it. Probe from a FRESH
        # thread: a non-blocking acquire there FAILS iff some other thread (the
        # reconcile) currently holds it. Record that observation at call time.
        observed = {}

        def _dns_side_effect(*_args, **_kwargs):
            result = {"got": None}

            def _probe():
                got = _GLOBAL_OPS_MUTEX.acquire(blocking=False)
                result["got"] = got
                if got:
                    _GLOBAL_OPS_MUTEX.release()

            t = threading.Thread(target=_probe)
            t.start()
            t.join()
            observed["held_during_dns"] = not result["got"]

        mock_dns.side_effect = _dns_side_effect

        result = reconcile_service(db_session, svc)

        assert result["phase"] == "healthy"
        mock_dns.assert_called_once()
        # The global-ops mutex was held for the whole reconcile_dns call.
        assert observed.get("held_during_dns") is True
        # And it is released once the reconcile finishes.
        probe_after = {"got": None}

        def _probe_after():
            got = _GLOBAL_OPS_MUTEX.acquire(blocking=False)
            probe_after["got"] = got
            if got:
                _GLOBAL_OPS_MUTEX.release()

        t_after = threading.Thread(target=_probe_after)
        t_after.start()
        t_after.join()
        assert probe_after["got"] is True

    @patch(_P_DNS)
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
    def test_no_tailscale_ip_skips_dns_but_completes(
        self, mock_secret, mock_render, mock_write, mock_cert,
        mock_network, mock_create_edge, mock_find_edge, mock_start,
        mock_ts_ip, mock_reload, mock_aggregate, mock_health, mock_dns,
        db_session, tmp_data_dir,
    ):
        # When the edge never reports a Tailscale IP, the DNS step MUST be skipped
        # (no record can point at a missing IP) yet the reconcile still proceeds to
        # health and completes. CF token + zone are configured so the ONLY reason
        # DNS is skipped is the absent IP, isolating the `ts_ip` guard branch.

        svc = _create_service(db_session)
        mock_secret.side_effect = lambda name: {
            "tailscale_authkey": "ts-key", "cloudflare_token": "cf-tok",
        }.get(name)
        set_setting(db_session, "cf_zone_id", "zone1")
        mock_render.return_value = "config"
        edge = MagicMock()
        edge.id = "e1"
        edge.status = "running"
        mock_find_edge.return_value = edge
        mock_ts_ip.return_value = None  # edge never surfaced a Tailscale IP
        mock_health.return_value = {}
        mock_aggregate.return_value = "healthy"

        result = reconcile_service(db_session, svc)

        assert result["tailscale_ip"] is None
        mock_dns.assert_not_called()
        assert result["phase"] == "healthy"
        status = db_session.get(ServiceStatus, svc.id)
        assert status.tailscale_ip is None
