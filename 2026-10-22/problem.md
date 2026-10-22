# Problem

**Category:** `dsa`

## Statement

Implement an **LFU (Least-Frequently-Used) cache** with O(1) `get`/`put` — a genuinely different eviction policy from the LRU cache built on 2026-09-20, not a rename. LRU needs one structure (a recency-ordered list); O(1) LFU needs two, layered: a frequency → bucket-of-keys map, plus a `min_freq` pointer, so finding the eviction victim never requires scanning anything.

"LFU" alone doesn't specify tie-breaking: when several keys share the current minimum frequency, which one gets evicted? This implementation picks the standard answer (least-recently-used among the tied keys) and tests it directly, not by accident of implementation.

**Requirements:**
- `get`/`put` must each be O(1), not O(log n) and not O(n) — verified by direct timing comparison against an O(n)-per-eviction naive reference, at growing cache sizes, not just claimed.
- Both a `get` and a `put` on an existing key count as a "use" and increment that key's frequency.
- Tie-breaking (least-recently-used among equal frequencies) must be demonstrated with a scenario that actually produces a tie — not a scenario where one key just happens to have strictly the lowest frequency, which would pass even with no tie-breaking logic at all.
- The real-world weakness of pure (non-aging) LFU — a key that was popular long ago and never touched again keeps a frequency count nothing in a later, genuinely-current workload can catch up to without matching that count — must be **measured** against an LRU cache on a workload built to trigger it, with the actual regime where the effect holds (and where it doesn't) characterized, not assumed to hold everywhere.

## Constraints

- Standard library only (`collections.OrderedDict`).
- Each day in this repo is self-contained — the LRU cache used for the staleness-pollution comparison is a small, fresh implementation built in this file, not an import from 2026-09-20.
- Differential-tested against an independently-structured O(n) reference (`NaiveLFU`) across randomized operation sequences, checking not just returned values but internal per-key frequency state at every step — agreeing on output while disagreeing on frequency would mean a future eviction decision is silently wrong even though nothing has shown it yet.

## Approach

1. **Three parallel structures kept in sync on every operation**: `_key_to_value` (the data), `_key_to_freq` (access counts), and `_freq_to_keys` (frequency → an `OrderedDict` of keys at that frequency, oldest-at-this-frequency first). The `OrderedDict` per bucket is what makes tie-breaking O(1): the oldest entry at the minimum frequency is always `next(iter(bucket))`/`popitem(last=False)`, never a scan.
2. **`_bump`, the one piece of logic every mutating operation shares**: move a key from its current frequency bucket to the next one up, appending it there. If removing it empties the old bucket *and* that bucket was the current minimum, the minimum frequency increments — this is the only place `min_freq` ever needs to move up, and it never needs to move down except on a fresh insert (handled separately, by resetting it to 1).
3. **`NaiveLFU`**: a plain dict plus a separate frequency counter and a monotonic "last used" tick, scanning every stored key on eviction via `min(store, key=lambda k: (freq[k], last_used[k]))` — obviously correct by construction, with no bucket bookkeeping to get wrong, used to differential-test the real implementation and to measure the O(1) vs O(n) gap directly.
4. **`SimpleLRU`**: a minimal `OrderedDict`-based LRU, built fresh in this file purely to drive the staleness-pollution comparison — not itself the subject of this exercise (that's 2026-09-20).
5. **The staleness-pollution measurement, characterized across three regimes, not asserted from one favorable number**: a workload with one key hammered early and never touched again, followed by a cycling "currently hot" working set — measured at a working-set size below, at, and above the cache capacity, because (as the measurement shows) the dramatic LFU-loses-to-LRU gap only appears in a narrow window, not generally.
