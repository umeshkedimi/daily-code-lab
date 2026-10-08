## Implementation

- All four limiters take `clock: Callable[[], float]` instead of calling `time.monotonic()`/`time.time()` internally — the same pattern as 2026-09-20's `LRUCache` TTL, chosen for the same reason: boundary behavior (exactly at a window edge, exactly when a token refills) needs to be tested at an exact instant, not approximated by sleeping and hoping the scheduler cooperates.
- `FixedWindowCounter`: `window_index = floor(now / window_seconds)` is the entire state machine — a single integer comparison tells you whether to reset. No memory of the previous window at all, which is both the whole implementation and the whole flaw.
- `SlidingWindowLog`: a `deque` of admitted timestamps, pruned from the front with `while log and log[0] <= cutoff: popleft()` before checking `len(log) < limit`. The `<=` (not `<`) means a request exactly `window_seconds` old no longer counts — the trailing window is `(now - window_seconds, now]`, left-exclusive.
- `NaiveSlidingWindowLog`: built differently on purpose, not just a copy with different variable names — logs every *attempt* (not just admitted ones) to an ever-growing list, and recomputes the in-window admitted count from scratch each call by filtering that whole list. O(total attempts ever) per call, versus the real implementation's O(1) amortized. Used only for differential testing, never for the timing/memory comparisons.
- `SlidingWindowCounter`: `_roll_windows` is the one piece of state transition logic worth being careful about — `previous_count` only carries over from `current_count` when the new window index is *exactly* one more than the last seen index (a genuinely consecutive rollover). A gap of more than one empty window correctly zeroes both counts, since there's no "previous window" with real data to discount from.
- `TokenBucket`: refill is computed lazily, inline in `allow()` — `elapsed * rate` tokens accrue since the last check, capped at `capacity`. No background thread, no scheduled tick; the bucket only "catches up" exactly when asked a question, which also means a bucket that's never queried costs nothing between queries.
- Not built: multi-key management (a dict of per-key limiter instances is the obvious extension, deliberately left out since key management is orthogonal to the algorithms themselves), a leaky-bucket variant (smooths output rate instead of just bounding burst — a close cousin of token bucket, mirror-image trade-off), and distributed coordination (already built, differently, as the Redis ZSET + Lua version on 2026-08-13 — this exercise is specifically about the single-process algorithmic trade-offs, not the "shared state across instances" problem).

## Complexity

| Algorithm | Time per call | Memory |
|---|---|---|
| Fixed window counter | O(1) | O(1) |
| Sliding window log | O(1) amortized (each timestamp pushed/popped at most once) | O(limit) |
| Sliding window counter | O(1) | O(1) |
| Token bucket | O(1) | O(1) |
| `NaiveSlidingWindowLog` (reference only) | O(total attempts ever) | O(total attempts ever) |

- **Measured: boundary burst**, `limit=100`, `window=10s`, 100 requests at `t=9.99` and 100 more at `t=10.01` (a 0.02s span):
  - `FixedWindowCounter`: **100 admitted before, 100 after — 200 total**, exactly the textbook up-to-2x flaw.
  - `SlidingWindowLog`: **100 before, 0 after — 100 total**, exactly `limit`, because the first 100 are still logged and still within the trailing 10s window when the second batch arrives.
  - `SlidingWindowCounter`: **100 before, 1 after — 101 total**, a 1% overcount at this specific (near-worst-case) boundary timing — a large reduction from the fixed window's 2x, not a full fix.
- **Measured: `SlidingWindowLog` memory under sustained above-limit traffic** — 200,000 requests, `limit=50`, packed far faster than the window drains: internal log length never exceeded **50** (`== limit`, confirming the O(limit) memory bound empirically, not just by reading the eviction logic).
- **Measured: `TokenBucket` burst vs. steady-state** — `capacity=20, rate=5/s`, after a long idle period: **20/30** requests admitted immediately (exactly `capacity`, not more), then throttled; separately, over a 2,000-second run offered far faster than the bucket can admit, the observed admitted rate converged to the configured `rate` within 5% across 10 random seeds and three rate values (1, 5, 10/s).

## Follow-up Questions

**Why?**
Every public API, SaaS product, and internal service-to-service boundary needs some admission control, and which algorithm is "correct" depends entirely on what you're actually trying to bound — burst tolerance, exact fairness, or memory cost — which is the thing a side-by-side comparison makes concrete instead of abstract.

**How exactly?**
Fixed window: reset-on-boundary counting, no memory between windows. Sliding log: exact accounting via a pruned timestamp deque. Sliding counter: a cheap two-window-blend approximation of the log. Token bucket: a different question entirely — bounded burst plus converged steady rate, via continuous lazy refill.

**Which algorithm?**
All four are standard, named techniques (fixed window, sliding window log, sliding window counter / "Cloudflare-style" approximation, token bucket) — nothing novel here; the exercise is building each correctly enough to measure the textbook trade-offs directly rather than taking them on faith.

