"""Tests for certificate issue and renewal orchestration."""

from unittest.mock import patch

import pytest

from app.certs.cert_manager import LEGO_FORCE_RENEW_DAYS, issue_cert, renew_cert
from tests._cert_helpers import _real_pem_pair


class TestIssueCert:
    @patch("app.certs.lego_runner._run_lego")
    def test_issues_and_copies_certs(self, mock_lego, tmp_path):

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

    @patch("app.certs.lego_runner._run_lego")
    def test_raises_if_cert_files_missing(self, mock_lego, tmp_path):

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        lego_dir.mkdir(parents=True)

        with pytest.raises(RuntimeError, match="did not produce"):
            issue_cert("test.example.com", "a@b.com", "cf-token", cert_dir, lego_dir)

class TestRenewCert:
    @patch("app.certs.lego_runner._run_lego")
    def test_renews_and_copies_certs(self, mock_lego, tmp_path):

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

    @patch("app.certs.lego_runner._run_lego")
    def test_renew_falls_back_to_issue_when_lego_state_missing(self, mock_lego, tmp_path):
        """If lego's own account+cert state is gone (e.g. .lego wiped) a renew
        can never succeed; renew_cert must issue a fresh cert via `lego run`
        instead of failing every scan and looping forever on the retry backoff.
        """

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

    @patch("app.certs.lego_runner._run_lego")
    def test_renew_falls_back_to_issue_when_renew_fails(self, mock_lego, tmp_path):
        """The cert files survive but `lego renew` itself fails (e.g. the ACME
        account under .lego was wiped). The file check can't catch this, so the
        renew would fail every scan and loop forever on the backoff. renew_cert
        must catch the failure and fall back to a fresh `lego run` issue."""

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

    @patch("app.certs.lego_runner._run_lego")
    def test_force_renew_uses_large_days_to_bypass_lego_skip(self, mock_lego, tmp_path):
        """force=True must make `lego renew` actually renew regardless of expiry.

        lego's renew silently no-ops (exit 0, files untouched) unless the cert
        expires within --days, so a forced renewal must pass a --days value
        larger than any cert lifetime; otherwise a manual force-renew republishes
        the same cert with an unchanged expiry."""

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

    @patch("app.certs.lego_runner._run_lego")
    def test_forced_in_place_renew_reports_not_fresh_issued(self, mock_lego, tmp_path):
        """A FORCED renewal whose `lego renew` succeeds in place is still a
        renewal, not a fallback fresh issue: renew_cert must return
        fresh_issued=False so the caller labels the event cert_renewed rather
        than cert_issued. Only the unforced success path and the two fallback
        (True) paths pin the tuple today; the forced-success False return went
        unasserted, so a regression mislabeling a forced in-place renewal as a
        fresh issue would slip through."""

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "lego"
        lego_certs = lego_dir / "certificates"
        lego_certs.mkdir(parents=True)
        cert_pem, key_pem = _real_pem_pair()
        (lego_certs / "test.example.com.crt").write_bytes(cert_pem)
        (lego_certs / "test.example.com.key").write_bytes(key_pem)

        result = renew_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir, days=30, force=True,
        )

        # lego renew succeeded (no fallback), so this is an in-place renewal.
        assert result == (cert_dir, False)
        # A single `lego renew` ran; the fresh-issue (`run`) fallback never fired.
        invoked = [c.args[0] for c in mock_lego.call_args_list]
        assert any("renew" in a for a in invoked)
        assert not any("run" in a for a in invoked)

    @patch("app.certs.lego_runner._run_lego")
    def test_unforced_renew_uses_supplied_days(self, mock_lego, tmp_path):
        """Without force, renew honours the caller's renewal window verbatim."""

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

    @patch("app.certs.lego_runner._run_lego")
    def test_renew_raises_when_renew_produces_no_cert_files(self, mock_lego, tmp_path):
        """`lego renew` exiting 0 but leaving no cert files must raise, not
        silently publish a missing/partial pair. Prior lego state exists, so the
        missing-files fast path and the failed-renew fallback are both bypassed;
        only the post-renew file check can catch this, so renew_cert must refuse
        rather than copy nonexistent files into a generation."""

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

class TestRenewFallbackAfterSC2Cleanup:
    """SC2 removes lego's per-hostname cert artifacts on service delete /
    hostname change. That is only safe because ``renew_cert`` falls back to a
    fresh issue when the lego state is absent. This regression test pins that
    load-bearing fallback: given a served cert dir but NO ``.lego/certificates``
    state (exactly the post-cleanup shape), renew_cert must re-issue and report
    ``fresh_issued=True`` rather than failing."""

    @patch("app.certs.lego_runner._run_lego")
    def test_renew_reissues_when_sc2_cleared_lego_state(self, mock_lego, tmp_path):

        cert_dir = tmp_path / "certs" / "test.example.com"
        lego_dir = tmp_path / "certs" / ".lego"
        # The served cert dir survives, but SC2 wiped the lego per-hostname
        # artifacts: leave lego_dir/certificates/*.{crt,key} absent so renew
        # cannot proceed and must fall back to a fresh issue.
        cert_dir.mkdir(parents=True)

        # The fallback issue -> `lego run` re-creates the lego artifacts.
        fresh_cert, fresh_key = _real_pem_pair(tag=99)

        def fake_run_lego(args, **kwargs):
            lego_certs = lego_dir / "certificates"
            lego_certs.mkdir(parents=True, exist_ok=True)
            (lego_certs / "test.example.com.crt").write_bytes(fresh_cert)
            (lego_certs / "test.example.com.key").write_bytes(fresh_key)

        mock_lego.side_effect = fake_run_lego

        result_dir, fresh_issued = renew_cert(
            "test.example.com", "a@b.com", "cf-token",
            cert_dir, lego_dir, days=30,
        )

        assert result_dir == cert_dir
        assert fresh_issued is True
        # A fresh issue (`lego run`), never `renew`.
        call_args = mock_lego.call_args.args[0]
        assert "run" in call_args
        assert "renew" not in call_args
        # The freshly-issued cert is now the published pair.
        assert (cert_dir / "current" / "fullchain.pem").read_bytes() == fresh_cert
