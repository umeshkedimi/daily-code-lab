## Implementation

- `_Node` (`__slots__`: key, value, expires_at, prev, next) is the unit of storage. The map holds `key -> node`, so a lookup lands directly on the object that the list can unlink in O(1). Storing the *value* in the map and searching the list for the key would silently turn every operation into O(n).
- Sentinel `_head`/`_tail` nodes bracket the list. `_unlink` and `_push_front` are four pointer writes each with no `if node.prev is None` branches; the eviction victim is always `_tail.prev`.
- `get`: map lookup → if expired, remove and count a miss + expiration → otherwise unlink, push to front, count a hit. Because a miss is signalled by the caller-supplied `default` and hits return the stored value untouched, `None` is a legal stored value; callers who need to tell "stored None" from "absent" pass their own sentinel as `default` (`test_none_is_a_storable_value_distinct_from_a_miss`).
- `put`: computes `expires_at` *before* taking the lock or touching any state, so an invalid `ttl` raises with the cache unchanged (`test_invalid_ttl_on_update_leaves_existing_entry_untouched`). Updating an existing key rewrites value and expiry and refreshes recency without changing size, so it can never trigger an eviction. Inserting a new key pushes it to the front and evicts `_tail.prev` if size now exceeds capacity.
- Every `put` replaces the expiry outright (explicit `ttl`, else `default_ttl`, else none). So `put("k", v)` on a key that had a TTL *clears* it — a deliberate, tested choice, because "keep the old TTL" would make it impossible to ever remove an expiry.
- An expired entry that is the eviction victim is counted as an *expiration*, not an *eviction* — a live entry pushed out by capacity pressure is the signal you tune capacity on; an entry that was already dead is not.
- `__contains__` is deliberately read-only (no recency refresh, no removal): a membership check that changed eviction order would be a surprising side effect (`test_contains_is_read_only_and_does_not_refresh_recency`).
- The clock is injected and defaults to `time.monotonic`, so TTL behavior is tested by assigning `clock.now` — no `sleep`, no flakiness — and wall-clock changes (NTP steps, DST) can't extend or kill entries.
- `check_invariants()` verifies map ↔ list agreement, symmetric prev/next links, the tail link, and the capacity bound. Its list walk is **bounded** (see Key Learnings for why).
- Not handled (by design): eager expiry, per-key locking / single-flight loading, an async variant, size-in-bytes accounting, and serialization.

## Complexity

- `get`, `put`, `delete`, `__contains__`: **O(1)** time — one dict operation plus a constant number of pointer writes.
- `purge_expired`, `keys`, `check_invariants`: O(n).
- Space: O(capacity) — one node plus one dict slot per entry; `__slots__` trims per-node overhead.

Measured (mixed get/put, ~50% hit rate, ns/op — see benchmark in `demo()`):

| capacity | LRUCache (this) | `OrderedDict` LRU | naive list |
|---:|---:|---:|---:|
| 1,000 | ~680 | ~193 | ~27,600 |
| 10,000 | ~730 | ~209 | ~300,000 |
| 100,000 | ~760 | ~250 | ~3,000,000 |

Ours is flat (a ~12% drift from cache misses at large sizes); the naive list grows ~10x per 10x capacity, i.e. linear. `OrderedDict` is also O(1) and is ~3.5x *faster* than this implementation because its linked list is in C — in production Python you'd use it (or `functools.lru_cache`); the point of writing the pointer version is understanding what those do.

## Follow-up Questions

**Why?**
Caches sit under almost everything (DB result caches, session stores, memoization, the Redis-style caches from earlier days). A bounded cache needs an eviction policy, and LRU is the standard because recency is a good, cheap proxy for future use. As an interview problem it tests whether you can combine two data structures to get an operation that neither can do alone.

**How exactly?**
Map for lookup, doubly linked list for recency. `get` = lookup + move-to-front. `put` = lookup-or-insert + move-to-front + evict tail if over capacity. TTL adds an `expires_at` per node checked lazily on access.

**Which algorithm?**
LRU eviction over a hash map + doubly linked list (the classic O(1) construction). TTL is lazy expiry; the alternatives are eager expiry via a min-heap on `expires_at` (O(log n) per put, precise), or timing wheels (O(1) amortized, complex) — both rejected to keep the strict O(1) property in the headline.

**Which library?**
None — pure standard library (`threading`, `time`). `OrderedDict` and `functools.lru_cache` are the production choices and are used only as the *benchmark baseline* here.

