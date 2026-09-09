# Problem

**Category:** `backend`

## Statement

Build a **retry mechanism** for calling an unreliable async operation (an external API call), with **exponential backoff and jitter**, that:

- Retries transient failures (timeouts, connection errors, 5xx) up to a configurable maximum number of attempts.
- Does **not** retry non-transient failures (4xx client errors) — retrying those is wasted work at best, harmful at worst.
- Backs off between attempts with a delay that grows exponentially and is randomized (jittered), rather than fixed or purely deterministic.
- Reports clearly when it gives up, including the final underlying error.

## Constraints

- A non-retryable failure must fail on the very first attempt — no delay, no wasted retry budget.
- A retryable failure that never recovers must still terminate (bounded by `max_attempts`), not retry forever.
- Backoff delay must grow with each attempt but be capped, and must not be identical across repeated calls (jitter) — otherwise many callers failing at once would all retry in lockstep and hit the recovering service with another synchronized wave (a thundering herd).
- The retry logic must be reusable across arbitrary async callables, not hardcoded to one specific API call.

## Approach

1. **Separate "how to retry" from "what's worth retrying."** `retry_async(func, ...)` is generic — it takes any async callable — and a pluggable `classify(exc) -> bool` function decides whether a given exception should trigger another attempt. The default classifier (`is_retryable`) treats transport-level errors (timeouts, connection errors) and 5xx HTTP responses as retryable, and everything else — in particular 4xx — as not.

2. **Exponential backoff with full jitter**, following the standard formula (as popularized by AWS's "Exponential Backoff And Jitter" architecture writeup): `delay = uniform(0, min(max_delay, base_delay * 2^(attempt-1)))`. The delay's *ceiling* grows exponentially with each attempt (spreading load further out the longer a service struggles), but the *actual* delay is randomized within that ceiling on every call, which is what prevents synchronized retry storms.

3. **Fail fast on non-retryable errors.** The retry loop checks `classify(exc)` before deciding to sleep-and-retry; if the classifier says no, the original exception is re-raised immediately, with zero delay and zero wasted attempts.

4. **Distinguish "gave up" from "the actual error."** When every attempt is exhausted, the loop raises a purpose-built `RetryError` that carries both `attempts` (how many were tried) and `last_exception` (what actually kept failing) — so a caller catching it can log or alert with the real underlying cause, not just "it didn't work."

5. **Proved every claim empirically**, not just by code inspection: a function that fails twice then recovers (retry does its job), a function that always raises a non-retryable error (fails instantly, no delay), a function that always raises a retryable error (exhausts attempts, delays visibly grow), a direct sample of `compute_backoff` showing real variance at a fixed attempt number (jitter is real, not decorative), and the same classifier applied to real HTTP calls (`httpbin.org/status/500` retried and exhausted, `httpbin.org/status/404` failed immediately).
