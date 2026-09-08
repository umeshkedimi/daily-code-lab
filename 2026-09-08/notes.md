## Implementation

- `RedisLock.__init__` takes an injected `redis.Redis` client, a `resource` name (mapped to key `lock:<resource>`), and a `ttl_ms` — no lock is created until `acquire()` is called.
- `acquire()` generates a fresh `uuid4().hex` token per call and loops on `redis.set(key, token, nx=True, px=ttl_ms)`. `nx=True` maps to Redis's `NX` flag (set only if absent) and `px=ttl_ms` sets the expiry atomically in the same command — there's no separate `EXPIRE` call, which matters: if `SET` and `EXPIRE` were two commands, a crash between them would leave a lock with no expiry at all, permanently deadlocked.
- Blocking acquisition is a simple poll loop (`time.sleep(retry_delay)` between attempts) bounded by a `timeout` deadline — not the most efficient option under heavy contention (see trade-offs), but simple and correct.
- `release()` refuses to act if `self.token is None` (never acquired, or already released) and otherwise runs the Lua script with `KEYS=[key]`, `ARGV=[token]`. The script does `GET` and conditionally `DEL` as one atomic unit — the same "read-then-write must be atomic" principle used for the rate limiter's check-and-increment ([[redis-rate-limiter]] if this repo cross-references by day), just applied to check-and-delete instead.
- `__enter__`/`__exit__` wrap `acquire()`/`release()` so `with RedisLock(r, "resource"):` is the intended default usage — release always runs (even on an exception inside the `with` block, since `__exit__` returns `False` rather than suppressing it) and a failed acquire raises immediately instead of silently proceeding without the lock.
- Edge case handled (demo scenario 4): a holder whose lock already expired, and who is unaware of that, calling `release()` — the Lua script's token comparison correctly returns `0` (no-op) instead of deleting whatever new holder's lock now occupies that key.
- Edge case handled (demo scenario 3): a holder that never calls `release()` at all (simulating a crash) — the lock becomes acquirable again exactly once its TTL elapses, not never.
- Not handled (explicitly out of scope): automatic lock extension for a critical section that runs longer than the TTL (a "watchdog"/heartbeat thread that periodically renews the TTL while the holder is still alive and working) — see trade-offs below.

## Complexity

- Time: `acquire()` is O(1) per attempt (`SET NX PX` is a single Redis operation); under contention with polling, worst case is O(timeout / retry_delay) attempts. `release()` is O(1) — one Lua script doing a `GET` + conditional `DEL`.
- Space: O(1) per lock — a single Redis key regardless of how many callers are contending for it.

## Follow-up Questions

**Why?**
Some operations must not run twice at once even when the system is scaled horizontally — charging a payment method once, running a scheduled job exactly one time across a fleet of workers, serializing writes to a resource that isn't itself transactional. An in-process `threading.Lock` only coordinates threads within one process; it says nothing about a second instance of the same service running the same code at the same time. A distributed lock moves the coordination point to shared, external state everyone can see.

**How exactly?**
`acquire()` issues `SET lock:<resource> <token> NX PX <ttl>`. Redis either creates the key (caller now owns the lock) or the command is a no-op because the key already exists (someone else owns it) — one atomic decision, no ambiguity even with many simultaneous callers. `release()` runs a Lua script that only deletes the key if its current value still matches the token this specific `acquire()` call received.

**Which algorithm?**
This is the single-instance Redis lock pattern (the basis of, but simpler than, the multi-node Redlock algorithm) — correct as long as you're relying on one Redis deployment (or one Redis Cluster acting as one logical store) as the single source of truth, rather than trying to get quorum across multiple independent Redis nodes.

**Which library?**
`redis` (redis-py) for both the `SET NX PX` call and `register_script` for the atomic release — same library used for [[redis-rate-limiter]], since both problems reduce to "do a read-and-conditionally-write atomically in Redis," which is exactly what `register_script`'s Lua execution is for.

**What happens internally?**
Redis processes commands (and full Lua scripts) one at a time on a single thread, so there is no interleaving possible between two clients' `SET NX` calls — one arrives first in Redis's command queue and wins, full stop, regardless of network timing jitter between clients. The token comparison inside the release script executes as part of that same single-threaded guarantee, which is what removes the TOCTOU (time-of-check-to-time-of-use) gap that a `GET` followed by a separate `DEL` from the Python side would have.

