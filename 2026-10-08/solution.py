"""Four rate-limiting algorithms, sharing one interface (`allow() -> bool`,
single key, time injected via a clock), compared on the thing that actually
distinguishes them: how they behave right at a window boundary and under
sustained load, not just "do they roughly cap requests."

- FixedWindowCounter: O(1) time and memory, but admits up to 2x the
  configured limit in a short burst that straddles a window boundary.
- SlidingWindowLog: exact (it IS the definition of "N requests in the last
  W seconds"), at the cost of O(limit) memory -- it logs every admitted
  request's timestamp to know precisely which are still in-window.
- SlidingWindowCounter: an O(1)-memory approximation of the sliding log,
  using a weighted blend of the current and previous fixed window's counts.
  Fixes most of the boundary burst at a small, measured accuracy cost.
- TokenBucket: a different contract entirely -- not "N requests per window"
  but "a steady refill rate, plus a bounded burst allowance" -- included to
  show that "rate limiter" is not one specification but a family of them.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Callable, Deque, List, Protocol

Clock = Callable[[], float]


class RateLimiter(Protocol):
    def allow(self) -> bool:
        """True if the request happening right now is admitted."""
        ...


class FixedWindowCounter:
    """Divides time into back-to-back windows of length `window_seconds`.
    Counts requests in the current window; resets the count the instant
    the window changes. O(1) time and memory -- and exactly as cheap as
    that sounds, which is the whole problem: nothing stops `limit` requests
    landing in the last instant of one window and another `limit` landing
    in the first instant of the next."""

    def __init__(self, limit: int, window_seconds: float, clock: Clock):
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._window_index = None
        self._count = 0

    def allow(self) -> bool:
        window_index = math.floor(self._clock() / self.window_seconds)
        if window_index != self._window_index:
            self._window_index = window_index
            self._count = 0
        if self._count < self.limit:
            self._count += 1
            return True
        return False


class SlidingWindowLog:
    """Logs the timestamp of every *admitted* request; admits a new one iff
    fewer than `limit` logged timestamps still fall within the last
    `window_seconds`. This is the precise, by-definition answer to "how many
    requests in the trailing window" -- every other algorithm here is either
    this exactly, or a cheaper approximation of it.

    Memory is O(limit), not O(total requests ever): a rejected request is
    never logged, and `limit` admitted requests is the most that can ever
    be in-window at once, so the deque never grows past `limit` entries."""

    def __init__(self, limit: int, window_seconds: float, clock: Clock):
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._log: Deque[float] = deque()

    def allow(self) -> bool:
        now = self._clock()
        cutoff = now - self.window_seconds
        while self._log and self._log[0] <= cutoff:
            self._log.popleft()
        if len(self._log) < self.limit:
            self._log.append(now)
            return True
        return False

    def __len__(self) -> int:
        return len(self._log)


class NaiveSlidingWindowLog:
    """Reference for SlidingWindowLog, built differently on purpose: logs
    EVERY attempt (admitted or not) to an unbounded list, and recomputes the
    in-window admitted count from scratch on every call by filtering that
    whole list -- O(total attempts ever) per call, versus the real
    implementation's O(1) amortized (each entry pushed and popped at most
    once). Correct by inspection; used only to cross-check, never for the
    timing/memory comparisons."""

    def __init__(self, limit: int, window_seconds: float, clock: Clock):
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._all_attempts: List[float] = []
        self._admitted: List[float] = []

    def allow(self) -> bool:
        now = self._clock()
        self._all_attempts.append(now)
        cutoff = now - self.window_seconds
        in_window = [t for t in self._admitted if t > cutoff]
        self._admitted = in_window
        if len(in_window) < self.limit:
            self._admitted.append(now)
            return True
        return False


class SlidingWindowCounter:
    """O(1)-memory approximation of SlidingWindowLog: keeps only a count for
    the current fixed window and the previous one, and estimates the
    trailing-window count as a weighted blend of the two -- the previous
    window's count is discounted by how much of it has already "scrolled
    out" of the trailing window as time moves through the current one.

    `estimate = previous_count * (1 - elapsed_fraction) + current_count`
    where `elapsed_fraction` is how far into the current window `now` is.
    This assumes the previous window's requests were spread evenly across
    it, which is an approximation, not a fact -- see notes.md for measured
    error bounds against the exact log."""

    def __init__(self, limit: int, window_seconds: float, clock: Clock):
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._window_index = None
        self._current_count = 0
        self._previous_count = 0

    def _roll_windows(self, window_index: int) -> None:
        if window_index == self._window_index:
            return
        is_consecutive_window = self._window_index is not None and window_index == self._window_index + 1
        if is_consecutive_window:
            self._previous_count = self._current_count
        else:
            # First call, or a gap of more than one window with no traffic:
            # nothing meaningful carries over from a window with no data.
            self._previous_count = 0
        self._current_count = 0
        self._window_index = window_index

    def allow(self) -> bool:
        now = self._clock()
        window_index = math.floor(now / self.window_seconds)
        self._roll_windows(window_index)
        window_start = window_index * self.window_seconds
        elapsed_fraction = (now - window_start) / self.window_seconds
        estimate = self._previous_count * (1 - elapsed_fraction) + self._current_count
        if estimate < self.limit:
            self._current_count += 1
            return True
        return False


class TokenBucket:
    """A bucket holds up to `capacity` tokens, refilling continuously at
    `rate` tokens/second; each request costs one token and is admitted iff
    the bucket has at least one. Refill is computed lazily (elapsed time
    since the last check), not via a background timer.

    A different contract from the three above: this bounds *burst size*
    (at most `capacity` requests can land back-to-back) and *sustained
    rate* (long-run throughput converges to `rate`) rather than bounding
    "requests in any trailing window of length W" directly."""

    def __init__(self, capacity: float, rate: float, clock: Clock):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.capacity = capacity
        self.rate = rate
        self._clock = clock
        self._tokens = capacity
        self._last_refill = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        self._last_refill = now
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def allow(self) -> bool:
        self._refill()
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


def simulate(limiter: RateLimiter, timestamps: List[float], clock_state: List[float]) -> List[bool]:
    """Drives `limiter` through `timestamps` in order, setting the shared
    mutable `clock_state[0]` before each call so the limiter's injected
    clock reads the right time. Returns the admit/reject decision per
    timestamp, in order."""
    decisions = []
    for t in timestamps:
        clock_state[0] = t
        decisions.append(limiter.allow())
    return decisions


def demo() -> None:
    limit, window = 100, 10.0

    def boundary_burst(ctor):
        now = [0.0]
        lim = ctor(limit, window, lambda: now[0])
        now[0] = window - 0.01
        before = sum(lim.allow() for _ in range(limit))
        now[0] = window + 0.01
        after = sum(lim.allow() for _ in range(limit))
        return before, after

    print(f"1) Boundary burst: {limit} requests just before a window boundary, {limit} more just after (limit={limit}, window={window}s)")
    for name, ctor in [
        ("FixedWindowCounter", FixedWindowCounter),
        ("SlidingWindowLog", SlidingWindowLog),
        ("SlidingWindowCounter", SlidingWindowCounter),
    ]:
        before, after = boundary_burst(ctor)
        print(f"   {name:>22}: {before} admitted before, {after} after  ->  {before + after} total in a 0.02s span")

    print(f"\n2) SlidingWindowLog memory stays bounded by `limit`, not by total requests ever seen")
    now = [0.0]
    log = SlidingWindowLog(limit=50, window_seconds=1.0, clock=lambda: now[0])
    max_len = 0
    for i in range(200_000):
        now[0] = i * 0.00002
        log.allow()
        max_len = max(max_len, len(log))
    print(f"   max internal length over 200,000 requests (limit=50): {max_len}")

    print(f"\n3) TokenBucket: bounded burst right after an idle period, then throttled to the steady rate")
    now = [1000.0]
    bucket = TokenBucket(capacity=20, rate=5, clock=lambda: now[0])
    burst = sum(bucket.allow() for _ in range(30))
    print(f"   capacity=20, rate=5/s: admitted {burst}/30 immediately after a long idle period")


if __name__ == "__main__":
    demo()
