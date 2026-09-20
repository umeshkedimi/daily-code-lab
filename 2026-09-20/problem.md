# Problem

**Category:** `dsa` / `python`

## Statement

Implement an **LRU (least-recently-used) cache** with **O(1) `get` and `put`**, extended with **per-key TTL expiry**, and safe to use from multiple threads. Build it from primitive data structures — not `OrderedDict` or `functools.lru_cache`.

**Requirements:**
- `get(key)` returns the value and marks the entry most-recently-used; a miss returns a default.
- `put(key, value, ttl=None)` inserts or updates; when over capacity, the least-recently-used entry is evicted.
- Both operations are O(1).
- Entries can expire: a per-key `ttl` (and an optional cache-wide default) after which the entry behaves as absent.
- Correct under concurrent access from many threads.

## Constraints

- O(1) must be true, and demonstrated, not just claimed — cost per operation should stay flat as capacity grows.
- `None` must be a storable value, distinguishable from a miss.
- Expiry must be testable without sleeping (injectable clock), and must use a monotonic clock by default so wall-clock adjustments can't resurrect or prematurely kill entries.
- A cache-wide invariant must hold after every operation: the hash map and the recency list describe exactly the same set of entries, and size never exceeds capacity.

## Approach

1. **Two structures, each covering the other's weakness.** A hash map gives O(1) lookup but no ordering; a doubly linked list gives O(1) reorder/remove *given a node reference* but no lookup. Store the node itself as the map's value: lookup finds the node in O(1), and unlinking it from the list is O(1) pointer surgery because a doubly linked node knows both its neighbours. Most-recent entries sit at the head; the eviction victim is always the tail's predecessor.

2. **Sentinel head and tail nodes** so insert/unlink never special-case an empty list or the ends — removing a whole class of `None`-check bugs in the exact code where pointer bugs hide.

3. **TTL as lazy expiry, to keep the O(1) guarantee.** Each node carries an `expires_at`; an entry is treated as absent (and removed) when read after that time, or when it reaches the LRU end and is evicted. Eagerly expiring entries would need a second structure ordered by expiry (a heap: O(log n) per put), which would break the strict O(1) headline. The cost of laziness — an expired entry can occupy a slot until touched — is named explicitly and mitigated with an O(n) `purge_expired()` escape hatch. Expiry is exclusive at the boundary (`now >= expires_at` is expired).

4. **Thread safety with one coarse lock.** Every public operation is a short critical section (a handful of pointer writes), so a single `threading.Lock` is both correct and cheap; finer-grained locking on a linked list is far harder to get right and buys nothing here because every operation touches the shared head anyway.

5. **Verification designed to catch the bugs this structure actually has.**
   - A **differential test** against an independent O(n) list-based oracle over thousands of random operation sequences (put/get/delete/contains/purge/clock-advance), comparing not just return values but the full recency order after *every* step.
   - A **structural invariant checker** (map ↔ list agreement, link symmetry, capacity bound) that is itself safe to run on a corrupted structure.
   - A **thread hammer test with a negative control**: the same workload, with the lock removed, must corrupt the cache — otherwise a passing concurrency test proves nothing.
   - A **benchmark** showing per-op cost is flat with capacity for the linked-list cache and linear for the naive baseline, reported alongside `OrderedDict` (the honest reference: C-implemented, faster by a constant factor).
