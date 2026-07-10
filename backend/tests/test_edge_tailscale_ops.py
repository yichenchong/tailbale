"""Tests for edge Tailscale IP detection."""

import json
from unittest.mock import MagicMock, patch

from app.edge.tailscale_ops import detect_tailscale_ip
from tests._edge_helpers import _ConnectStubMixin


class TestDetectTailscaleIp(_ConnectStubMixin):
    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_session._find_edge_container")
    def test_detects_ip_via_tailscale_ip(self, mock_find, mock_sleep):

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (0, b"100.64.0.1\n")
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.1"

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_session._find_edge_container")
    def test_detects_ip_via_status_json(self, mock_find, mock_sleep):

        mock_container = MagicMock()
        mock_container.status = "running"
        # tailscale ip -4 fails
        status_json = json.dumps({
            "Self": {
                "TailscaleIPs": ["100.64.0.2", "fd7a::1"]
            }
        }).encode()
        mock_container.exec_run.side_effect = [
            (1, b""),  # tailscale ip fails
            (0, status_json),  # tailscale status --json succeeds
        ]
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.2"

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_session._find_edge_container")
    def test_retries_on_failure(self, mock_find, mock_sleep):

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.side_effect = [
            (1, b""),  # attempt 1: tailscale ip fails
            (1, b""),  # attempt 1: tailscale status fails
            (0, b"100.64.0.3\n"),  # attempt 2: tailscale ip succeeds
        ]
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=2, retry_delay=0)
        assert result == "100.64.0.3"

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_session._find_edge_container")
    def test_returns_none_after_max_retries(self, mock_find, mock_sleep):

        mock_container = MagicMock()
        mock_container.status = "running"
        mock_container.exec_run.return_value = (1, b"not ready")
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=2, retry_delay=0)
        assert result is None

    @patch("app.edge.container_session._find_edge_container")
    def test_returns_none_if_container_not_found(self, mock_find):

        mock_find.return_value = None
        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result is None

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_session._find_edge_container")
    def test_ignores_non_tailscale_ips(self, mock_find, mock_sleep):

        mock_container = MagicMock()
        mock_container.status = "running"
        # Returns a non-Tailscale IP first, then status with Tailscale IP
        mock_container.exec_run.side_effect = [
            (0, b"192.168.1.1\n"),  # Not a 100.x IP
            (0, json.dumps({"Self": {"TailscaleIPs": ["100.64.0.5"]}}).encode()),
        ]
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=1)
        assert result == "100.64.0.5"

    @patch("app.backoff.time.sleep")
    @patch("app.edge.container_session._find_edge_container")
    def test_returns_none_when_container_never_running(self, mock_find, mock_sleep):
        """The container exists but never reaches 'running' (e.g. stuck exited):
        the pre-loop guard bails with None and never exec's tailscale inside a
        non-running container."""

        mock_container = MagicMock()
        mock_container.status = "exited"
        mock_find.return_value = mock_container

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=2, retry_delay=0)

        assert result is None
        mock_container.exec_run.assert_not_called()

    @patch("app.backoff.time.sleep")
    @patch("app.edge.tailscale_ops._wait_for_running")
    @patch("app.edge.container_session._find_edge_container")
    def test_skips_exec_when_container_leaves_running_state(
        self, mock_find, mock_wait, mock_sleep
    ):
        """The container clears the initial 'running' gate but then leaves running
        state mid-detection. Each retry re-checks (container.reload + a bounded
        _wait_for_running) and skips the exec rather than shelling into a
        non-running container; when it never recovers, every attempt skips and
        detection returns None without ever exec-ing tailscale."""

        mock_container = MagicMock()
        mock_container.status = "restarting"  # not 'running' on the in-loop recheck
        mock_find.return_value = mock_container
        # Entry gate (True) admits the loop; every in-loop recheck reports the
        # container never regained 'running' (False).
        mock_wait.side_effect = [True, False, False]

        result = detect_tailscale_ip("svc_123", "edge_test", max_retries=2, retry_delay=0)

        assert result is None
        mock_container.exec_run.assert_not_called()
        # State was re-checked on every attempt (entry gate + one per retry).
        assert mock_wait.call_count == 3
        mock_container.reload.assert_called()
