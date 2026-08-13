## Implementation

- `RateLimiter.__init__` takes an existing `redis.Redis` client (dependency injected, not created internally) plus `max_requests` and `window_seconds`, and registers the Lua script once via `redis.register_script()`. `register_script` returns a callable `Script` object that redis-py handles as `EVALSHA` with automatic fallback to a full `EVAL` if the script isn't cached on that particular Redis node (`NOSCRIPT` error) — so the script text is only sent over the wire once in the common case.
- `is_allowed(user_id)` builds the per-user key `ratelimit:<user_id>`, generates a fresh `uuid4().hex` as the ZSET member for this request (needed because ZADD treats a duplicate member as a score *update*, not a new entry — a plain timestamp as the member would silently collapse two concurrent requests landing in the same millisecond into one entry), and invokes the script.
- The Lua script does four things atomically: (1) reads Redis's own time via `TIME`, (2) `ZREMRANGEBYSCORE` to drop entries older than the window, (3) `ZCARD` to count what's left, (4) if under the limit, `ZADD` the new request and refresh `PEXPIRE` on the key so it self-cleans if the user goes idle.
- `PEXPIRE` on every accepted request (not just on key creation) is deliberate: it keeps the TTL rolling forward so the key never expires out from under an active user, while still guaranteeing cleanup (no orphaned keys) once a user goes quiet for longer than the window.
- Edge case handled: a user who never sends a request 6+ never creates a key at all — no pre-provisioning needed, first request lazily creates the ZSET.
- Edge case handled: `_make_client()` reads `REDIS_HOST`/`REDIS_PORT` from the environment so the same code targets a local dev Redis, a test container, or a production endpoint without code changes.
- Not handled (explicitly out of scope): Redis being unavailable. See **What if this fails?** below — the current code lets a connection error propagate rather than choosing a fail-open/fail-closed policy, because that's a product decision, not an implementation detail.

## Complexity

- Time: each `is_allowed()` call is one round trip to Redis running `TIME` (O(1)), `ZREMRANGEBYSCORE` (O(log N + M) where N is set size and M is the number of expired members removed), `ZCARD` (O(1)), and `ZADD` (O(log N)). Since N is capped at `max_requests` (5) by construction, every operation here is effectively O(1) in practice.
- Space: O(max_requests) per active user (5 ZSET entries max), auto-expiring via `PEXPIRE` — no unbounded growth, no cleanup job needed.

## Follow-up Questions

**Why?**
Rate limiting is the standard defense against a single client (buggy, malicious, or just chatty) monopolizing shared backend capacity. The "distributed" part is the actual point of the exercise: rate limiting is trivial with a local in-memory counter, and trivially *wrong* the moment you run more than one instance of your service — this is the exact bug class that ships when someone bolts a naive limiter onto a horizontally-scaled service.

**How exactly?**
Every request calls `RateLimiter.is_allowed(user_id)`. That runs one atomic Redis Lua script that trims expired entries out of the user's sorted set, counts what remains, and — only if under 5 — adds this request's timestamp and returns 1 (allowed) or 0 (rejected). The caller (a web framework middleware, in a real service) turns that boolean into a `200`-continues or a `429 Too Many Requests`.

**Which algorithm?**
Sliding-window log, via a per-user Redis sorted set keyed by request timestamp. Chosen over fixed-window counters (allows boundary bursts up to 2x limit) and over sliding-window *counters* (an approximation that weights the previous fixed window — cheaper but not exact). At `max_requests=5`, the log's per-user memory cost is negligible, so there's no reason to trade accuracy for the counter approximation here.

**Which library?**
`redis` (redis-py), the standard Python Redis client. Used its `register_script`/`Script` mechanism specifically because it's the documented, correct way to run Lua atomically from Python — hand-rolling `EVAL` calls would mean manually managing the `EVALSHA`/`NOSCRIPT`-fallback dance that `register_script` already does.

**What happens internally?**
Redis is single-threaded for command execution, and a Lua script submitted via `EVAL`/`EVALSHA` runs to completion as one unit before Redis processes any other client's command — no other command can interleave between this script's `ZCARD` and its `ZADD`. That's what turns "check count, then add" from a race condition into an atomic decision. `ZREMRANGEBYSCORE`/`ZCARD`/`ZADD` on a sorted set are skip-list operations under the hood, hence the O(log N) terms.

