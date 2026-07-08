"""Tests for the shared capped-exponential backoff helper (app.backoff)."""

import asyncio
import random

import pytest

from app.backoff import capped_exponential, retry_sync, run_periodic


class TestCappedExponential:
    def test_attempt_zero_is_base(self):
        assert capped_exponential(0, base=15, cap=3600) == 15

    def test_doubles_each_attempt_until_cap(self):
        # base=15, cap=3600: 15, 30, 60, 120, 240, 480, 960, 1920, then clamp.
        seq = [capped_exponential(a, base=15, cap=3600) for a in range(8)]
        assert seq == [15, 30, 60, 120, 240, 480, 960, 1920]

    def test_growth_is_strictly_increasing_below_cap(self):
        prev = 0.0
        for a in range(7):
            cur = capped_exponential(a, base=15, cap=3600)
            assert cur > prev
            prev = cur

    def test_clamps_at_cap_for_large_attempts(self):
        for attempt in (8, 12, 19, 64, 1000):
            assert capped_exponential(attempt, base=15, cap=3600) == 3600

    def test_negative_attempt_treated_as_zero(self):
        assert capped_exponential(-5, base=15, cap=3600) == 15

    def test_fixed_when_base_equals_cap(self):
        # Degenerate capped exponential: no growth, every attempt is the cap.
        for attempt in (0, 1, 5, 50):
            assert capped_exponential(attempt, base=30, cap=30) == 30

    def test_cap_below_base_clamps_immediately(self):
        assert capped_exponential(0, base=100, cap=30) == 30
        assert capped_exponential(3, base=100, cap=30) == 30

    def test_jitter_off_by_default_is_deterministic(self):
        a = capped_exponential(3, base=15, cap=3600)
        b = capped_exponential(3, base=15, cap=3600)
        assert a == b == 120

    def test_jitter_scales_within_bounds_and_is_injectable(self):
        rng = random.Random(1234)
        base_delay = 120  # attempt=3, base=15
        for _ in range(200):
            d = capped_exponential(3, base=15, cap=3600, jitter=0.25, rng=rng)
            assert base_delay * 0.75 <= d <= base_delay * 1.25

    def test_jitter_with_seeded_rng_is_reproducible(self):
        d1 = capped_exponential(3, base=15, cap=3600, jitter=0.5, rng=random.Random(7))
        d2 = capped_exponential(3, base=15, cap=3600, jitter=0.5, rng=random.Random(7))
        assert d1 == d2
        assert d1 != 120  # jitter actually perturbed the base delay

    def test_base_zero_returns_zero(self):
        assert capped_exponential(5, base=0, cap=3600) == 0.0

    def test_full_jitter_never_returns_negative_delay(self):
        # jitter >= 1 ("full jitter") drives the random factor (1 + uniform(-j, j))
        # to/below zero. A delay is a duration: a negative one is meaningless
        # (asyncio.sleep rejects it; a "retry at now+delay" schedule lands in the
        # past). It must be floored at 0.0, never go negative.
        rng = random.Random(0)
        saw_zero_floor = False
        for _ in range(500):
            d = capped_exponential(3, base=15, cap=3600, jitter=1.5, rng=rng)
            assert d >= 0.0
            if d == 0.0:
                saw_zero_floor = True
        assert saw_zero_floor, "jitter>=1 should have produced a clamped 0.0 at least once"

    def test_jitter_exactly_one_stays_non_negative(self):
        rng = random.Random(99)
        for _ in range(500):
            d = capped_exponential(5, base=15, cap=3600, jitter=1.0, rng=rng)
            assert d >= 0.0


