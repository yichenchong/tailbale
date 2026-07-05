"""Tests for the certificate manager module."""

import functools
import subprocess
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


def _fake_lego_proc(lines, returncode=0, wait_side_effect=None):
    """Build a fake Popen process for streaming _run_lego tests."""
    proc = MagicMock()
    proc.stdout = iter(lines)
    if wait_side_effect is not None:
        proc.wait.side_effect = wait_side_effect
    else:
        proc.wait.return_value = returncode
    proc.returncode = returncode
    return proc


@functools.cache
def _real_pem_pair(tag: int = 0) -> tuple[bytes, bytes]:
    """Return ``(fullchain_pem, privkey_pem)`` for a real, self-signed, matching
    cert/key pair. Cached per *tag* so the RSA keygen cost is paid once; pass a
    distinct *tag* when a test needs a genuinely different pair (e.g. to assert an
    OLD vs NEW generation swap actually changed the published bytes).

    Publishing now refuses an UNPARSEABLE certificate (``_atomic_copy_certs``
    verifies expiry-readability + key match before the swap), so any test that
    drives a real publish must hand it parseable PEM, not placeholder text.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2027, 6, 15, tzinfo=UTC))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


class TestRunLego:
    @patch("app.certs.cert_manager.subprocess.Popen")
    def test_runs_lego_with_cloudflare_env(self, mock_popen, tmp_path):
        from app.certs.cert_manager import LEGO_TIMEOUT_SECONDS, _run_lego

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
        from app.certs.cert_manager import LEGO_OVERALL_REQUEST_LIMIT
        limit_idx = cmd.index("--overall-request-limit") + 1
        assert cmd[limit_idx] == str(LEGO_OVERALL_REQUEST_LIMIT)
        assert LEGO_OVERALL_REQUEST_LIMIT <= 5
        # Timeout must comfortably exceed lego's DNS propagation wait (~60s).
        mock_popen.return_value.wait.assert_called_with(timeout=LEGO_TIMEOUT_SECONDS)
        assert LEGO_TIMEOUT_SECONDS >= 120

    @patch("app.certs.cert_manager.subprocess.Popen")
    def test_streams_each_output_line_to_log(self, mock_popen, tmp_path, caplog):
        import logging

        from app.certs.cert_manager import _run_lego

        mock_popen.return_value = _fake_lego_proc(
            ["[INFO] acme: registering account\n", "[INFO] acme: obtaining cert\n"],
            returncode=0,
        )

        with caplog.at_level(logging.INFO, logger="app.certs.cert_manager"):
            _run_lego(["run"], cloudflare_token="cf-token", lego_dir=tmp_path)

        assert "[lego] [INFO] acme: registering account" in caplog.text
        assert "[lego] [INFO] acme: obtaining cert" in caplog.text

    @patch("app.certs.cert_manager.subprocess.Popen")
    def test_raises_on_failure(self, mock_popen, tmp_path):
        from app.certs.cert_manager import _run_lego

        mock_popen.return_value = _fake_lego_proc(["auth error\n"], returncode=1)

        with pytest.raises(RuntimeError, match="lego failed"):
            _run_lego(["run"], cloudflare_token="bad", lego_dir=tmp_path)

    @patch("app.certs.cert_manager.subprocess.Popen")
    def test_raises_on_timeout(self, mock_popen, tmp_path):
        from app.certs.cert_manager import _run_lego

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


    @patch("app.certs.cert_manager.os.killpg")
    @patch("app.certs.cert_manager.os.getpgid")
    def test_timeout_kill_ignores_non_integer_pid(self, mock_getpgid, mock_killpg):
        from app.certs.cert_manager import _kill_process_tree

        proc = MagicMock()
        proc.pid = MagicMock()

        _kill_process_tree(proc)

        mock_getpgid.assert_not_called()
        mock_killpg.assert_not_called()
        proc.kill.assert_called_once()

    @patch("app.certs.cert_manager.os.killpg")
    @patch("app.certs.cert_manager.os.getpgid")
    def test_timeout_kill_ignores_non_positive_pid(self, mock_getpgid, mock_killpg):
        from app.certs.cert_manager import _kill_process_tree

        proc = MagicMock()
        proc.pid = 0

        _kill_process_tree(proc)

        mock_getpgid.assert_not_called()
        mock_killpg.assert_not_called()
        proc.kill.assert_called_once()

    @patch("app.certs.cert_manager.os.killpg")
    @patch("app.certs.cert_manager.os.getpgid")
    def test_timeout_kill_signals_whole_process_group(self, mock_getpgid, mock_killpg):
        """A real (positive int) pid must SIGKILL the entire process group, not
        just the immediate child: a descendant lego spawn that inherited the
        stdout pipe would otherwise keep it open and defeat LEGO_TIMEOUT_SECONDS.
        The negative-pid guard tests pin the safety check; this pins the actual
        group kill so dropping/altering it can't pass unnoticed."""
        import signal

        from app.certs.cert_manager import _kill_process_tree

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
        import threading
        import time

        from app.certs import cert_manager

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

        with patch("app.certs.cert_manager._exec_lego", side_effect=fake_exec):
            threads = [
                threading.Thread(
                    target=cert_manager._run_lego,
                    args=([], "cf-token", tmp_path),
                )
                for _ in range(8)
            ]
            for th in threads:
                th.start()
            for th in threads:
                th.join()

        assert max_active == 1

