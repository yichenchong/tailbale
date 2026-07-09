"""Tests for certificate publish, atomic swap, and generation pruning."""

import os
import stat

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.certs import cert_manager
from app.certs.cert_manager import _atomic_copy_certs, _prune_old_generations, cert_key_pair_matches
from tests._cert_helpers import _real_pem_pair, _write_cert_key_pair


class TestAtomicCopyCerts:
    def test_publishes_via_current_symlink(self, tmp_path):

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

    def test_failed_swap_removes_staging_symlink(self, tmp_path, monkeypatch):
        """A pre-publish crash (os.replace raises) must remove the
        ``.current.*.tmp`` staging symlink ``os.symlink`` already created, not
        just the half-built generation. Otherwise every failed swap leaks a
        staging symlink that only a LATER successful publish's prune reaps - and
        if no publish ever succeeds they accumulate. The except path unlinks the
        staging symlink before re-raising; this pins that cleanup, which
        test_failed_swap_keeps_previous_current (asserts only the old current)
        and test_first_issuance_failure_leaves_no_current (asserts only gen-*)
        both leave unchecked."""
        dest_dir = tmp_path / "dest"
        src_cert = tmp_path / "c.pem"
        src_key = tmp_path / "k.pem"
        _write_cert_key_pair(src_cert, src_key)

        # os.symlink(gen_name, tmp_link) succeeds; the swap onto ``current`` fails.
        def boom(src, dst):
            raise OSError("crash during swap")

        monkeypatch.setattr(cert_manager.os, "replace", boom)

        with pytest.raises(OSError, match="crash during swap"):
            _atomic_copy_certs(src_cert, src_key, dest_dir)

        names = [p.name for p in dest_dir.iterdir()]
        # No staging symlink leaked and the half-built generation was discarded.
        assert not any(n.startswith(".current.") and n.endswith(".tmp") for n in names)
        assert [n for n in names if n.startswith("gen-")] == []
        assert not (dest_dir / "current").exists()

class TestPruneOldGenerations:
    def test_reaps_superseded_gens_and_stale_staging_only(self, tmp_path):
        """``_prune_old_generations`` reaps ONLY superseded ``gen-*`` dirs and
        stale ``.current.*.tmp`` staging symlinks. The live ``current`` symlink,
        the kept generation, and any UNRELATED entry (a plain file, a non-gen
        directory) are left untouched - the guarantee the docstring makes but
        no test pinned directly."""
        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        keep = "gen-keep"
        keep_dir = dest_dir / keep
        keep_dir.mkdir()
        (keep_dir / "fullchain.pem").write_text("live")
        os.symlink(keep, dest_dir / "current")

        # Superseded generation + stale staging symlink: both must be reaped.
        old_gen = dest_dir / "gen-old"
        old_gen.mkdir()
        (old_gen / "fullchain.pem").write_text("old")
        stale = dest_dir / ".current.999.888.deadbeef.tmp"
        os.symlink("gen-old", stale)

        # Unrelated entries: a plain file and a non-gen directory must survive.
        (dest_dir / "notes.txt").write_text("keepme")
        unrelated_dir = dest_dir / "backup"
        unrelated_dir.mkdir()
        (unrelated_dir / "data").write_text("d")

        _prune_old_generations(dest_dir, keep=keep)

        # Superseded artifacts gone.
        assert not old_gen.exists()
        assert not stale.is_symlink()
        # Live current + kept gen + unrelated entries all survive untouched.
        assert (dest_dir / "current").is_symlink()
        assert os.readlink(dest_dir / "current") == keep
        assert keep_dir.is_dir()
        assert (keep_dir / "fullchain.pem").read_text() == "live"
        assert (dest_dir / "notes.txt").read_text() == "keepme"
        assert unrelated_dir.is_dir()
        assert (unrelated_dir / "data").read_text() == "d"

    def test_missing_dest_dir_is_a_noop(self, tmp_path):
        """A nonexistent dest dir (``iterdir`` raises OSError) is swallowed:
        prune is best-effort and must never raise into the publish success
        path. Pins the ``except OSError: return`` guard."""
        # Must not raise.
        _prune_old_generations(tmp_path / "does-not-exist", keep="gen-x")
