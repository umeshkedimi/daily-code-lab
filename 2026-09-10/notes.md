## Implementation

- `AsyncConnectionPool.__init__` stores config only; no I/O happens until `start()`, which eagerly opens `min_size` connections and pushes them onto an `asyncio.Queue` (`self._idle`).
- `acquire()` is an `@asynccontextmanager`, so usage is `async with pool.acquire() as conn:` — the connection is guaranteed to be released (or dropped) via the `finally` block even if the caller's query raises.
- Inside `acquire()`: first, `asyncio.wait_for(self._permits.acquire(), timeout=self.acquire_timeout)` — this is the one line doing all the pool-bounding work. If a permit is already available, this returns instantly; if not, it waits (cooperatively, not blocking the event loop) until one frees up or the timeout fires, in which case `asyncio.TimeoutError` is caught and re-raised as the pool's own `PoolTimeoutError`.
- Once a permit is held, the code tries `self._idle.get_nowait()` first (reuse), falling back to `self._new_connection()` only if the idle queue is empty (`asyncio.QueueEmpty`). This ordering is what makes reuse the default path and creation the exception path — the pool only grows toward `max_size` under real demand, it doesn't pre-create eagerly beyond `min_size`.
- The liveness check (`if conn.is_closed(): ... conn = await self._new_connection()`) happens *after* popping from idle but *before* yielding to the caller — this is the "lazy health check" point: a connection that died while sitting idle gets replaced right here, invisibly to the caller, rather than the caller discovering a `ConnectionDoesNotExistError` mid-query.
- `_release()` mirrors that same check on the way back in: if the pool is closed or the connection died *during* use, it's closed (if not already) and discarded — `self._live_count -= 1` and it is never put back on `self._idle`. Either way, `self._permits.release()` always runs, because a permit represents pool *capacity*, not a specific connection object — capacity must be returned even when the connection backing it didn't survive.
- `close()` flips `self._closed = True` (so all future `acquire()` calls fail fast via the check at the top) and drains + closes every idle connection. It deliberately does *not* wait for currently-checked-out connections to be returned — see trade-offs on why that's a real, named limitation rather than a full graceful-drain.
- `stats()` exposes `live_connections`, `idle`, and `checked_out` as plain instance counters maintained alongside the real logic — kept as observability-only counters, explicitly not used to *gate* any decision (the semaphore and the queue are the only things actually enforcing correctness), so a bug in the stats bookkeeping can never corrupt pool behavior, only the debug output.
- Edge case handled: `min_size` connections created in `start()` never touch the semaphore — they're pre-warmed idle capacity, not "checked out," so the invariant (idle + checked-out ≤ max_size) holds immediately after `start()` as long as `min_size <= max_size`.
- Edge case handled: many concurrent `acquire()` calls racing when the idle queue has, say, 1 connection and 2 callers want one — `asyncio.Queue.get_nowait()` is safe to call concurrently (no torn reads), so exactly one of the two callers gets the idle connection and the other correctly falls through to creating a new one (bounded by its own already-won permit).
- Not handled (explicitly out of scope): a background reaper for idle connections that have sat unused too long (real pools often recycle idle connections after some max idle time to avoid stale server-side state) — this pool only ever checks liveness at acquire-time.

## Complexity

- Time: `acquire()` is O(1) in the common case (idle connection available, no wait) and bounded by `acquire_timeout` in the worst case (pool exhausted). `release()` is O(1).
- Space: O(max_size) — at most `max_size` live `asyncpg.Connection` objects exist at any time, whether idle or checked out.

## Follow-up Questions

**Why?**
Opening a new TCP connection and running Postgres's connection-setup handshake (auth, session initialization) on every single query is expensive relative to the query itself, and a database can only sustain a limited number of concurrent connections before it degrades (each backend process costs real memory and scheduling overhead on the Postgres server itself). A pool amortizes connection setup across many queries and puts a hard ceiling on how much connection load one client can put on the database — exactly the same "bound concurrency against a shared resource" shape as [[redis-distributed-lock]] and [[redis-rate-limiter]], just applied to database connections instead of a mutex or a request quota.

