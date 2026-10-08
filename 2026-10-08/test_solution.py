import random

import pytest

from solution import (
    FixedWindowCounter,
    NaiveSlidingWindowLog,
    SlidingWindowCounter,
    SlidingWindowLog,
    TokenBucket,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# --- FixedWindowCounter ------------------------------------------------------------


def test_fixed_window_admits_up_to_limit_then_rejects():
    clock = FakeClock()
    lim = FixedWindowCounter(limit=3, window_seconds=10, clock=clock)
    assert [lim.allow() for _ in range(3)] == [True, True, True]
    assert lim.allow() is False


def test_fixed_window_resets_on_new_window():
    clock = FakeClock()
    lim = FixedWindowCounter(limit=2, window_seconds=10, clock=clock)
    assert lim.allow() and lim.allow()
    assert lim.allow() is False
    clock.now = 10.0  # next window starts exactly at a multiple of window_seconds
    assert lim.allow() is True


def test_fixed_window_rejects_invalid_config():
    with pytest.raises(ValueError):
        FixedWindowCounter(limit=0, window_seconds=1, clock=FakeClock())
    with pytest.raises(ValueError):
        FixedWindowCounter(limit=1, window_seconds=0, clock=FakeClock())


def test_fixed_window_boundary_burst_can_reach_2x_limit():
    # The defining flaw: limit requests at the end of one window plus limit
    # more at the start of the next are both admitted, even though they all
    # land within a span far shorter than window_seconds.
    clock = FakeClock()
    lim = FixedWindowCounter(limit=5, window_seconds=10, clock=clock)
    clock.now = 9.99
    admitted_before = sum(lim.allow() for _ in range(5))
    clock.now = 10.01
    admitted_after = sum(lim.allow() for _ in range(5))
    assert admitted_before == 5
    assert admitted_after == 5  # 10 requests admitted in a 0.02s span, for a limit of 5


# --- SlidingWindowLog ---------------------------------------------------------------


def test_sliding_log_admits_up_to_limit_then_rejects():
    clock = FakeClock()
    lim = SlidingWindowLog(limit=3, window_seconds=10, clock=clock)
    assert [lim.allow() for _ in range(3)] == [True, True, True]
    assert lim.allow() is False


def test_sliding_log_does_not_allow_boundary_burst():
    # The exact fix for the fixed-window flaw: admitting 5 just before a
    # boundary leaves no room for 5 more just after it, because the first 5
    # are still within the trailing 10s window.
    clock = FakeClock()
    lim = SlidingWindowLog(limit=5, window_seconds=10, clock=clock)
    clock.now = 9.99
    admitted_before = sum(lim.allow() for _ in range(5))
    clock.now = 10.01
    admitted_after = sum(lim.allow() for _ in range(5))
    assert admitted_before == 5
    assert admitted_after == 0


def test_sliding_log_admits_again_once_old_entries_expire():
    clock = FakeClock()
    lim = SlidingWindowLog(limit=2, window_seconds=10, clock=clock)
    assert lim.allow() and lim.allow()
    assert lim.allow() is False
    clock.now = 10.0  # the first admitted request (t=0) is now exactly window_seconds old
    assert lim.allow() is True  # cutoff is exclusive-at-boundary: t=0 <= 10.0 - 10.0 is evicted


def test_sliding_log_length_never_exceeds_limit_under_sustained_load():
    clock = FakeClock()
    lim = SlidingWindowLog(limit=4, window_seconds=1.0, clock=clock)
    for i in range(2000):
        clock.now = i * 0.1  # far faster than the window drains relative to limit
        lim.allow()
        assert len(lim) <= 4


# --- SlidingWindowLog vs. NaiveSlidingWindowLog (differential) ---------------------


@pytest.mark.parametrize("seed", range(25))
def test_sliding_log_matches_naive_reference_on_random_traffic(seed):
    rng = random.Random(seed)
    limit = rng.randint(1, 10)
    window = rng.choice([1.0, 5.0, 10.0])
    clock = FakeClock()
    fast = SlidingWindowLog(limit, window, clock)
    naive = NaiveSlidingWindowLog(limit, window, clock)

    t = 0.0
    for _ in range(300):
        t += rng.uniform(0, window / 2)  # mix of bursts and gaps relative to the window
        clock.now = t
        assert fast.allow() == naive.allow()


# --- SlidingWindowCounter ------------------------------------------------------------


def test_sliding_counter_admits_up_to_limit_within_one_window():
    clock = FakeClock()
    lim = SlidingWindowCounter(limit=3, window_seconds=10, clock=clock)
    assert [lim.allow() for _ in range(3)] == [True, True, True]
    assert lim.allow() is False


def test_sliding_counter_greatly_reduces_the_boundary_burst_vs_fixed_window():
    clock = FakeClock()
    lim = SlidingWindowCounter(limit=10, window_seconds=10, clock=clock)
    clock.now = 9.99  # end of window 0: elapsed_fraction ~ 0.999, cheap to fill
    admitted_before = sum(lim.allow() for _ in range(10))
    clock.now = 10.01  # start of window 1: previous window's count is barely discounted
    admitted_after = sum(lim.allow() for _ in range(10))
    assert admitted_before == 10
    # Not zero (it's an approximation, not exact), but nowhere near the
    # fixed-window counter's full second batch of 10.
    assert admitted_after <= 2


def test_sliding_counter_discounts_previous_window_as_time_passes():
    # After almost a full window has elapsed since the rollover, the
    # previous window's count should be almost entirely "scrolled out" --
    # a limiter that forgot the (1 - elapsed_fraction) discount and just
    # summed both windows' raw counts would wrongly keep rejecting here.
    clock = FakeClock()
    lim = SlidingWindowCounter(limit=10, window_seconds=10, clock=clock)
    for _ in range(10):
        assert lim.allow()
    clock.now = 19.9  # 0.99 of the way through the next window
    admitted = sum(lim.allow() for _ in range(10))
    assert admitted >= 8


def test_sliding_counter_handles_a_gap_longer_than_one_window():
    clock = FakeClock()
    lim = SlidingWindowCounter(limit=2, window_seconds=10, clock=clock)
    assert lim.allow() and lim.allow()
    clock.now = 100.0  # many empty windows have passed; nothing should carry over
    assert lim.allow() and lim.allow()
    assert lim.allow() is False


# --- TokenBucket ---------------------------------------------------------------------


def test_token_bucket_allows_a_burst_up_to_capacity_then_throttles():
    clock = FakeClock()
    bucket = TokenBucket(capacity=5, rate=1, clock=clock)
    assert [bucket.allow() for _ in range(5)] == [True] * 5
    assert bucket.allow() is False  # bucket just drained


def test_token_bucket_refills_over_time():
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, rate=1, clock=clock)
    assert bucket.allow() is True
    assert bucket.allow() is False
    clock.now = 0.999
    assert bucket.allow() is False  # not quite a full token back yet
    clock.now = 1.0
    assert bucket.allow() is True