**How is it implemented?**
See [Implementation](#implementation) above.

**What if this fails?**
- Redis becomes unreachable: `acquire()`/`release()` raise a `redis` connection error, uncaught here — same open fail-open-vs-fail-closed question as the rate limiter: does losing the coordination layer mean "let everything through" or "block everything"? Left visible rather than silently decided.
- The critical section legitimately runs longer than the TTL: the lock expires *while the holder is still working*, a second caller acquires it, and now two callers are inside the critical section simultaneously — the exact failure this lock exists to prevent. This is the one real weakness of TTL-based locking, and it's not solved by this implementation (no watchdog/heartbeat renewal) — see trade-offs.
- The whole Redis instance restarts and loses the key: the lock is gone, anyone can acquire — acceptable if Redis's own persistence/HA story is trusted; not acceptable if Redis itself is a single point of failure your system can't tolerate losing (a genuinely different, harder problem — Redlock's multi-node quorum approach exists specifically to address this, at real complexity cost).

**What trade-offs did you consider?**
- Polling (`time.sleep` between retries) vs. Redis Pub/Sub to get woken up the instant a lock is released: polling is simpler and correct, at the cost of latency (up to one `retry_delay` interval of wasted waiting) and a steady trickle of `SET NX` calls under contention. Chosen for simplicity, since the correctness of the lock doesn't depend on which waiting strategy is used.
- Fixed TTL, no watchdog renewal (chosen) vs. a background thread that extends the TTL every so often while the holder is still alive: the fixed-TTL version is simpler and has fewer moving parts, but forces a real choice between "TTL too short → risk losing the lock mid-work" and "TTL too long → slow crash recovery." A watchdog removes that tension but adds a live background thread and its own failure modes (what if the watchdog itself stalls?) — left out to keep scope honest about what was actually built versus what a hardened version would need.
- Single-Redis lock (chosen) vs. Redlock (quorum across multiple independent Redis nodes): Redlock exists to survive a single Redis node failing, at meaningfully higher implementation and operational complexity. Appropriate when Redis itself must not be a single point of failure; overkill (and its own source of subtle bugs) otherwise.

**How do you debug it?**
- `redis-cli GET lock:<resource>` shows exactly who (which token) currently holds a lock, if anyone — the first thing to check when a critical section seems to be running more than once, or not running at all.
- `redis-cli TTL lock:<resource>` confirms the expiry is set and counting down as expected — a lock with no TTL at all (`-1`) would indicate the `SET` call is missing its `PX`, a serious bug (permanent deadlock risk).
- To reproduce the lost-update bug that proves the lock matters, `demo()` scenario 1 (no lock) is the deliberate regression case — if scenario 2 (with the lock) ever also under-counts, that's the signal the lock's atomicity has broken.
- For the crash-recovery and safe-release paths (scenarios 3 and 4), the test forces the exact failure by construction (never calling `release()`; sleeping past a short TTL) rather than waiting for a real crash to happen — deterministic reproduction beats hoping to catch a race live.

**How do you evaluate it?**
- Mutual exclusion under real contention: the counter test (50 then 60 threads across two runs) — the racy version reliably lost updates (6/50 in one run), the locked version reliably landed on the exact expected count across every run.
- Crash recovery: a holder that never releases still allows a second holder in, but only after the TTL elapses (verified as `False` immediately, `True` after 1.1s against a 1s TTL).
- Safe release: a stale holder's `release()` call correctly reports `released=False` and the current, valid holder's lock is verified to be untouched afterward (`r.get(key) == fresh.token`).
- Not evaluated here: behavior when the critical section outlives the TTL (the flagged open weakness), and behavior under Redis unavailability — both are named gaps, not silent ones.

## Key Learnings

- The token (not just key presence) is what turns "a lock" into "a lock I can prove I own" — without it, release() has no way to distinguish "delete my own lock" from "delete whatever's currently there," which is unsafe the moment TTLs are involved.
- TTL-based locks trade a hard correctness guarantee (the critical section is exclusive, full stop) for a softer one (the critical section is exclusive as long as it finishes within the TTL) — that trade should be named explicitly when choosing this pattern for a real system, not discovered later as a production incident.
- The same shape kept reappearing this week: rate limiting, the release script here, and the async fetch's per-source isolation are all instances of "a read-decide-write sequence must be atomic or it isn't safe under concurrency" — recognizing that shape is more transferable than any one implementation.
