# Problem

**Category:** `backend` / `system-design`

## Statement

Implement a circuit breaker that wraps calls to an unreliable dependency, trips to a fail-fast state during an outage, and probes for recovery — then measure it against naive retry-with-backoff (no breaker) under a simulated outage.

**States:** `CLOSED` (calls pass through, outcomes tracked), `OPEN` (calls rejected immediately, dependency never touched), `HALF_OPEN` (a limited number of trial calls probe the dependency).

**Requirements:**

- `CircuitBreaker(failure_threshold, min_requests, window_size, reset_timeout, half_open_max_calls, clock)`.
- `.call(func, *args, **kwargs)` — executes `func` through the breaker; returns its result on success, re-raises its exception on failure (the exception still propagates to the caller — the breaker only *observes* failure, it never swallows it), and raises `CircuitOpenError` immediately (without calling `func`) when the circuit is `OPEN`.
- `.state` — current state (`CLOSED` / `OPEN` / `HALF_OPEN`), for observation and testing.
- Rolling window of the last `window_size` outcomes while `CLOSED`. Trips to `OPEN` only once at least `min_requests` calls are present in the window **and** the failure rate within it is `>= failure_threshold`.
- After `reset_timeout` seconds (measured via an injectable `clock`) spent in `OPEN`, the next call is treated as a trial and transitions the breaker to `HALF_OPEN`.
- In `HALF_OPEN`: up to `half_open_max_calls` trial calls are allowed through. A single trial failure reopens the circuit (`OPEN`, timer reset to `reset_timeout` from now). If every trial succeeds, the circuit closes (`CLOSED`, window reset empty).
- Injectable `clock: Callable[[], float]` (defaults to `time.monotonic`) so tests control elapsed time without real sleeping.

## Constraints

- Standard library only.
- Single-threaded — no lock / thread-safety requirement (noted as a stretch).
- Tests must be deterministic: drive time through a fake clock, never `time.sleep`.

## Approach

1. **Count-based rolling window, not time buckets.** A `deque(maxlen=window_size)` of booleans is enough to show the trip behavior and is simpler than Hystrix-style time-windowed buckets, which are left as a stretch.
2. **`min_requests` guards against tripping on a tiny, unrepresentative sample** — 2 calls and 2 failures is a 100% failure rate but not enough signal to act on; the breaker must ignore the rate until the window has enough entries.
3. **`OPEN` short-circuits before `func` is ever touched.** That's the entire point: during an outage a naive caller keeps paying the dependency's full latency/timeout on every attempt, while the breaker converts each attempt into an immediate, cheap `CircuitOpenError`.
4. **`HALF_OPEN` trial calls are the only way out of `OPEN`.** Time alone doesn't prove recovery — a real call does. A single failed trial is treated as proof the dependency is still down, which resets the full `reset_timeout` rather than decaying or extending it incrementally.
5. **Exceptions always propagate to the caller.** The breaker's job is deciding *whether to attempt* the call, not hiding the dependency's errors.
6. **Measurement**: simulate a dependency that fails deterministically for a fixed number of calls (the "outage") and then recovers, driving it from two callers on the same fake-clock timeline — (a) naive retry with exponential backoff and no breaker, (b) the same retry loop wrapped in the breaker. Compare: total calls that actually reach the dependency during the outage window, and how quickly each caller notices recovery once the dependency comes back.

## Stretch

- Time-windowed rolling buckets (Hystrix-style) instead of a count-based window.
- Thread-safe version with a lock around state transitions.
- Exponential backoff applied to `reset_timeout` itself across repeated reopens, instead of a fixed timeout.
- Per-dependency breaker registry keyed by name, so one process can guard several downstreams independently.
