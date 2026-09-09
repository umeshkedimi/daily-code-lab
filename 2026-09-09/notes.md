## Implementation

- `RetryPolicy` is a small frozen dataclass (`max_attempts`, `base_delay`, `max_delay`) — pure configuration, no behavior, so a caller can define a "fast local retry" policy and a "patient upstream API" policy side by side without touching the retry logic itself.
- `is_retryable(exc)` is the default classifier: built-in `TimeoutError`/`ConnectionError` and their `httpx` equivalents (`TimeoutException`, `ConnectError`, `ReadError`) are always retryable; `httpx.HTTPStatusError` is retryable only if `status_code >= 500`. Everything else (a `ValueError` from bad input, a 404, a 400) is not retryable by default. This function is a parameter, not a hardcoded check, specifically so a caller with different semantics (e.g. treating 429 Too Many Requests as retryable too) can swap in their own without touching `retry_async`.
- `compute_backoff(attempt, policy)` implements *full jitter*: `random.uniform(0, min(max_delay, base_delay * 2**(attempt-1)))`. Chose full jitter over "equal jitter" (`half the exponential delay, plus a random half`) or no jitter at all — full jitter has the most spread and does the best job avoiding synchronized retries, at the cost of occasionally picking a very short delay right after a failure (an accepted trade-off, elaborated below).
- `retry_async` is a plain function taking `func` and its args/kwargs, not a decorator — deliberately, so the *same* retry call can be configured differently per call site (different `policy`/`classify` for different operations) rather than a decorator baking one policy onto a function permanently at definition time.
- The loop's structure matters: on each attempt, `try: return await func(...)` means success exits immediately with no wrapping; the `except` branch first checks `classify(exc)` (fail fast if not retryable), then checks `attempt == max_attempts` (raise `RetryError` if this was the last chance), and only then computes a delay and sleeps. There's no sleep after the final failed attempt — the loop doesn't waste a delay it will never get to use.
- `on_retry` is an optional callback invoked right before each backoff sleep, given `(attempt, exception, delay)` — used here just for logging in the demo, but the same hook is exactly where a caller would wire in a retry-count metric or a circuit-breaker's failure counter in a production system.
- Edge case handled: a function that succeeds on the very first try never touches the backoff/classify machinery at all — the common case stays cheap.
- Edge case handled: `_make_flaky`'s internal call counter is captured in a closure (`calls = {"count": 0}`), so each call to the returned `flaky()` coroutine function observes and increments shared state correctly across repeated `await`s within one `retry_async` loop.
- Not handled (explicitly out of scope): retry budgets shared *across* many different calls (e.g. "no more than 100 retries per minute across the whole service", to protect a struggling downstream from the retries of many different callers combined, not just one caller's own backoff) — see trade-offs.

## Complexity

- Time: O(max_attempts) calls to `func` in the worst case; wall-clock time is bounded above by `sum(min(max_delay, base_delay * 2^(k-1)) for k in range(1, max_attempts))` (the jitter ceilings), since actual jittered delays are always ≤ their ceiling.
- Space: O(1) — no history is retained beyond the current attempt count and the most recent exception.

## Follow-up Questions

**Why?**
Transient failures are the normal condition of any network call, not the exception — a load balancer draining a node, a momentary DNS hiccup, a downstream service briefly over capacity. Giving up on the first failure treats a fluctuation as a permanent outage; retrying blindly and immediately (no backoff) can turn a struggling service's momentary blip into a self-inflicted denial-of-service from your own client hammering it. A correct retry policy sits between those two failure modes.

**How exactly?**
`retry_async` calls `func`; on an exception, it asks the classifier whether this specific failure is worth retrying. If yes and attempts remain, it computes a jittered delay from `compute_backoff`, calls the optional `on_retry` hook, awaits `asyncio.sleep(delay)` (yielding the event loop rather than blocking it), and loops back to try again. If the classifier says no, or attempts are exhausted, the loop raises — either the original exception (fail-fast path) or a `RetryError` wrapping the last one (exhausted path).

**Which algorithm?**
Exponential backoff with full jitter, per the formula in [Implementation](#implementation). This is the same family of technique used by real HTTP client libraries and cloud SDKs (e.g. AWS SDKs, gRPC's default retry policy) for exactly this problem — it's a solved, well-studied pattern, not something to reinvent from scratch, so this exercise deliberately implemented the standard, named formula rather than inventing an ad hoc one.

**Which library?**
Standard library only for the retry machinery itself (`asyncio.sleep`, `random.uniform`) — no retry library (e.g. `tenacity`) was used, specifically so the mechanics (classification, backoff math, fail-fast vs. exhaustion) are visible and owned rather than hidden inside a dependency. `httpx` is used only for the real-HTTP demo scenario, exactly as in [[async-multi-api-fetch]].

**What happens internally?**
`asyncio.sleep(delay)` doesn't block the event loop or the thread — it suspends the current coroutine and lets the loop run any other pending work (e.g. other concurrent retry loops, other requests) until the delay elapses, then resumes exactly this coroutine. This is why retry logic composes cleanly with the concurrent fan-out from [[async-multi-api-fetch]]: many `retry_async` calls backing off simultaneously don't block each other or waste a thread each.

**How is it implemented?**
See [Implementation](#implementation) above.

**What if this fails?**
- The operation is retryable but never recovers within `max_attempts`: `RetryError` is raised, carrying both the attempt count and the actual last exception — the caller gets a clear, distinguishable signal ("this was a retry exhaustion, and here's what kept failing") rather than an ambiguous final exception that looks identical to a first-try failure.
- The operation is not idempotent (e.g. it charges a payment, sends an email) and gets classified as retryable when it shouldn't be: this is a real correctness risk this implementation does *not* protect against — the classifier answers "is this exception type transient," not "is this operation safe to run twice." A production retry wrapper for non-idempotent operations needs an idempotency key (as in a real payment API), which is out of scope here but worth naming explicitly.
- The `classify` function itself throws: unhandled here — the classifier is trusted code, same as `func` and `policy`, not an untrusted boundary.

**What trade-offs did you consider?**
- Full jitter (chosen) vs. no jitter / "equal jitter": full jitter gives the best spread against thundering herds but means a retry can occasionally happen almost immediately (delay near 0) right after a failure, which for an already-struggling service could in rare cases retry sooner than ideal. Equal jitter (`half the max delay, plus random up to the other half`) guarantees a minimum backoff at the cost of less spread. Chose full jitter because it's the more commonly recommended default and the minimum-delay risk is small relative to the thundering-herd risk it avoids.
- Function + parameters (chosen) vs. a `@retry(policy)` decorator: a decorator reads nicer at the call site for a single fixed policy, but bakes that policy in at definition time — this implementation's plain-function form lets the same wrapped operation be retried differently depending on context (e.g. a background job retries much more patiently than a user-facing request path calling the identical underlying function).
- Per-call backoff only (chosen) vs. a shared, service-wide retry budget: this implementation only limits how much *one* caller retries *one* operation. It says nothing about the aggregate retry load many different callers put on a struggling downstream at once — that requires a shared budget or a circuit breaker layered on top, deliberately left out to keep today's scope to the retry primitive itself.

**How do you debug it?**
- The `on_retry` hook is the primary debugging surface — logging `(attempt, exception, delay)` on every retry (as the demo does) turns "why did this take 3 seconds" into a visible timeline instead of a mystery.
- To confirm the classifier is doing the right thing, the fastest check is scenario 2's timing: a non-retryable failure should return in effectively 0 seconds (no sleep at all) — if a call that should fail fast is instead visibly pausing, the classifier is misclassifying it as retryable.
- To confirm jitter is real rather than deterministic, sample `compute_backoff` directly at a fixed `attempt` several times (scenario 4) — if the values are identical or suspiciously close every time, the randomization isn't working (e.g. an unseeded-but-somehow-deterministic RNG, or a bug that always takes the ceiling instead of a uniform sample below it).
- For real endpoints, `httpbin.org/status/<code>` is a deterministic way to force any specific status code without depending on a real service happening to be broken at test time — used here to separately verify the 500 (retry) and 404 (no retry) paths.

**How do you evaluate it?**
- Recovery works: a function failing twice then succeeding returns the success value through `retry_async` with the expected number of logged retry attempts (verified in scenario 1).
- Fail-fast works: a non-retryable exception returns in ~0s with exactly one attempt made (verified in scenario 2, re-run twice with consistent sub-millisecond timing).
- Bounded exhaustion works: a persistently-failing retryable operation stops at exactly `max_attempts` and raises `RetryError` with the correct attempt count and wrapped exception, with visibly growing backoff delays between attempts (verified in scenario 3, re-run twice — delays varied run to run as expected from jitter, but attempt count and final behavior were identical).
- Jitter is real: sampling the same attempt number repeatedly produces different delays within the correct ceiling, not a constant (verified in scenario 4).
- Classifier generalizes to real HTTP failures, not just synthetic exceptions: `httpbin.org/status/500` was retried to exhaustion and `httpbin.org/status/404` failed on the first attempt, both re-confirmed on a second full run (verified in scenario 5).
- Not evaluated here: idempotency safety for non-idempotent operations, and behavior under a shared/service-wide retry budget — both named as explicit gaps above, not silent ones.

## Key Learnings

- "Retryable" is a property of the *failure*, not the *operation* — the same HTTP call can fail in a way that's worth retrying (a timeout) or a way that's actively wrong to retry (a 400), so the classification has to inspect the specific exception, not just "did this function call fail."
- Jitter isn't a nice-to-have polish step on top of exponential backoff — without it, many independent callers experiencing the same outage would resynchronize on every retry round and repeatedly hit the recovering service in unison, which is a self-inflicted version of the exact problem backoff exists to prevent.
- A retry wrapper only ever answers "should I try this exact call again" — it says nothing about whether trying it again is *safe* (idempotency) or whether *many other callers* retrying at once collectively overwhelms the thing they're all retrying against (a shared budget/circuit breaker). Naming both as explicitly out of scope is more honest than a "handles retries" description that quietly assumes them away.