**How exactly?**
A caller does `async with pool.acquire() as conn: await conn.fetch(...)`. Under the hood: win a semaphore permit (waiting up to `acquire_timeout` if the pool is fully checked out), grab an idle connection or open a fresh one, verify it isn't already dead, hand it to the caller, then on exit (success or exception) either return it to the idle queue or discard it if it died mid-use — releasing the permit either way.

**Which algorithm?**
Not a classical algorithm — this is the standard "object pool" pattern (same family as a thread pool or a `sync.Pool` in other languages), specialized here with `asyncio.Semaphore` doing the bounded-concurrency enforcement and `asyncio.Queue` doing the idle-object storage. The one real "algorithmic" property worth naming is the invariant proof itself: every code path that can create or destroy a live connection is gated by the same semaphore, which is what makes the bound provable rather than just usually-true.

**Which library?**
`asyncpg` for the raw connection primitive (`asyncpg.connect`, `conn.is_closed()`, `conn.fetchval`) — deliberately *not* `asyncpg.create_pool`, since the whole point of the exercise was to build and understand the pooling logic itself rather than use the one that ships built-in. `asyncio`'s own `Semaphore`/`Queue`/`wait_for` for the concurrency control, same as prior days.

**What happens internally?**
`asyncio.Semaphore.acquire()` decrements an internal counter and returns immediately if it's still non-negative; if it would go negative, the calling coroutine is suspended and queued (FIFO) until a `release()` call wakes the longest-waiting one. `asyncio.wait_for` races that suspended coroutine against a timer task, cancelling whichever loses. `asyncpg`'s `conn.is_closed()` reflects the client-side socket/protocol state — a server-side `pg_terminate_backend` eventually surfaces as a closed connection on the client once the TCP layer notices (which is why the demo sleeps briefly after terminating before checking).