class TestIssueCert:
    @patch("app.certs.cert_manager._run_lego")
    def test_issues_and_copies_certs(self, mock_lego, tmp_path):
        from app.certs.cert_manager import issue_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"

        # Simulate lego producing cert files
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        cert_pem, key_pem = _real_pem_pair()
        (lego_certs / "test.example.com.crt").write_bytes(cert_pem)
        (lego_certs / "test.example.com.key").write_bytes(key_pem)

        result = issue_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir,
        )

        assert result == cert_dir
        assert (cert_dir / "current" / "fullchain.pem").read_bytes() == cert_pem
        assert (cert_dir / "current" / "privkey.pem").read_bytes() == key_pem
        mock_lego.assert_called_once()

    @patch("app.certs.cert_manager._run_lego")
    def test_raises_if_cert_files_missing(self, mock_lego, tmp_path):
        from app.certs.cert_manager import issue_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        lego_dir.mkdir(parents=True)

        with pytest.raises(RuntimeError, match="did not produce"):
            issue_cert("test.example.com", "a@b.com", "cf-token", cert_dir, lego_dir)

    @patch("app.certs.cert_manager._run_lego")
    def test_default_lego_dir(self, mock_lego, tmp_path):
        from app.certs.cert_manager import issue_cert

        cert_dir = tmp_path / "certs" / "test.example.com"

        # Create expected lego output
        lego_dir = cert_dir.parent / ".lego"
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        cert_pem, key_pem = _real_pem_pair()
        (lego_certs / "test.example.com.crt").write_bytes(cert_pem)
        (lego_certs / "test.example.com.key").write_bytes(key_pem)

        issue_cert("test.example.com", "a@b.com", "cf-token", cert_dir)
        # lego_dir should default to cert_dir.parent / ".lego"
        assert mock_lego.called


