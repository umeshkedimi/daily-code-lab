## Implementation

- `CircuitBreaker.call` reads the clock once per call (`now`) and switches on `self._state`:
  - **OPEN**: if `now - opened_at < reset_timeout`, raise `CircuitOpenError` without touching `func`. Otherwise the breaker falls through into the `HALF_OPEN` branch in the same call — the call that discovers the timeout elapsed *is* the first trial, no wasted round-trip.
  - **HALF_OPEN**: each call consumes one of `half_open_max_calls` trial slots. Any exception immediately reopens (`_trip`) and re-raises — no failure-counting needed, a single trial failure is sufficient proof the dependency is still down. A success only closes the circuit once the trial budget is exhausted (`half_open_calls >= half_open_max_calls`); until then it stays `HALF_OPEN` and returns normally.
  - **CLOSED**: outcomes go into a `deque(maxlen=window_size)` of booleans. The trip check (`_should_trip`) only runs on failure — a success can never push the failure rate up, so checking after a success would be wasted work.
- `_should_trip` is a short-circuit: `len(window) < min_requests` returns `False` before ever computing a rate, so a handful of early failures on a cold breaker can't trip it.
- `_reset_closed` clears the window on close — history from before the outage (or before a previous half-open failure) never leaks into the next closed period's failure-rate calculation.
- Dropped an initial `_half_open_failures` counter — the "any single trial failure reopens" rule never needed a count, only a boolean "did this one fail", so the field was dead state. Caught by re-reading `call()` after the first green run.
- Not built: thread-safety (lock around state transitions), time-windowed rolling buckets, backoff on `reset_timeout` itself, per-dependency registry — all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `call` (closed fast-path) | O(1) amortized — `deque.append` is O(1), `_should_trip`'s `.count()` is O(window_size) | O(window_size) |
| `call` (open fast-path) | O(1) | O(1) |
| `call` (half-open) | O(1) | O(1) |

`_should_trip`'s `window.count(False)` is technically O(window_size) per failure, not O(1) — negligible at the window sizes here (10-20), but a real implementation under load would track a running failure count incrementally instead of rescanning the deque.

## Follow-up Questions

**Why fail fast instead of just retrying with backoff?** Retrying still pays the dependency's full timeout on every attempt. During a real outage (dependency hung, not just erroring instantly), that's the dominant cost — not the eventual error, but the wait. The breaker converts that wait into an immediate, cheap, local exception.

**How exactly does it decide to reopen from half-open?** Zero tolerance: one failed trial reopens immediately, full `reset_timeout` restarts. No decay, no partial credit — a half-open trial exists specifically to answer "is it safe to resume", and a single "no" is conclusive until proven otherwise by elapsed time.

**What happens internally during the gap between OPEN and the next trial?** Nothing — that's the point. No background timer, no polling thread. The breaker is purely reactive: the next `call()` that happens to arrive after `reset_timeout` has elapsed is what triggers the HALF_OPEN transition. If no one calls it, it just sits OPEN indefinitely.

**What if `func` itself is slow to fail (e.g. a real network timeout) rather than raising instantly?** The breaker only protects against calls it rejects outright (OPEN state). A trial call in HALF_OPEN, or any call while CLOSED, pays the dependency's real latency — the breaker reduces *how often* that latency is paid during an outage, not the latency of any individual call that's allowed through.

**What trade-offs did you consider?** Count-based window vs. time-based buckets: count-based is simpler and was sufficient to demonstrate the trip/reset-on-close behavior; time buckets (Hystrix-style) are more correct under bursty traffic (a count window can be dominated by one burst) but add real complexity for a one-day exercise. `half_open_max_calls > 1` vs. `== 1`: more trials give a sturdier recovery signal against a flaky-but-recovering dependency, at the cost of a slower, more cautious close.

**How do you evaluate it?** The outage simulation in `test_solution.py` (`test_breaker_reaches_dependency_far_less_than_naive_retry_during_outage`): naive retry-every-tick reaches the dependency 50/50 times during a 50-tick outage; the breaker reaches it only 10 times (5 to detect the outage via `min_requests`, 5 half-open probes at `reset_timeout` cadence) before correctly detecting recovery and closing. That's the whole value proposition in one number: 5x fewer calls to a dependency that's already struggling.

## Key Learnings

- The failure-rate check belongs only on the failure path. Putting it on every call (success included) is wasted work and, worse, a reasoning trap — I initially mistraced `test_rolling_window_evicts_old_failures_preventing_trip` assuming a post-success check would trip the breaker at exactly `failure_threshold`, which isn't how it should behave (a success should never be what causes a trip).
- Writing the test fixtures before the implementation (scaffold-first) surfaced two bugs by hand-tracing the state machine against them: a `min_requests=1` test that asserted "a single failure right after reset shouldn't trip" — which is self-contradictory once you trace it (with `min_requests=1`, a single failure *always* trips, by design) — and a call-count-based outage simulation that, with a realistic `reset_timeout`-gated breaker, would never finish within the test's own tick budget. Both were caught by tracing the implementation against the tests by hand before writing code, not by running it and seeing red.
- Reinforced: resetting shared state (the window) on a state transition is easy to state in a spec and easy to forget in code — it only showed up as a concrete requirement once a test depended on the post-close window actually being empty.
