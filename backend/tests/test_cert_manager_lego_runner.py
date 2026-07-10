"""Tests for lego subprocess execution."""

import logging
import signal
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.certs import lego_runner
from app.certs.lego_runner import (
    LEGO_OVERALL_REQUEST_LIMIT,
    LEGO_TIMEOUT_SECONDS,
    _kill_process_tree,
    _run_lego,
)
from tests._cert_helpers import _fake_lego_proc


class TestRunLego:
    @patch("app.certs.lego_runner.subprocess.Popen")
    def test_runs_lego_with_cloudflare_env(self, mock_popen, tmp_path):

        mock_popen.return_value = _fake_lego_proc(["ok\n"], returncode=0)

        _run_lego(
            ["--domains", "test.example.com", "--email", "a@b.com", "--accept-tos", "run"],
            cloudflare_token="cf-token-123",
            lego_dir=tmp_path,
        )

        mock_popen.assert_called_once()
        call = mock_popen.call_args
        # Check CF token in env
        assert call.kwargs["env"]["CF_DNS_API_TOKEN"] == "cf-token-123"
        # Check command args
        cmd = call.args[0]
        assert "lego" in cmd[0]
        assert "--dns" in cmd
        assert "cloudflare" in cmd
        assert "--overall-request-limit" in cmd
        # Throttle ACME requests low to avoid tripping rate limits.
        limit_idx = cmd.index("--overall-request-limit") + 1
        assert cmd[limit_idx] == str(LEGO_OVERALL_REQUEST_LIMIT)
        assert LEGO_OVERALL_REQUEST_LIMIT <= 5
        # Timeout must comfortably exceed lego's DNS propagation wait (~60s).
        mock_popen.return_value.wait.assert_called_with(timeout=LEGO_TIMEOUT_SECONDS)
        assert LEGO_TIMEOUT_SECONDS >= 120

    @patch("app.certs.lego_runner.subprocess.Popen")
    def test_streams_each_output_line_to_log(self, mock_popen, tmp_path, caplog):

        mock_popen.return_value = _fake_lego_proc(
            ["[INFO] acme: registering account\n", "[INFO] acme: obtaining cert\n"],
            returncode=0,
        )

        with caplog.at_level(logging.INFO, logger="app.certs.lego_runner"):
            _run_lego(["run"], cloudflare_token="cf-token", lego_dir=tmp_path)

        assert "[lego] [INFO] acme: registering account" in caplog.text
        assert "[lego] [INFO] acme: obtaining cert" in caplog.text

    @patch("app.certs.lego_runner.subprocess.Popen")
    def test_raises_on_failure(self, mock_popen, tmp_path):

        mock_popen.return_value = _fake_lego_proc(["auth error\n"], returncode=1)

        with pytest.raises(RuntimeError, match="lego failed"):
            _run_lego(["run"], cloudflare_token="bad", lego_dir=tmp_path)

    @patch("app.certs.lego_runner.subprocess.Popen")
    def test_raises_on_timeout(self, mock_popen, tmp_path):

        # First wait() (with timeout) raises; the post-kill wait() returns.
        proc = _fake_lego_proc(
            ["waiting for retry after 31h\n"],
            wait_side_effect=[
                subprocess.TimeoutExpired(cmd=["lego", "run"], timeout=300),
                0,
            ],
        )
        mock_popen.return_value = proc

        with pytest.raises(RuntimeError, match="lego timed out after"):
            _run_lego(["run"], cloudflare_token="cf-token", lego_dir=tmp_path)
        proc.kill.assert_called_once()

    @patch("app.certs.lego_runner.os.killpg")
    @patch("app.certs.lego_runner.os.getpgid")
    def test_timeout_kill_ignores_non_integer_pid(self, mock_getpgid, mock_killpg):

        proc = MagicMock()
        proc.pid = MagicMock()

        _kill_process_tree(proc)

        mock_getpgid.assert_not_called()
        mock_killpg.assert_not_called()
        proc.kill.assert_called_once()

    @patch("app.certs.lego_runner.os.killpg")
    @patch("app.certs.lego_runner.os.getpgid")
    def test_timeout_kill_ignores_non_positive_pid(self, mock_getpgid, mock_killpg):

        proc = MagicMock()
        proc.pid = 0

        _kill_process_tree(proc)

        mock_getpgid.assert_not_called()
        mock_killpg.assert_not_called()
        proc.kill.assert_called_once()

    @patch("app.certs.lego_runner.os.killpg")
    @patch("app.certs.lego_runner.os.getpgid")
    def test_timeout_kill_signals_whole_process_group(self, mock_getpgid, mock_killpg):
        """A real (positive int) pid must SIGKILL the entire process group, not
        just the immediate child: a descendant lego spawn that inherited the
        stdout pipe would otherwise keep it open and defeat LEGO_TIMEOUT_SECONDS.
        The negative-pid guard tests pin the safety check; this pins the actual
        group kill so dropping/altering it can't pass unnoticed."""

        proc = MagicMock()
        proc.pid = 4321
        mock_getpgid.return_value = 4321

        _kill_process_tree(proc)

        mock_getpgid.assert_called_once_with(4321)
        mock_killpg.assert_called_once_with(4321, signal.SIGKILL)
        # The immediate child is also killed as a belt-and-suspenders fallback.
        proc.kill.assert_called_once()

    def test_serializes_concurrent_invocations(self, tmp_path):
        """Concurrent _run_lego calls must never overlap: every service shares
        one .lego ACME account dir, so issuances must run one at a time."""

        active = 0
        max_active = 0
        guard = threading.Lock()

        def fake_exec(args, cloudflare_token, lego_dir):
            nonlocal active, max_active
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with guard:
                active -= 1
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("app.certs.lego_runner._exec_lego", side_effect=fake_exec):
            threads = [
                threading.Thread(
                    target=lego_runner._run_lego,
                    args=([], "cf-token", tmp_path),
                )
                for _ in range(8)
            ]
            for th in threads:
                th.start()
            for th in threads:
                th.join()

        assert max_active == 1