**What happens internally?**
Every operation is one dict probe (hash + compare) and a few attribute writes on nodes. Under CPython, `dict` ops on a single key are individually atomic thanks to the GIL, but a get is a *sequence* (probe, unlink, relink) that the GIL does not make atomic — a thread switch between the unlink and the relink leaves the list mid-surgery. That is exactly why the lock is required even in CPython, not just in free-threaded runtimes.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- Concurrent use without the lock: corrupts the structure. Verified rather than asserted — see below.
- Expired entries are not proactively removed: they hold slots until read, evicted, or `purge_expired()`. Under a write-heavy workload with short TTLs and no reads, `len(cache)` can sit at capacity while most entries are dead, and live entries can be evicted before dead ones. Mitigation: periodic `purge_expired()` (O(n)) or, if that matters, move to a heap.
- Value objects are stored by reference: mutating a cached mutable object mutates the "cached" copy. The cache does not copy.
- A cache stampede (many threads miss the same key and all recompute) is not addressed; that needs single-flight/per-key locking.

**What trade-offs did you consider?**
- *Lazy vs eager expiry:* lazy keeps get/put strictly O(1) at the cost of stale-slot occupancy; eager (min-heap) is precise at O(log n) per put plus an extra structure to keep consistent with the list.
- *One coarse lock vs finer locking:* every operation touches the shared head, so lock striping doesn't help a single LRU list; a coarse lock is simplest and its critical sections are a handful of pointer writes. Under heavy contention it serializes callers; the real answer at scale is sharding into N independent caches by key hash.
- *Hand-rolled vs `OrderedDict`:* the hand-rolled version is ~3.5x slower but exposes and lets you test every pointer invariant; `OrderedDict` is the right production tool.
- *Monotonic vs wall clock:* monotonic can't go backwards, but isn't comparable across processes or restarts — fine for an in-process cache, wrong for anything persisted.
- *`__contains__` read-only:* avoids a surprising side effect, at the cost of `in` followed by `get` doing two lookups.

**How do you debug it?**
- `check_invariants()` after suspicious sequences: it names the broken invariant (link asymmetry, map/list size mismatch, over capacity).
- `keys()` shows exact recency order; `stats()` shows hits/misses/evictions/expirations, which is how you notice an unexpectedly low hit rate or a capacity that's too small.
- The differential test prints the step and operation where the cache first diverges from the oracle — a minimal reproduction by construction.
- For races: shrink `sys.setswitchinterval` (used in the tests) to make thread switches ~1000x more frequent, turning a once-a-week production heisenbug into a failure within a second.

**How do you evaluate it?**
- **Correctness:** 53 tests — unit tests for each behavior and edge case (capacity 1, `None` values, TTL boundary exactly at `expires_at`, expired-victim accounting, invalid TTL leaving state untouched), plus a differential test: 8 seeds × 4 configurations × 1,500 random operations against the independent O(n) oracle, asserting equal return values *and equal full recency order after every single step*.
- **Complexity:** the benchmark above — flat per-op cost for this implementation vs linear growth for the naive baseline.
- **Thread safety:** an 8-thread hammer test with a tiny switch interval, **plus a negative control** — the identical workload against a cache whose lock is replaced by a no-op must corrupt it. In a scratch experiment: 10/10 trials clean with the lock, 0/10 without. Without the control, a green concurrency test only shows that races *didn't happen to occur*.
- **Test-suite stability:** after the fixes described below, the two concurrency tests ran 200 consecutive times with 0 failures (they had been failing ~3% of runs).

## Key Learnings

- **Diagnostics must survive the corruption they detect.** The first negative control hung the test run: an unlocked race left a cycle in the linked list, and `check_invariants` walked it with an unbounded `while`, so the tool built to report corruption spun forever on it. Bounding the walk by `len(map) + 1` turns "hang" into a clear assertion failure. Any checker that traverses a structure needs a step limit.
- **Diagnose the flake before "fixing" it.** The control then failed ~3% of runs. My first hypothesis — the unlocked cache occasionally survived all trials — was wrong: raising the trial budget and workload did not reduce failures (4/120). Capturing the actual failure showed the cache *was* corrupted every time, but 8 worker threads crashing simultaneously made pytest's own thread-exception hook race on `linecache` while formatting tracebacks and error out. The fix was to have workers catch and return their exceptions instead of letting them escape. That change also strengthened the locked-cache test, where a crashed worker previously produced only a warning, not a failure.
- **A negative control is what makes a concurrency test mean anything.** Passing under a lock proves little unless the same test demonstrably fails without one.
- **Benchmark setup can dwarf what you're measuring.** Pre-filling the naive baseline via `put` was itself O(n²) and pushed the demo past two minutes; loading the baseline directly restored a 2.6s demo while still timing only steady-state operations.
- **Asymptotics and constants are separate claims.** This is provably O(1) and ~3.5x slower than the C-backed `OrderedDict`. Both facts belong in the write-up.