**How is it implemented?**
See [Implementation](#implementation) above.

**What if this fails?**
- Pool exhausted under sustained load: callers get `PoolTimeoutError` after `acquire_timeout` — a clear, typed, boundable failure instead of an indefinite hang. Verified directly (scenario 2: 2 of 4 concurrent long queries against `max_size=2` correctly time out).
- A connection dies while idle: caught lazily on next acquire via `is_closed()`, discarded, replaced transparently. Verified against a real Postgres backend kill (`pg_terminate_backend`), confirmed via the backend PID changing between the killed connection and its replacement.
- A connection dies *while checked out* (mid-query): the caller's query raises whatever `asyncpg` exception surfaces (e.g. a connection error), and `_release()`'s own `is_closed()` check on the way back in still correctly discards it rather than returning a broken connection to idle — but the caller's own exception isn't retried by the pool itself; that's layered on separately (see [[async-retry-backoff]]), not the pool's job.
- The whole database goes down: every connection attempt/query fails; the pool doesn't hide this or pretend to have a connection — every acquire raises whatever `asyncpg` connection error results. No fallback/circuit-breaker behavior is built in here — a real production pool client would usually pair this with the retry-with-backoff pattern from [[async-retry-backoff]] at the call site.
- `close()` doesn't wait for in-flight checked-out connections to finish and be returned before returning itself — a genuinely incomplete "graceful" shutdown (see trade-offs). A caller still using a connection when `close()` runs will find its connection *not* forcibly closed out from under it (only idle ones are closed), but that connection is also never returned to any pool afterward — a real, named gap.

**What trade-offs did you consider?**
- Semaphore + Queue (chosen) vs. a plain list with a lock and manual counting: the semaphore already correctly implements "block until a slot is free, with FIFO fairness" — reimplementing that with a lock and a condition variable would just be recreating what `asyncio.Semaphore` already does correctly, with more room for a subtle bug.
- Lazy, acquire-time liveness checking (chosen) vs. a background task periodically pinging idle connections: the background-poller approach catches dead connections sooner (before anyone even asks for one) but adds a permanently-running task, its own failure modes, and load on the database from the pings themselves. Lazy checking is simpler and only pays the cost exactly when a connection is about to be used.
- `close()` that doesn't drain checked-out connections (chosen, but flagged) vs. a full graceful shutdown that waits for all outstanding `acquire()` context managers to exit before closing: the simpler version was implemented and the gap called out explicitly, rather than either building the more complex draining logic without being asked or silently pretending the simple version is complete.
- No idle-connection max-age/recycling: real pools (and `asyncpg.create_pool` itself) often recycle connections after N queries or T seconds of idle time to avoid subtle server-side state drift (e.g. session-level settings a previous user of the connection changed). Left out to keep scope to the core acquire/release/health/shutdown mechanics asked for.

**How do you debug it?**
- `pool.stats()` (`live_connections`, `idle`, `checked_out`) is the primary window into pool health — if `idle + checked_out` ever doesn't match expectations relative to `max_size`, that's the signal the semaphore/queue bookkeeping has a bug, versus if `checked_out` stays high long after queries should have finished, that points to a `release()` that isn't being reached (e.g. a code path that returns/raises without going through the context manager correctly).
- `SELECT pg_backend_pid()` from inside a checked-out connection, cross-referenced against Postgres's own `pg_stat_activity`, is how the self-healing scenario was verified concretely — it's the ground truth for "is this literally the same server-side connection or a new one," not just trusting the client library's internal state.
- To reproduce pool exhaustion deterministically for testing, `pg_sleep(n)` inside a query is a clean way to hold a connection checked out for a controlled duration without depending on real query complexity or data size.
- To reproduce a dead-idle-connection scenario deterministically, `pg_terminate_backend(pid)` from a separate admin connection is exact and repeatable — far more reliable for testing than trying to trigger a real network blip.

**How do you evaluate it?**
- Bounded concurrency: 6 queries at 0.3s each through a `max_size=3` pool consistently completed in ~0.63s across 3 repeated runs — matching the expected ~2 batches, not 6× (sequential) or 1× (unbounded parallel).
- Exhaustion handling: 4 concurrent 1s queries against `max_size=2` with a 0.5s `acquire_timeout` consistently resulted in exactly 2 successes and 2 `PoolTimeoutError`s.
- Self-healing: a connection killed server-side was confirmed replaced by a connection with a *different* Postgres backend PID on the very next acquire — not just "no exception was raised," but the actual underlying connection identity changing.
- Clean shutdown: `stats()` showed 0 live/idle connections immediately after `close()`, and a subsequent `acquire()` raised `PoolClosedError` immediately rather than hanging.
- Not evaluated here: behavior under `close()` while connections are still checked out (the named incomplete-shutdown gap), and behavior under real network-level partial failures (only a clean server-side kill was tested, not a hung/half-open TCP connection, which is a harder and less deterministic failure mode to reproduce in a demo).

## Key Learnings

- A pool's correctness rests entirely on one invariant (live connections never exceed max_size) being true under concurrency — reaching for `asyncio.Semaphore` instead of hand-rolling a counter+lock meant that invariant came from a well-tested primitive instead of from code that had to be manually reasoned about for every interleaving.
- "Return the permit, not necessarily the connection" is the key insight that makes self-healing simple: capacity (the permit) and the specific object occupying that capacity (the connection) are separate concerns, and conflating them (e.g. tying capacity directly to a fixed list of connection objects) would have made replacing a dead one far more awkward.
- This is the same recurring theme as the whole week: rate limiting, the distributed lock, retry logic, and now connection pooling are all variations of "bound and safely share access to a limited resource under concurrency" — the specific resource changes (a quota, a mutex, an operation's attempts, a database connection) but the shape of the correct solution (atomicity where it matters, explicit failure modes, provable invariants) keeps repeating.