**Which library?**
Standard library only — `collections.deque` for O(1) eviction from the front (same reasoning as 2026-09-30's monotonic-deque sliding-window-maximum problem: a `list` would make front-eviction O(n)), `math.floor` for window-index arithmetic.

**What happens internally?**
The sliding window counter's accuracy comes entirely from the linearity assumption baked into `elapsed_fraction` — it assumes the previous window's requests were spread uniformly across it, so "how much of that window has scrolled out of the trailing view" can be approximated by a single multiply. When traffic is actually spread roughly evenly (the common case), the approximation is tight; the 1% overcount measured above is close to the theoretical worst case, which happens when the previous window's requests are adversarially clustered right at its end (exactly what the boundary-burst test constructs).

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **A gap of more than one empty window** (no traffic for a while, then a request arrives): `SlidingWindowCounter._roll_windows` explicitly checks `window_index == self._window_index + 1` — anything else (including a multi-window gap) zeroes both counts rather than wrongly treating stale data as "the previous window." Verified (`test_sliding_counter_handles_a_gap_longer_than_one_window`).
- **A very long idle period before `TokenBucket.allow()` is ever called again:** `_refill` caps at `self.capacity`, so an elapsed time of 1000 seconds doesn't produce 1000+ seconds' worth of banked tokens — verified directly (`test_token_bucket_never_exceeds_capacity_even_after_a_long_idle_period`), and this is exactly the mutation (M5 below) that a missing `min()` would silently break.
- **Exactly `window_seconds` since an admitted sliding-log entry:** the eviction cutoff is left-exclusive (`<=`), so an entry exactly that old is evicted, not retained for one more call — verified directly (`test_sliding_log_admits_again_once_old_entries_expire`), and this exact boundary is what mutation M2 below flips.

**What trade-offs did you consider?**
- **Sliding window log (exact, O(limit) memory) vs. sliding window counter (approximate, O(1) memory):** the log is the right choice when `limit` is small relative to available memory and exactness matters (e.g., a hard contractual quota); the counter is the right choice at very high `limit`/high key cardinality (per-user limiting across millions of users) where O(limit) per key is real memory pressure and a small, bounded overcount is an acceptable price.
- **Token bucket vs. the three window-based algorithms:** window-based algorithms answer "how many requests in this time span"; token bucket answers "how bursty can traffic be, and what's the sustained ceiling" — these aren't interchangeable despite both being called "rate limiters." A latency-sensitive API that wants to allow a quick burst after idle (token bucket) has a different shape of problem than one that wants a hard cap on requests-per-minute regardless of burstiness (sliding log).
- **Lazy refill (chosen) vs. a background thread ticking tokens into the bucket on a timer:** lazy refill means zero cost for an idle bucket and no timer/thread lifecycle to manage, at the cost of recomputing elapsed time on every call — a fine trade for a single-process, synchronous rate limiter; a timer-driven version would matter more if other code needed to observe "current token count" without calling `allow()`.

**How do you debug it?**
- For a window-based limiter admitting too much right at a boundary, the first thing to check is always `window_index` vs. the previous call's window index at that exact timestamp — an off-by-one in the floor/boundary arithmetic (as in mutation M2 below) manifests exactly at edges, never in the middle of a window, so hand-picked boundary timestamps (not random ones) are the fastest way to localize it.
- For `SlidingWindowCounter`, printing `(previous_count, current_count, elapsed_fraction, estimate)` right before the admit/reject decision turns "why did this get rejected" into a one-line arithmetic check rather than a guess.
- Differential testing against `NaiveSlidingWindowLog` (simple, obviously correct, deliberately slow) is the actual tool of record for the real `SlidingWindowLog` — any disagreement points at the deque-pruning logic in the fast version, since the naive version has no clever logic to doubt.

**How do you evaluate it?**
- **51 tests, 5/5 consecutive clean runs** (fixed `pytest.mark.parametrize` seeds for the randomized differential and token-bucket-rate tests — reruns confirm environment stability, not randomness per se).
- **Mutation check, 6 targeted mutations, each applied and reverted in isolation with `__pycache__`/`.pytest_cache` cleared before every run** (continuing the practice adopted on 2026-09-30, after a stale-bytecode false negative there):
  - `FixedWindowCounter` never resets its count on a window change: 2/51 failed.
  - `SlidingWindowLog` eviction cutoff `<=` → `<` (off-by-one on exact-boundary expiry): 1/51 failed.
  - `SlidingWindowCounter` treats every window transition as consecutive (ignores multi-window gaps): 1/51 failed.
  - `SlidingWindowCounter` drops the `(1 - elapsed_fraction)` discount entirely (sums both windows' raw counts): **initially 0/51 failed — a real test-coverage gap, not a benign mutation** (see Key Learnings).
  - `TokenBucket` refill forgets the `min(capacity, ...)` cap: 1/51 failed.
  - `TokenBucket` off-by-one, `tokens >= 1` → `tokens > 1`: 8/51 failed.

## Key Learnings

- **A mutation that produces zero test failures needs to be checked by hand before being written off as "benign," because it might instead be a real coverage gap.** Dropping `SlidingWindowCounter`'s elapsed-fraction discount initially passed every test — not because the mutation was harmless (it makes the limiter uniformly *stricter*, never looser, so it can't fail an upper-bound assertion), but because no test exercised a consecutive window rollover with a non-trivial elapsed fraction and asserted a *lower* bound on admissions. Confirmed the gap directly (`correct-code admitted: 10` vs. `mutated-code admitted: 0` on the same scripted scenario) before writing `test_sliding_counter_discounts_previous_window_as_time_passes`, which now fails correctly on that mutation. The general lesson, consistent with 2026-09-30's monotonic-deque finding: "0 failures" has two causes — the mutation is genuinely behavior-preserving, or the test suite only checks one direction of the behavior — and only re-deriving the expected output by hand distinguishes them.
- **"Rate limiter" undersells how different these four algorithms' actual guarantees are.** Before measuring it directly, the fixed-window boundary flaw is easy to state ("up to 2x burst") but the sliding counter's partial fix is easy to overstate — it's a *measured* 1% overcount at this specific near-worst-case timing, not a guarantee of exactness, and that number only means something because it was checked against the exact log rather than assumed.
