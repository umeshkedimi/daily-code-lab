# Problem

**Category:** `backend`

## Statement

Build an **async Python service** that fetches data from 3 independent external APIs and combines their responses into a single result.

**Requirements:**
- The API calls should execute **concurrently**, not sequentially, to minimize total response time.
- Handle **timeouts and partial failures** without blocking or crashing the entire operation.

## Constraints

- Total wall-clock time should track the *slowest* single call, not the sum of all calls — that's the actual proof concurrency is happening.
- One source failing (timeout, non-2xx status, connection error) must not prevent the other sources' data from being returned.
- The caller always gets back one combined result — never a raised exception from a single bad source, and never a partial hang.

## Approach

1. **Concurrency primitive: `asyncio.gather`.** Three independent, unrelated network calls with no data dependency between them is the textbook case for `asyncio.gather` — kick all three coroutines off, `await` them together, and let the event loop interleave the I/O waits.

2. **HTTP client: `httpx.AsyncClient`.** Needed an async-native HTTP client with real per-request timeout support and a typed exception hierarchy (`httpx.TimeoutException`, `httpx.HTTPStatusError`, `httpx.HTTPError`) to distinguish *why* a source failed.

3. **Failure isolation at the coroutine boundary, not at `gather()`.** The key design decision: each per-source coroutine (`fetch_source`) catches its own exceptions internally and always returns a `SourceResult` — it never raises. That means `asyncio.gather()` is called in its default mode (no `return_exceptions=True` needed), and a failure in one source is structurally incapable of cancelling or crashing the others, because from `gather`'s point of view every coroutine "succeeded" — it just may have succeeded in reporting failure.

4. **Combine into one structured result**, not a bag of raw responses: a `CombinedResult` with `data` (successful sources only), `errors` (failed sources with reasons), and an `overall_status` of `complete` / `partial` / `failed` — so the caller can make a decision (serve partial data? return 200 with a warning? 502?) instead of the library making that call for them.

5. **Demonstrated, not just implemented, both required behaviors**: a happy-path run against 3 real, healthy public APIs (proves concurrency via wall-clock time), and a second run where one source is forced to time out and another forced to return HTTP 500 (proves partial-failure handling), both in `solution.py`'s `demo()`.