class TestRenewCert:
    @patch("app.certs.cert_manager._run_lego")
    def test_renews_and_copies_certs(self, mock_lego, tmp_path):
        from app.certs.cert_manager import renew_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"

        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        cert_pem, key_pem = _real_pem_pair()
        (lego_certs / "test.example.com.crt").write_bytes(cert_pem)
        (lego_certs / "test.example.com.key").write_bytes(key_pem)

        result = renew_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir, days=30,
        )

        assert result == (cert_dir, False)
        assert (cert_dir / "current" / "fullchain.pem").read_bytes() == cert_pem
        assert (cert_dir / "current" / "privkey.pem").read_bytes() == key_pem

        # Verify lego was called with renew args
        call_args = mock_lego.call_args.args[0]
        assert "renew" in call_args
        assert "--days" in call_args

    @patch("app.certs.cert_manager._run_lego")
    def test_renew_falls_back_to_issue_when_lego_state_missing(self, mock_lego, tmp_path):
        """If lego's own account+cert state is gone (e.g. .lego wiped) a renew
        can never succeed; renew_cert must issue a fresh cert via `lego run`
        instead of failing every scan and looping forever on the retry backoff.
        """
        from app.certs.cert_manager import renew_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        # Deliberately leave lego_dir/certificates/*.crt absent: state was lost.

        # The fallback runs issue_cert -> `lego run`; simulate it producing files.
        def fake_run_lego(args, **kwargs):
            lego_certs = lego_dir / "certificates"
            lego_certs.mkdir(parents=True, exist_ok=True)
            cert_pem, key_pem = _real_pem_pair()
            (lego_certs / "test.example.com.crt").write_bytes(cert_pem)
            (lego_certs / "test.example.com.key").write_bytes(key_pem)

        mock_lego.side_effect = fake_run_lego

        result = renew_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir, days=30,
        )

        assert result == (cert_dir, True)
        fresh_cert, _fresh_key = _real_pem_pair()
        assert (cert_dir / "current" / "fullchain.pem").read_bytes() == fresh_cert
        # lego was invoked as a fresh issue (`run`), never `renew`.
        call_args = mock_lego.call_args.args[0]
        assert "run" in call_args
        assert "renew" not in call_args

    @patch("app.certs.cert_manager._run_lego")
    def test_renew_falls_back_to_issue_when_renew_fails(self, mock_lego, tmp_path):
        """The cert files survive but `lego renew` itself fails (e.g. the ACME
        account under .lego was wiped). The file check can't catch this, so the
        renew would fail every scan and loop forever on the backoff. renew_cert
        must catch the failure and fall back to a fresh `lego run` issue."""
        from app.certs.cert_manager import renew_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        # Cert files PRESENT, so the missing-files fast path does NOT fire.
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        (lego_certs / "test.example.com.crt").write_text("STALE CERT")
        (lego_certs / "test.example.com.key").write_text("STALE KEY")

        def fake_run_lego(args, **kwargs):
            if "renew" in args:
                raise RuntimeError("lego failed: acme: account does not exist")
            # Fallback issue (`run`) succeeds and refreshes the cert files.
            cert_pem, key_pem = _real_pem_pair()
            (lego_certs / "test.example.com.crt").write_bytes(cert_pem)
            (lego_certs / "test.example.com.key").write_bytes(key_pem)

        mock_lego.side_effect = fake_run_lego

        result = renew_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir, days=30,
        )

        cert_result, fresh_issued = result
        assert cert_result == cert_dir
        # A fallback fresh issue surfaces fresh_issued=True, which the caller
        # maps to a cert_issued (not cert_renewed) event label.
        assert fresh_issued is True
        assert (cert_dir / "current" / "fullchain.pem").read_bytes() == _real_pem_pair()[0]
        # Both a failed renew and a successful run were attempted, in that order.
        invoked = [c.args[0] for c in mock_lego.call_args_list]
        assert any("renew" in a for a in invoked)
        assert any("run" in a for a in invoked)

    @patch("app.certs.cert_manager._run_lego")
    def test_force_renew_uses_large_days_to_bypass_lego_skip(self, mock_lego, tmp_path):
        """force=True must make `lego renew` actually renew regardless of expiry.

        lego's renew silently no-ops (exit 0, files untouched) unless the cert
        expires within --days, so a forced renewal must pass a --days value
        larger than any cert lifetime; otherwise a manual force-renew republishes
        the same cert with an unchanged expiry."""
        from app.certs.cert_manager import LEGO_FORCE_RENEW_DAYS, renew_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        cert_pem, key_pem = _real_pem_pair()
        (lego_certs / "test.example.com.crt").write_bytes(cert_pem)
        (lego_certs / "test.example.com.key").write_bytes(key_pem)

        renew_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir, days=30, force=True,
        )

        args = mock_lego.call_args.args[0]
        assert "renew" in args
        days_idx = args.index("--days") + 1
        assert args[days_idx] == str(LEGO_FORCE_RENEW_DAYS)
        # The forced value must exceed any plausible cert lifetime (LE = 90 days).
        assert int(args[days_idx]) > 365

    @patch("app.certs.cert_manager._run_lego")
    def test_unforced_renew_uses_supplied_days(self, mock_lego, tmp_path):
        """Without force, renew honours the caller's renewal window verbatim."""
        from app.certs.cert_manager import renew_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        cert_pem, key_pem = _real_pem_pair()
        (lego_certs / "test.example.com.crt").write_bytes(cert_pem)
        (lego_certs / "test.example.com.key").write_bytes(key_pem)

        renew_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir, days=30,
        )

        args = mock_lego.call_args.args[0]
        days_idx = args.index("--days") + 1
        assert args[days_idx] == "30"

    @patch("app.certs.cert_manager._run_lego")
    def test_renew_raises_when_renew_produces_no_cert_files(self, mock_lego, tmp_path):
        """`lego renew` exiting 0 but leaving no cert files must raise, not
        silently publish a missing/partial pair. Prior lego state exists, so the
        missing-files fast path and the failed-renew fallback are both bypassed;
        only the post-renew file check can catch this, so renew_cert must refuse
        rather than copy nonexistent files into a generation."""
        from app.certs.cert_manager import renew_cert

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        crt = lego_certs / "test.example.com.crt"
        key = lego_certs / "test.example.com.key"
        crt.write_text("STALE CERT")
        key.write_text("STALE KEY")

        # renew "succeeds" (no exception, so no fresh-issue fallback) yet the
        # expected cert files are gone afterwards.
        def fake_run_lego(args, **kwargs):
            crt.unlink()
            key.unlink()

        mock_lego.side_effect = fake_run_lego

        with pytest.raises(RuntimeError, match="did not produce expected cert files"):
            renew_cert(
                "test.example.com", "a@b.com", "cf-token",
                cert_dir, lego_dir, days=30,
            )
        # Exactly one lego call (the renew); no fresh-issue fallback fired and no
        # publish happened (the file check raised before _atomic_copy_certs).
        assert mock_lego.call_count == 1
        renew_args = mock_lego.call_args.args[0]
        assert "renew" in renew_args
        assert "run" not in renew_args
        assert not (cert_dir / "current").exists()


