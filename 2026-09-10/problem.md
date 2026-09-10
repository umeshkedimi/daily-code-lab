# Problem

**Category:** `backend` / `system-design`

## Statement

Build an **async Postgres connection pool** from scratch (on top of `asyncpg`'s raw connection API, not its built-in `create_pool`) that:

- Bounds the number of live database connections to a configurable `max_size`, regardless of how many coroutines want one concurrently.
- Reuses idle connections instead of opening a new one per query.
- Blocks a caller waiting for a connection up to a timeout, then fails clearly (not hangs forever) if the pool stays exhausted.
- Detects a connection that died while idle (network blip, admin kill, server restart) and replaces it, rather than handing out a broken connection.
- Shuts down cleanly: closes all connections, and rejects further use afterward instead of hanging or silently misbehaving.

## Constraints

- At any instant, `idle connections + checked-out connections <= max_size` — the pool must never open more real database connections than configured, even under a concurrent stampede of callers.
- `min_size` connections are pre-warmed so the first few callers don't pay cold-connection latency.
- A caller must always either get a working connection or a clear, typed exception (`PoolTimeoutError` on exhaustion, `PoolClosedError` after shutdown) — never an indefinite hang, and never a connection that's silently dead.

## Approach

1. **A counting semaphore is the actual pooling primitive, not a manual counter with a lock.** `asyncio.Semaphore(max_size)` gives exactly the guarantee needed: at most `max_size` "permits" can be held at once, `acquire()` on it blocks (or, wrapped in `asyncio.wait_for`, times out) when none are free, and it's already correct under concurrent callers — no hand-rolled check-then-increment race to get wrong.

2. **The semaphore gates the *decision* to reuse-or-create, not the connection objects themselves.** Every `acquire()` call must win a permit before it's allowed to either pop an idle connection or open a new one. This is what keeps the invariant (idle + checked-out ≤ max_size) true by construction: a connection is only ever created while holding a permit, and idle connections just sit there, unclaimed, until some acquire() call's permit lets it claim one.

3. **A connection that fails is dropped, not returned — but its permit still goes back.** On release, if the connection turned out to be dead (`conn.is_closed()`), it's closed and discarded rather than requeued; the semaphore permit is released regardless, so pool capacity isn't permanently lost to one bad connection — the next caller to win that permit will simply create a fresh one.

4. **Liveness is checked lazily, on acquire, not via a background poller.** An idle connection can die while sitting unused (the admin kills its backend, the network blips). Rather than running a periodic health-check loop, the pool checks `conn.is_closed()` right before handing a connection out — cheap, and it means a dead idle connection is caught before a caller ever tries to use it, not discovered mid-query.

5. **`asyncio.wait_for` around the semaphore acquire turns "pool exhausted" into a bounded wait with a typed failure**, not an unbounded block — a caller under load gets `PoolTimeoutError` within `acquire_timeout` seconds and can decide what to do (retry, degrade, surface an error) instead of hanging.

6. **Proved every property against a real Postgres instance**, not a mock: bounded concurrency (6 queries through a `max_size=3` pool take ~2 batches' worth of time, not 6, not 1), exhaustion under contention (2 of 4 concurrent long queries against `max_size=2` are correctly rejected on timeout), self-healing (a connection killed server-side via `pg_terminate_backend` is detected and replaced on next use, verified by its Postgres backend PID changing), and clean shutdown (post-close `acquire()` fails immediately with no hang).