def test_token_bucket_never_exceeds_capacity_even_after_a_long_idle_period():
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, rate=1, clock=clock)
    bucket.allow()  # drain to 2 remaining
    clock.now = 1000.0  # idle far longer than it would take to refill past capacity
    admitted = sum(bucket.allow() for _ in range(10))
    assert admitted == 3  # capped at `capacity`, not however many 1000s of elapsed seconds imply


def test_token_bucket_rejects_invalid_config():
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, rate=1, clock=FakeClock())
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, rate=0, clock=FakeClock())


@pytest.mark.parametrize("seed", range(10))
def test_token_bucket_long_run_admitted_rate_converges_to_configured_rate(seed):
    rng = random.Random(seed)
    rate = rng.choice([1.0, 5.0, 10.0])
    clock = FakeClock()
    bucket = TokenBucket(capacity=rate, rate=rate, clock=clock)  # no initial burst credit beyond one "window"
    duration = 2000.0
    # Offer requests far more often than the bucket can admit, so the
    # admitted count is bucket-limited, not offered-limited.
    admitted = 0
    t = 0.0
    while t < duration:
        clock.now = t
        if bucket.allow():
            admitted += 1
        t += rng.uniform(0.001, 0.01)
    observed_rate = admitted / duration
    assert observed_rate == pytest.approx(rate, rel=0.05)