class TestAtomicCopyCerts:
    def test_publishes_via_current_symlink(self, tmp_path):
        import os

        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "src_cert.pem"
        src_key = tmp_path / "src_key.pem"
        cert_pem, key_pem = _real_pem_pair()
        src_cert.write_bytes(cert_pem)
        src_key.write_bytes(key_pem)

        dest_dir = tmp_path / "dest"
        _atomic_copy_certs(src_cert, src_key, dest_dir)

        current = dest_dir / "current"
        # current is a RELATIVE symlink to a bare gen-* dir, so it resolves
        # inside the edge container's read-only /certs bind mount.
        assert current.is_symlink()
        target = os.readlink(current)
        assert target.startswith("gen-")
        assert "/" not in target and not os.path.isabs(target)
        assert (current / "fullchain.pem").read_bytes() == cert_pem
        assert (current / "privkey.pem").read_bytes() == key_pem

    def test_privkey_locked_to_0600(self, tmp_path):
        import stat

        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "src_cert.pem"
        src_key = tmp_path / "src_key.pem"
        cert_pem, key_pem = _real_pem_pair()
        src_cert.write_bytes(cert_pem)
        src_key.write_bytes(key_pem)
        # Source key deliberately world-readable to prove the dest is locked down.
        src_key.chmod(0o644)

        dest_dir = tmp_path / "dest"
        _atomic_copy_certs(src_cert, src_key, dest_dir)

        mode = stat.S_IMODE((dest_dir / "current" / "privkey.pem").stat().st_mode)
        assert mode == 0o600

    def test_fsyncs_files_gen_dir_then_hostname_dir(self, tmp_path, monkeypatch):
        from app.certs import cert_manager

        src_cert = tmp_path / "src_cert.pem"
        src_key = tmp_path / "src_key.pem"
        cert_pem, key_pem = _real_pem_pair()
        src_cert.write_bytes(cert_pem)
        src_key.write_bytes(key_pem)
        dest_dir = tmp_path / "dest"

        fsynced_files = []
        fsynced_dirs = []
        monkeypatch.setattr(cert_manager, "fsync_file", lambda path: fsynced_files.append(path.name))
        monkeypatch.setattr(cert_manager, "fsync_directory_strict", lambda path: fsynced_dirs.append(path))

        cert_manager._atomic_copy_certs(src_cert, src_key, dest_dir)

        # Both files in the new generation are fsynced before publishing.
        assert "fullchain.pem" in fsynced_files
        assert "privkey.pem" in fsynced_files
        # The gen dir is made durable before the swap; the hostname dir after it.
        assert len(fsynced_dirs) == 2
        assert fsynced_dirs[0].name.startswith("gen-")
        assert fsynced_dirs[0].parent == dest_dir
        assert fsynced_dirs[1] == dest_dir

    def test_no_tmp_files_left_on_success(self, tmp_path):
        import os

        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "c.pem"
        src_key = tmp_path / "k.pem"
        cert_pem, key_pem = _real_pem_pair()
        src_cert.write_bytes(cert_pem)
        src_key.write_bytes(key_pem)

        dest_dir = tmp_path / "dest"
        _atomic_copy_certs(src_cert, src_key, dest_dir)

        names = [p.name for p in dest_dir.iterdir()]
        assert not any(n.endswith(".tmp") for n in names)
        # Exactly one generation plus the current symlink pointing at it.
        gens = [n for n in names if n.startswith("gen-")]
        assert len(gens) == 1
        assert os.readlink(dest_dir / "current") == gens[0]

    def test_creates_dest_directory(self, tmp_path):
        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "c.pem"
        src_key = tmp_path / "k.pem"
        cert_pem, key_pem = _real_pem_pair()
        src_cert.write_bytes(cert_pem)
        src_key.write_bytes(key_pem)

        dest_dir = tmp_path / "nested" / "deep" / "dest"
        _atomic_copy_certs(src_cert, src_key, dest_dir)
        assert dest_dir.is_dir()
        assert (dest_dir / "current" / "fullchain.pem").read_bytes() == cert_pem

    def test_swap_replaces_previous_generation(self, tmp_path):
        import os

        from app.certs.cert_manager import _atomic_copy_certs

        src_cert = tmp_path / "c.pem"
        src_key = tmp_path / "k.pem"
        dest_dir = tmp_path / "dest"

        old_cert, old_key = _real_pem_pair(1)
        new_cert, new_key = _real_pem_pair(2)
        src_cert.write_bytes(old_cert)
        src_key.write_bytes(old_key)
        _atomic_copy_certs(src_cert, src_key, dest_dir)
        first_gen = os.readlink(dest_dir / "current")

        src_cert.write_bytes(new_cert)
        src_key.write_bytes(new_key)
        _atomic_copy_certs(src_cert, src_key, dest_dir)
        second_gen = os.readlink(dest_dir / "current")

        assert second_gen != first_gen
        assert (dest_dir / "current" / "fullchain.pem").read_bytes() == new_cert
        assert (dest_dir / "current" / "privkey.pem").read_bytes() == new_key
        # The superseded generation is pruned; only the live one remains.
        gens = [p.name for p in dest_dir.iterdir() if p.name.startswith("gen-")]
        assert gens == [second_gen]

    def test_refuses_to_publish_mismatched_pair(self, tmp_path):
        """A cert whose key does not match must NEVER be published: the new
        generation is discarded and the existing current is left untouched."""
        import os

        from cryptography.hazmat.primitives.asymmetric import rsa

        from app.certs.cert_manager import _atomic_copy_certs, cert_key_pair_matches

        dest_dir = tmp_path / "dest"

        # A first, valid generation is live.
        good_cert = tmp_path / "good_cert.pem"
        good_key = tmp_path / "good_key.pem"
        _write_cert_key_pair(good_cert, good_key)
        _atomic_copy_certs(good_cert, good_key, dest_dir)
        good_gen = os.readlink(dest_dir / "current")

        # Attempt to publish a real-but-MISMATCHED pair (cert signed by an
        # unrelated key) - the kind of mismatched current/ pair on-disk
        # corruption or external tampering could produce. _atomic_copy_certs
        # must refuse.
        bad_cert = tmp_path / "bad_cert.pem"
        bad_key = tmp_path / "bad_key.pem"
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        _write_cert_key_pair(bad_cert, bad_key, cert_key=other)

        with pytest.raises(RuntimeError, match="mismatched"):
            _atomic_copy_certs(bad_cert, bad_key, dest_dir)

        # current still resolves to the original good generation, which matches.
        assert os.readlink(dest_dir / "current") == good_gen
        cur = dest_dir / "current"
        assert cert_key_pair_matches(cur / "fullchain.pem", cur / "privkey.pem") is True

    def test_refuses_to_publish_unparseable_cert(self, tmp_path):
        """CT1 guard: an UNPARSEABLE fullchain must NEVER be published, even
        beside a real private key. This is a distinct guard from the mismatch
        check because ``cert_key_pair_matches`` treats an unparseable cert as
        "nothing to verify" and returns True (lenient contract), so the mismatch
        guard alone would let the corrupt cert through. Publishing it would point
        ``current`` at a cert Caddy cannot load AND the success-path prune would
        delete the last-good generation, taking TLS down with no fallback. The
        new generation must be discarded and any existing ``current`` untouched.
        """
        import os

        from app.certs.cert_manager import _atomic_copy_certs, cert_key_pair_matches

        dest_dir = tmp_path / "dest"

        # A first, valid generation is live.
        good_cert = tmp_path / "good_cert.pem"
        good_key = tmp_path / "good_key.pem"
        _write_cert_key_pair(good_cert, good_key)
        _atomic_copy_certs(good_cert, good_key, dest_dir)
        good_gen = os.readlink(dest_dir / "current")

        # Attempt to publish an UNPARSEABLE cert beside a real key. The lenient
        # mismatch check would pass (unparseable cert -> "nothing to verify" ->
        # True), so only the expiry-readability guard can reject it.
        bad_cert = tmp_path / "bad_cert.pem"
        bad_key = tmp_path / "bad_key.pem"
        bad_cert.write_bytes(
            b"-----BEGIN CERTIFICATE-----\nNOT-A-REAL-CERT\n-----END CERTIFICATE-----\n"
        )
        _, key_pem = _real_pem_pair()
        bad_key.write_bytes(key_pem)
        # Sanity: the mismatch guard is blind to this corruption.
        assert cert_key_pair_matches(bad_cert, bad_key) is True

        with pytest.raises(RuntimeError, match="unparseable"):
            _atomic_copy_certs(bad_cert, bad_key, dest_dir)

        # current still resolves to the original good generation (no last-good
        # generation was pruned), and the corrupt generation was discarded.
        assert os.readlink(dest_dir / "current") == good_gen
        cur = dest_dir / "current"
        assert cert_key_pair_matches(cur / "fullchain.pem", cur / "privkey.pem") is True
        gens = [p.name for p in dest_dir.iterdir() if p.name.startswith("gen-")]
        assert gens == [good_gen]

    def test_failed_swap_keeps_previous_current(self, tmp_path, monkeypatch):
        """If a crash strikes before the symlink swap, the old current stays
        valid - the new generation is simply discarded (atomic swap)."""
        import os

        from app.certs import cert_manager
        from app.certs.cert_manager import _atomic_copy_certs, cert_key_pair_matches

        dest_dir = tmp_path / "dest"
        good_cert = tmp_path / "good_cert.pem"
        good_key = tmp_path / "good_key.pem"
        _write_cert_key_pair(good_cert, good_key)
        _atomic_copy_certs(good_cert, good_key, dest_dir)
        good_gen = os.readlink(dest_dir / "current")
        good_fullchain = (dest_dir / "current" / "fullchain.pem").read_bytes()

        # Simulate interruption: the atomic publish (os.replace) fails.
        def boom(src, dst):
            raise OSError("crash before swap")

        monkeypatch.setattr(cert_manager.os, "replace", boom)

        new_cert = tmp_path / "new_cert.pem"
        new_key = tmp_path / "new_key.pem"
        _write_cert_key_pair(new_cert, new_key)
        with pytest.raises(OSError, match="crash before swap"):
            _atomic_copy_certs(new_cert, new_key, dest_dir)

        # current is unchanged and still resolves to the valid old pair.
        assert os.readlink(dest_dir / "current") == good_gen
        cur = dest_dir / "current"
        assert (cur / "fullchain.pem").read_bytes() == good_fullchain
        assert cert_key_pair_matches(cur / "fullchain.pem", cur / "privkey.pem") is True

    def test_first_issuance_failure_leaves_no_current(self, tmp_path, monkeypatch):
        """A first issuance interrupted before the swap leaves no current at all
        (an absent cert, never a mismatched one)."""
        from app.certs import cert_manager
        from app.certs.cert_manager import _atomic_copy_certs

        dest_dir = tmp_path / "dest"
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        _write_cert_key_pair(cert, key)

        def boom(src, dst):
            raise OSError("crash before swap")

        monkeypatch.setattr(cert_manager.os, "replace", boom)

        with pytest.raises(OSError, match="crash before swap"):
            _atomic_copy_certs(cert, key, dest_dir)

        assert not (dest_dir / "current").exists()
        assert not (dest_dir / "current").is_symlink()
        # The half-built generation was discarded, leaving nothing behind.
        assert [p.name for p in dest_dir.iterdir() if p.name.startswith("gen-")] == []

    def test_interrupted_generation_is_never_referenced(self, tmp_path):
        """A stray generation from a prior interrupted write is never served:
        with no current symlink a reader sees no cert, and the next successful
        publish prunes the orphan. current only ever points at a matching pair."""
        from app.certs.cert_manager import _atomic_copy_certs, cert_key_pair_matches

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        # Simulate a crashed write: an orphan gen dir with a half-written pair
        # and NO current symlink.
        orphan = dest_dir / "gen-orphan"
        orphan.mkdir()
        (orphan / "fullchain.pem").write_text("HALF WRITTEN")
        # privkey deliberately absent -> an incomplete, never-published generation.

        # current is absent: a reader sees no cert (never a broken one).
        assert not (dest_dir / "current").exists()

        # A subsequent successful publish heals the directory.
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        _write_cert_key_pair(cert, key)
        _atomic_copy_certs(cert, key, dest_dir)

        cur = dest_dir / "current"
        assert cur.is_symlink()
        assert cert_key_pair_matches(cur / "fullchain.pem", cur / "privkey.pem") is True
        # The orphan generation was pruned.
        assert not orphan.exists()

    def test_prunes_stale_temp_staging_symlinks(self, tmp_path):
        """A crash between os.symlink and os.replace can leave a `.current.*.tmp`
        staging symlink behind. The next successful publish must reap it so the
        leftovers do not accumulate across crashes."""
        import os

        from app.certs.cert_manager import _atomic_copy_certs

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()
        # Simulate a hard crash that left a staging symlink (its target may even
        # be a generation that never materialized).
        stale = dest_dir / ".current.999.888.deadbeef.tmp"
        os.symlink("gen-never-published", stale)
        assert stale.is_symlink()

        src_cert = tmp_path / "c.pem"
        src_key = tmp_path / "k.pem"
        cert_pem, key_pem = _real_pem_pair()
        src_cert.write_bytes(cert_pem)
        src_key.write_bytes(key_pem)
        _atomic_copy_certs(src_cert, src_key, dest_dir)

        # The stale staging symlink was reaped; no `.tmp` litter remains, and the
        # live publish (current + its generation) is intact.
        names = [p.name for p in dest_dir.iterdir()]
        assert not any(n.endswith(".tmp") for n in names)
        assert not stale.is_symlink()
        assert os.readlink(dest_dir / "current").startswith("gen-")

    def test_post_publish_step_failure_keeps_live_generation(self, tmp_path, monkeypatch):
        """A failure in the best-effort step AFTER the atomic swap (the dest-dir
        durability fsync / logging) must never tear down the just-published
        generation. Pre-fix the except path rmtree'd the live gen dir, leaving
        `current` dangling and TLS down; the swap is the commit point, so the
        publish must stand."""
        import os

        from app.certs import cert_manager
        from app.certs.cert_manager import _atomic_copy_certs, cert_key_pair_matches

        dest_dir = tmp_path / "dest"
        src_cert = tmp_path / "cert.pem"
        src_key = tmp_path / "key.pem"
        _write_cert_key_pair(src_cert, src_key)

        # fsync_directory_strict runs twice: gen_dir (pre-swap) then dest_dir
        # (post-swap). Fail ONLY the post-swap dest-dir fsync.
        real_fsync_dir = cert_manager.fsync_directory_strict

        def flaky_fsync_dir(path):
            if path == dest_dir:
                raise OSError("EIO syncing dest dir after swap")
            return real_fsync_dir(path)

        monkeypatch.setattr(cert_manager, "fsync_directory_strict", flaky_fsync_dir)

        # The publish has committed, so the call returns normally (no raise).
        _atomic_copy_certs(src_cert, src_key, dest_dir)

        current = dest_dir / "current"
        assert current.is_symlink()
        # current resolves to a real generation — not a dangling link.
        assert current.exists()
        target = os.readlink(current)
        assert (dest_dir / target).is_dir()
        assert cert_key_pair_matches(current / "fullchain.pem", current / "privkey.pem") is True

    def test_post_publish_failure_preserves_prior_generation(self, tmp_path, monkeypatch):
        """A post-swap durability failure must NOT prune the previous generation.

        The failing step is the dest-dir fsync that makes the new ``current``
        rename durable, so the rename may still be only in page cache. If a crash
        then reverts the un-synced rename back to the previous generation, that
        generation must still exist — otherwise ``current`` dangles and TLS goes
        down until a costly ACME re-issue. Pre-fix the prune ran unconditionally
        after the (failed) durability step and rmtree'd the prior generation,
        deleting the very pair a revert would fall back to."""
        import os

        from app.certs import cert_manager
        from app.certs.cert_manager import _atomic_copy_certs, cert_key_pair_matches

        dest_dir = tmp_path / "dest"
        old_cert = tmp_path / "old_cert.pem"
        old_key = tmp_path / "old_key.pem"
        _write_cert_key_pair(old_cert, old_key)
        # First publish establishes a live previous generation durably.
        _atomic_copy_certs(old_cert, old_key, dest_dir)
        old_gen = os.readlink(dest_dir / "current")
        old_gen_dir = dest_dir / old_gen
        assert old_gen_dir.is_dir()

        # Fail ONLY the post-swap dest-dir fsync of the SECOND publish; the
        # gen-dir fsync (pre-swap) still succeeds so the new pair is published.
        real_fsync_dir = cert_manager.fsync_directory_strict

        def flaky_fsync_dir(path):
            if path == dest_dir:
                raise OSError("EIO syncing dest dir after swap")
            return real_fsync_dir(path)

        monkeypatch.setattr(cert_manager, "fsync_directory_strict", flaky_fsync_dir)

        new_cert = tmp_path / "new_cert.pem"
        new_key = tmp_path / "new_key.pem"
        _write_cert_key_pair(new_cert, new_key)
        # The swap committed, so the call returns normally despite the fsync EIO.
        _atomic_copy_certs(new_cert, new_key, dest_dir)

        # The new generation is live and matches.
        current = dest_dir / "current"
        new_gen = os.readlink(current)
        assert new_gen != old_gen
        assert cert_key_pair_matches(current / "fullchain.pem", current / "privkey.pem") is True
        # The prior generation SURVIVED (prune skipped): a crash that reverts the
        # not-yet-durable rename lands on a still-valid matching pair, not a
        # dangling link.
        assert old_gen_dir.is_dir()
        assert cert_key_pair_matches(
            old_gen_dir / "fullchain.pem", old_gen_dir / "privkey.pem"
        ) is True

    def test_post_publish_failures_accumulate_then_durable_publish_reaps_all(
        self, tmp_path, monkeypatch
    ):
        """Unbounded-growth guard for the prune-skip path.

        Skipping the prune on a post-swap durability failure leaves the prior
        generation as a crash-revert target, so consecutive post-publish
        failures accumulate stale ``gen-*`` dirs. That accumulation is BOUNDED:
        every failure keeps the full backlog intact (never loses the durable
        revert target), and the next fully durable publish reaps the ENTIRE
        backlog in a single pass — ``_prune_old_generations`` retains only the
        live ``keep`` gen — so growth can never run away."""
        import os

        from app.certs import cert_manager
        from app.certs.cert_manager import _atomic_copy_certs, cert_key_pair_matches

        dest_dir = tmp_path / "dest"
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        _write_cert_key_pair(cert, key)

        # A durable baseline publish: prune runs and only the baseline remains.
        _atomic_copy_certs(cert, key, dest_dir)
        assert len([p for p in dest_dir.iterdir() if p.name.startswith("gen-")]) == 1

        real_fsync_dir = cert_manager.fsync_directory_strict

        def fail_dest_fsync(path):
            if path == dest_dir:
                raise OSError("EIO syncing dest dir after swap")
            return real_fsync_dir(path)

        monkeypatch.setattr(cert_manager, "fsync_directory_strict", fail_dest_fsync)

        # Three consecutive post-swap durability failures. Each commits a new
        # live gen via the atomic swap but skips the prune, so the backlog of
        # stale gen-* dirs grows by one each time and NONE is lost.
        for _ in range(3):
            _atomic_copy_certs(cert, key, dest_dir)

        gens = [p.name for p in dest_dir.iterdir() if p.name.startswith("gen-")]
        # baseline + 3 post-publish-failure publishes, nothing pruned.
        assert len(gens) == 4
        # current still resolves to a real, matching generation throughout.
        cur = dest_dir / "current"
        assert os.readlink(cur) in gens
        assert cert_key_pair_matches(cur / "fullchain.pem", cur / "privkey.pem") is True

        # Restore durable fsync and publish once more: the WHOLE stale backlog
        # is reaped in one pass, leaving only the freshly-published live gen.
        monkeypatch.setattr(cert_manager, "fsync_directory_strict", real_fsync_dir)
        _atomic_copy_certs(cert, key, dest_dir)

        final_gens = [p.name for p in dest_dir.iterdir() if p.name.startswith("gen-")]
        assert len(final_gens) == 1
        assert os.readlink(dest_dir / "current") == final_gens[0]
        assert cert_key_pair_matches(
            dest_dir / "current" / "fullchain.pem",
            dest_dir / "current" / "privkey.pem",
        ) is True