**How is it implemented?**
See [Implementation](#implementation) above.

**What if this fails?**
- Redis unreachable/down → the `redis` client raises (`ConnectionError`/`TimeoutError`) out of `is_allowed()`, uncaught in this implementation. In production this is a real design decision, not an oversight: **fail-open** (let the request through if Redis is down) prioritizes availability over strict enforcement; **fail-closed** (reject everything) prioritizes protecting backend capacity over availability. This exercise deliberately leaves it unhandled so the choice stays visible rather than silently defaulting to one.
- Lua script eviction/`NOSCRIPT` on a Redis restart or failover → handled transparently by redis-py's `Script` wrapper, which retries as a full `EVAL`.
- Clock skew between app instances → sidestepped entirely by sourcing time from Redis's `TIME` command inside the script rather than trusting each caller's local clock.

**What trade-offs did you consider?**
- Sliding-window log (chosen) vs. fixed-window counter: log is exact and prevents boundary bursts; costs O(limit) memory per user instead of O(1). At a limit of 5 this is a non-issue, so exactness won.
- Sliding-window log vs. token bucket: token bucket naturally supports bursting up to a bucket size with a steady refill rate, which is a different product behavior (smooths traffic vs. hard-caps a window). The problem explicitly asked for "5 per minute," which maps directly onto a window, not a refill rate — log was the more literal fit.
- Lua script (chosen) vs. `MULTI`/`EXEC` transaction: a Redis transaction batches commands atomically but can't branch on a value read *within* the same transaction (no "count, then decide" inside `MULTI`). Lua can read `ZCARD` and conditionally skip the `ZADD` in one atomic unit — that conditional branch is exactly what this problem needs and transactions can't express.
- Fail-open vs. fail-closed on Redis failure: left as an open, explicit trade-off (see above) rather than picked, since it's a business decision about risk tolerance, not a technical one.

**How do you debug it?**
- `redis-cli ZRANGE ratelimit:<user_id> 0 -1 WITHSCORES` shows exactly what the limiter currently believes about a user — every tracked request and its timestamp — which immediately tells you if entries are being trimmed correctly.
- `redis-cli TTL ratelimit:<user_id>` confirms the rolling expiry is behaving (should always be ≤ window_seconds).
- To debug a suspected race, the concurrency demo in `solution.py` (30–50 threads hitting one user simultaneously) is the reproduction harness — if atomicity ever broke (e.g. someone "optimized" this into separate `ZCARD` + `ZADD` calls from Python instead of one Lua script), this demo would immediately show `allowed > 5`.
- `redis-cli MONITOR` while running the demo shows the actual command stream hitting Redis, useful for confirming the script really is one round trip per `is_allowed()` call rather than several.

**How do you evaluate it?**
- Correctness under no contention: sequential requests — first 5 allowed, 6th+ rejected. Verified in `demo()` step 1.
- Correctness under contention: many threads/connections hitting the same user concurrently — total allowed must equal the limit exactly, not more. Verified in `demo()` step 2, and re-run 5x with 50 threads with zero deviation (always exactly 5).
- Per-user isolation: verified in `demo()` step 3 — a fresh user's first request is unaffected by another user's exhausted quota.
- Time-based recovery: verified in `demo()` step 4 with a short window — requests rejected mid-window are allowed again once the window elapses.
- What's *not* covered here: failure-mode behavior (Redis down) and load/latency characteristics under realistic traffic — both called out above as open, deliberately unimplemented trade-offs rather than gaps I missed.

## Key Learnings

- "Check-then-act" is the recurring shape of every rate-limiter bug: read a count, decide based on it, then write — and the gap between read and write is exactly where concurrent requests race. Recognizing that shape is more valuable than memorizing this specific fix.
- Redis being single-threaded for command/script execution is what makes it such a convenient place to put shared, atomically-updated state — it turns "atomicity" from a distributed-systems problem into "write it as one Lua script."
- Distributing a service doesn't just mean "put the counter somewhere shared" — it also breaks assumptions like "the caller's clock is trustworthy," which is why sourcing time from Redis itself (not from each app instance) mattered here.