class TestRunPeriodic:
    """The shared background-loop skeleton: startup delay, dynamic interval,
    clean cancellation, and error backoff (default vs on_error)."""

    @staticmethod
    def _stop_after(sleeps, n):
        async def fake_sleep(secs):
            sleeps.append(secs)
            if len(sleeps) >= n:
                raise asyncio.CancelledError()
        return fake_sleep

    def test_startup_then_work_then_interval(self, monkeypatch):
        sleeps: list[float] = []
        calls = {"work": 0}

        async def work():
            calls["work"] += 1

        monkeypatch.setattr(asyncio, "sleep", self._stop_after(sleeps, 2))
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                run_periodic(
                    name="Test loop",
                    startup_delay=7,
                    interval_fn=lambda: 33,
                    work=work,
                )
            )
        # startup delay, one work call, then the interval.
        assert sleeps == [7, 33]
        assert calls["work"] == 1

    def test_dynamic_interval_is_read_each_pass(self, monkeypatch):
        sleeps: list[float] = []
        intervals = iter([11, 22, 33])

        async def work():
            pass

        monkeypatch.setattr(asyncio, "sleep", self._stop_after(sleeps, 3))
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                run_periodic(
                    name="Test loop",
                    startup_delay=0,
                    interval_fn=lambda: next(intervals),
                    work=work,
                )
            )
        # startup 0, then interval_fn() re-read on each successful pass.
        assert sleeps == [0, 11, 22]

    def test_cancellation_during_work_propagates(self, monkeypatch):
        sleeps: list[float] = []

        async def work():
            raise asyncio.CancelledError()

        async def fake_sleep(secs):
            sleeps.append(secs)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                run_periodic(
                    name="Test loop",
                    startup_delay=1,
                    interval_fn=lambda: 5,
                    work=work,
                )
            )
        # Only the startup sleep ran; the cancel from work re-raised out.
        assert sleeps == [1]

    def test_error_uses_on_error_backoff(self, monkeypatch):
        sleeps: list[float] = []
        seen: list[BaseException] = []

        async def work():
            raise RuntimeError("boom")

        def on_error(exc):
            seen.append(exc)
            return 99

        monkeypatch.setattr(asyncio, "sleep", self._stop_after(sleeps, 2))
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                run_periodic(
                    name="Test loop",
                    startup_delay=2,
                    interval_fn=lambda: 5,
                    work=work,
                    on_error=on_error,
                )
            )
        # startup, then the on_error backoff (NOT interval_fn's 5).
        assert sleeps == [2, 99]
        assert isinstance(seen[0], RuntimeError)

    def test_error_without_on_error_falls_back_to_interval(self, monkeypatch):
        sleeps: list[float] = []

        async def work():
            raise RuntimeError("boom")

        monkeypatch.setattr(asyncio, "sleep", self._stop_after(sleeps, 2))
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(
                run_periodic(
                    name="Test loop",
                    startup_delay=3,
                    interval_fn=lambda: 5,
                    work=work,
                )
            )
        # No on_error -> error backoff defaults to interval_fn().
        assert sleeps == [3, 5]


class TestRetrySync:
    """Synchronous bounded-retry primitive: yields 0..attempts-1, sleeping
    `delay` BETWEEN attempts only (no trailing sleep). time.sleep is patched
    out so these assert timing/count without real waits."""

    def test_yields_all_indices_and_sleeps_between_attempts(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("app.backoff.time.sleep", lambda d: sleeps.append(d))
        assert list(retry_sync(3, 0.5)) == [0, 1, 2]
        # attempts-1 sleeps, one between each consecutive pair, none trailing.
        assert sleeps == [0.5, 0.5]

    def test_single_attempt_never_sleeps(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("app.backoff.time.sleep", lambda d: sleeps.append(d))
        assert list(retry_sync(1, 9.0)) == [0]
        assert sleeps == []

    def test_zero_or_negative_attempts_yield_nothing_and_never_sleep(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr("app.backoff.time.sleep", lambda d: sleeps.append(d))
        assert list(retry_sync(0, 1.0)) == []
        assert list(retry_sync(-3, 1.0)) == []
        assert sleeps == []

    def test_break_out_of_loop_abandons_remaining_sleeps(self, monkeypatch):
        # Sleep happens BEFORE attempts 1..n-1; a caller that breaks after the
        # first attempt abandons the generator, so no further sleep runs.
        sleeps: list[float] = []
        monkeypatch.setattr("app.backoff.time.sleep", lambda d: sleeps.append(d))
        seen: list[int] = []
        for attempt in retry_sync(5, 1.0):
            seen.append(attempt)
            if attempt == 0:
                break
        assert seen == [0]
        assert sleeps == []