class TestGetCertExpiry:
    def test_returns_none_for_missing_file(self, tmp_path):
        from app.certs.cert_manager import get_cert_expiry

        result = get_cert_expiry(tmp_path / "nonexistent.pem")
        assert result is None

    def test_returns_none_for_invalid_cert(self, tmp_path):
        from app.certs.cert_manager import get_cert_expiry

        bad_cert = tmp_path / "bad.pem"
        bad_cert.write_text("not a certificate")

        result = get_cert_expiry(bad_cert)
        assert result is None

    def test_parses_real_cert(self, tmp_path):
        """Generate a self-signed cert and verify expiry parsing."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        from app.certs.cert_manager import get_cert_expiry

        # Generate a test certificate
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
        expiry = datetime(2027, 6, 15, 12, 0, 0, tzinfo=UTC)

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
            .not_valid_after(expiry)
            .sign(key, hashes.SHA256())
        )

        cert_path = tmp_path / "fullchain.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        result = get_cert_expiry(cert_path)
        assert result is not None
        assert result.year == 2027
        assert result.month == 6
        assert result.day == 15

def _write_cert_key_pair(cert_path, key_path, *, key=None, cert_key=None):
    """Write a self-signed cert + a private key to disk.

    The cert is signed by ``cert_key`` (defaults to ``key``); pass a different
    ``cert_key`` to deliberately produce a mismatched pair (cert's public key
    does not correspond to the key written to ``key_path``).
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    if key is None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if cert_key is None:
        cert_key = key
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.example.com")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(cert_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2027, 6, 15, tzinfo=UTC))
        .sign(cert_key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


class TestCertKeyPairMatches:
    def test_true_for_matching_pair(self, tmp_path):
        from app.certs.cert_manager import cert_key_pair_matches

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        _write_cert_key_pair(cert_path, key_path)

        assert cert_key_pair_matches(cert_path, key_path) is True

    def test_false_for_mismatched_pair(self, tmp_path):
        """A cert signed by one key beside a *different* private key is the
        mismatched current/ pair that on-disk corruption or external tampering
        can leave; cert_key_pair_matches must catch it."""
        from cryptography.hazmat.primitives.asymmetric import rsa

        from app.certs.cert_manager import cert_key_pair_matches

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # Cert's public key comes from cert_key; privkey.pem holds an unrelated key.
        _write_cert_key_pair(cert_path, key_path, cert_key=other_key)

        assert cert_key_pair_matches(cert_path, key_path) is False

    def test_false_when_key_missing(self, tmp_path):
        from app.certs.cert_manager import cert_key_pair_matches

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        _write_cert_key_pair(cert_path, tmp_path / "scratch.pem")

        assert cert_key_pair_matches(cert_path, key_path) is False

    def test_false_when_key_unreadable(self, tmp_path):
        from app.certs.cert_manager import cert_key_pair_matches

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        _write_cert_key_pair(cert_path, tmp_path / "scratch.pem")
        key_path.write_text("not a private key")

        assert cert_key_pair_matches(cert_path, key_path) is False

    def test_true_when_cert_missing(self, tmp_path):
        """Nothing to verify: get_cert_expiry already drives a re-issue."""
        from app.certs.cert_manager import cert_key_pair_matches

        assert cert_key_pair_matches(tmp_path / "absent.pem", tmp_path / "k.pem") is True

    def test_true_when_cert_unreadable(self, tmp_path):
        from app.certs.cert_manager import cert_key_pair_matches

        cert_path = tmp_path / "fullchain.pem"
        key_path = tmp_path / "privkey.pem"
        cert_path.write_text("not a certificate")
        key_path.write_text("not a key")

        assert cert_key_pair_matches(cert_path, key_path) is True
