# Problem

**Category:** `backend` / `system-design`

## Statement

Implement and compare four in-process rate-limiting algorithms sharing one interface (`allow() -> bool`, single key, time injected via a clock rather than read from the wall clock):

1. **Fixed window counter** — count requests in the current fixed-length window, reset at each boundary.
2. **Sliding window log** — log every admitted request's timestamp; admit iff fewer than `limit` logged timestamps are still within the trailing window.
3. **Sliding window counter** — an O(1)-memory approximation of (2): blend the current and previous fixed window's counts, weighted by how far into the current window "now" is.
4. **Token bucket** — a bucket of `capacity` tokens refilling continuously at `rate`/second; each request costs one token.

The point isn't "implement four things that each roughly cap request volume" — it's to make the actual difference between them concrete and measured: where each one's accounting breaks down (or doesn't), what each costs in memory, and that "rate limiter" is not one specification but a small family of related-but-different ones.

**Requirements:**
- All four must be testable without sleeping — a clock is injected (`Callable[[], float]`), never read from `time.time()` directly inside `allow()`.
- The classic fixed-window flaw — up to `2x limit` admitted in a short span straddling a window boundary — must be demonstrated numerically, not just asserted in prose.
- The sliding window log's memory must be shown to stay bounded by `limit`, not by total requests ever received, under sustained above-limit traffic.
- The sliding window counter's approximation error against the exact log must be measured, not assumed small.
- The token bucket's two distinct guarantees — bounded burst size, and converged long-run average rate — must each be checked separately, since neither implies the other on its own.

## Constraints

- Standard library only (`collections.deque`, `math.floor`).
- Single key per limiter instance — multi-tenant key management is a different (orthogonal) problem, not the point of this exercise.
- The sliding window log is cross-checked against an independently-built, deliberately-slower reference (`NaiveSlidingWindowLog`, O(total attempts) per call, filtering a never-pruned list) via randomized differential testing — not trusted on its own just because it reads as "obviously correct."

## Approach

1. **Fixed window counter**: `window_index = floor(now / window_seconds)`; track a single running count that resets whenever `window_index` changes. O(1) time and memory, and that simplicity is exactly the flaw — nothing connects one window's accounting to the next, so a full `limit` can land in the last instant of one window and another full `limit` in the first instant of the next, with no memory of the first batch to stop it.
2. **Sliding window log**: a deque of admitted timestamps, pruned from the front (oldest first) on every call before checking whether there's room for one more. This is the literal definition of "fewer than `limit` requests in the trailing `window_seconds`" — exact by construction, at the cost of O(limit) memory (bounded because a rejected request is never logged, and `limit` is the most that can ever be simultaneously in-window).
3. **Sliding window counter**: keep only `current_count` and `previous_count` (one fixed window each); estimate the trailing-window count as `previous_count * (1 - elapsed_fraction) + current_count`, where `elapsed_fraction` is how far "now" is into the current window. This assumes the previous window's requests were spread evenly across it — an approximation, which is why it's cross-checked against the exact log rather than assumed correct.
4. **Token bucket**: lazy refill — on each call, compute tokens accrued since the last check (`elapsed * rate`), cap at `capacity`, then admit iff at least one token is available. Lazy refill means no background timer/thread is needed; the bucket is only ever "caught up" exactly when something asks it a question.
5. **Comparison, not just four passing test files**: a boundary-burst scenario run identically against all three window-based algorithms, a sustained-load memory-growth check on the log, an explicit error-bound measurement for the counter against the log, and separate burst-size and long-run-rate checks for the token bucket.
