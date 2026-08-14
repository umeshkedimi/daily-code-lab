## Implementation

- `Source` is a small frozen dataclass (`name`, `url`, `timeout`) — each source can carry its own timeout, since a weather API and a slow third-party service shouldn't be forced onto one blanket timeout.
- `fetch_source(client, source)` is the failure-isolation boundary: it wraps the actual `await client.get(...)` in a `try/except` covering, in order, `httpx.TimeoutException` (most specific), `httpx.HTTPStatusError` (raised by `response.raise_for_status()` for 4xx/5xx), then the general `httpx.HTTPError` (DNS failure, connection refused, etc.). Every branch returns a `SourceResult` — none of them re-raise. This function cannot fail loudly; it can only report failure as data.
- `gather_sources(sources)` opens one `httpx.AsyncClient` (so all three calls share connection pooling/keep-alive) and does `await asyncio.gather(*(fetch_source(client, s) for s in sources))`. Because every coroutine passed to `gather` always resolves successfully (per the point above), the default `gather` behavior — where one raised exception cancels the sibling tasks and propagates — never triggers. `return_exceptions=True` was deliberately *not* needed once failure was pushed down into each coroutine.
- Results are split into `succeeded`/`failed` by `status`, and `overall_status` is derived: `complete` (0 failures), `partial` (some but not all failed), `failed` (all failed) — three states instead of a boolean, because "some data came back" is a meaningfully different caller decision than "everything worked" or "nothing came back."
- `wall_clock_seconds` (measured around the whole `gather`) vs. the sum of each `SourceResult.elapsed_seconds` is printed side by side in the demo specifically to make concurrency *observable*, not just asserted — if these numbers were close to equal, that would indicate the calls ran sequentially.
- Edge case handled: a source can be slow without failing (`weather` at ~0.8s doesn't block `todo` from finishing at ~0.3s) — concurrency means the whole batch takes as long as the *slowest* member, not blocking cheap sources behind expensive ones.
- Edge case handled: an HTTP 500 and a client-side timeout are both "the source failed," but they're distinguishable in the output (`status: "error"` vs `status: "timeout"`) — a real caller might retry a timeout but not a 500.
- Not handled (explicitly out of scope): retries, circuit breaking, and an overall deadline for the whole `gather_sources` call (currently the total time is bounded only by the sum of misbehaving per-source timeouts in the worst case, not a hard ceiling — see trade-offs below).

## Complexity

- Time: O(max(t₁, t₂, t₃)) wall-clock, where tᵢ is each source's response time — not O(t₁ + t₂ + t₃) as a sequential version would be. This is the entire point of the exercise, and it's empirically verified in the demo output (e.g. wall_clock 0.82s vs. sum-of-calls 1.63s across 3 sources).
- Space: O(n) for n sources — one `SourceResult` per source held in memory at once; no unbounded growth.

## Follow-up Questions

**Why?**
Sequential HTTP calls to unrelated APIs waste wall-clock time waiting on I/O the CPU isn't doing anything useful during. This is the standard shape of a backend aggregation/BFF (backend-for-frontend) endpoint — "get me the user's profile, their recent orders, and their recommendations" — where the three calls have zero dependency on each other and serializing them only punishes the end user's latency for no reason.

**How exactly?**
`asyncio.gather(*(fetch_source(client, s) for s in sources))` schedules all three `fetch_source` coroutines onto the event loop at once. Each one immediately hits `await client.get(...)`, which suspends that coroutine at the point it starts waiting on the socket — control returns to the event loop, which then resumes whichever coroutine's I/O completes next. None of the three ever blocks a real OS thread while waiting; they're all just suspended Python objects until the loop wakes them.

**Which algorithm?**
Not really an algorithm in the classic sense — this is concurrent I/O orchestration ("fan-out/fan-in"): fan out N independent requests, wait for all N, fan back in to one combined structure. The one real algorithmic decision is the three-way status classification (`complete`/`partial`/`failed`) used to summarize N independent outcomes into one caller-facing verdict.

**Which library?**
`httpx` for the HTTP client (async-native, typed exceptions, per-request timeouts) and the standard library's `asyncio` for the concurrency primitive itself (`gather`). Considered `aiohttp` (the older, more established async HTTP library) — went with `httpx` since it shares one API surface between sync and async clients, which matters for testability (see debugging notes), and its exception hierarchy maps directly onto the ok/timeout/error split this problem needed.

**What happens internally?**
`asyncio.gather` wraps each coroutine in a `Task`, which the event loop schedules independently. Under the hood, `httpx.AsyncClient.get()` performs a non-blocking socket connect/write via `anyio`/`asyncio` transports; whenever the socket isn't immediately ready to read/write, the coroutine yields control back to the event loop via `await`, and the loop's `select`/`epoll`-based readiness poll (via `asyncio`'s selector event loop) decides which suspended coroutine to resume once its socket actually has data. `gather` collects results into the original submission order once every task in the group has completed.

**How is it implemented?**
See [Implementation](#implementation) above.

**What if this fails?**
- Per-source failure (the actual requirement): fully handled — caught inside `fetch_source`, surfaced as a `SourceResult` with `status="timeout"` or `status="error"`, never propagates.
- All three sources fail simultaneously: `gather_sources` still returns cleanly, with `overall_status="failed"` and an empty `data` dict — the caller decides what a fully-failed aggregation means for their endpoint (502? cached fallback?), the function doesn't decide for them.
- Not currently handled: an overall deadline. If every source's individual timeout is generous (say, 30s) and all three happen to be maximally slow at once, `gather_sources` takes ~30s even though each one "succeeds" from httpx's point of view — there's no wrapping `asyncio.wait_for(gather_sources(...), overall_timeout)`. Left out deliberately to keep today's scope to what was asked (per-source timeout handling), but it's the natural next layer for production use.

**What trade-offs did you consider?**
- Catch-inside-coroutine (chosen) vs. `asyncio.gather(..., return_exceptions=True)`: the latter is the "textbook" way to make `gather` failure-tolerant, but it returns a list that mixes real results and `Exception` instances, pushing an `isinstance` check onto every caller. Catching inside `fetch_source` means the return type is always `SourceResult` — a stronger, simpler contract for the caller, at the cost of slightly more code in `fetch_source` itself.
- Distinguishing `timeout` from `error` vs. collapsing both into a generic `failed`: chosen the finer-grained version because the correct caller response differs (retry a timeout, don't retry a 4xx) — worth the extra enum value.
- No overall deadline on `gather_sources`: correctly scoped to "handle per-source timeouts," but noted as a real gap rather than silently assumed away — see **What if this fails?**.
- `httpx` vs `aiohttp`: `aiohttp` is more battle-tested for very high-throughput async serving, but `httpx`'s shared sync/async API meant less friction for the demo/test code specifically — a real production choice would weigh this against team familiarity and existing stack.

**How do you debug it?**
- The demo's per-source timing breakdown (`elapsed_seconds` per source, printed alongside `wall_clock_seconds`) is the primary debugging signal — if wall-clock ever creeps close to the *sum* of individual times instead of the max, that's the tell that something is accidentally serializing (e.g. a shared resource lock, or an accidentally `await`ed-in-sequence loop instead of `gather`).
- To debug a specific source's failure, `SourceResult.error` carries the concrete exception message (`"timed out after 1.5s"`, `"HTTP 500"`), so the failure mode is visible without re-running under a debugger.
- Forcing failures deterministically for testing: `httpbin.org/delay/N` (server-side controllable delay, pair with a shorter client `timeout` to force `TimeoutException`) and `httpbin.org/status/N` (force any status code, e.g. 500) — both used directly in the demo's scenario 2, rather than relying on a real API happening to be down at test time.

**How do you evaluate it?**
- Concurrency actually happening: verified by comparing `wall_clock_seconds` to the sum of per-source `elapsed_seconds` — re-ran the happy path 3 times, wall-clock consistently tracked the slowest single call (e.g. 0.82s wall-clock vs 1.63s summed), never the sum.
- Partial-failure tolerance: verified with scenario 2 — a forced timeout and a forced HTTP 500 alongside one healthy source; confirmed `overall_status="partial"`, the healthy source's data still appears in `combined.data`, and both failures appear in `combined.errors` with distinguishable reasons, and — critically — the process didn't hang or raise.
- What's not covered: load/throughput behavior with many more than 3 sources, and behavior under the "all sources slow, no overall deadline" scenario flagged above as an open gap rather than something silently assumed fine.

## Key Learnings

- Pushing failure handling *into* each concurrent unit (so it always returns a value) is a cleaner pattern than trying to handle failures *around* the concurrency primitive (`gather(..., return_exceptions=True)`) — it keeps the aggregator's contract simple and typed, at the cost of a little more code per unit.
- "Concurrent" needs to be demonstrated, not just claimed — comparing wall-clock time against the sum of individual call times is a cheap, convincing way to prove it rather than trusting that `asyncio.gather` was used correctly.
- A binary success/fail signal is often not enough for a multi-source aggregation — `complete`/`partial`/`failed` as three explicit states pushes the "what do we do with partial data" decision to the right place (the caller), instead of the aggregation layer silently picking one behavior.
