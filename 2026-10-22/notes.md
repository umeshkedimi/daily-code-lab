## Implementation

- `_bucket(freq)` creates a frequency's `OrderedDict` lazily, only when a key is actually about to be added to it -- deliberately not `collections.defaultdict`, whose auto-vivification-on-read would silently create an empty bucket (and a spurious entry in `_freq_to_keys`) from a bare existence check, which is exactly the kind of bug that's invisible until something later iterates `_freq_to_keys` and finds a phantom empty bucket sitting at a frequency nothing occupies.
- `_bump`: the single piece of logic every mutating path (`get` on a hit, `put` on an existing key) shares. Removing a key from its old bucket and, if that empties the bucket *and* it was the current `min_freq`, incrementing `min_freq` -- this is the only place the minimum ever moves *up*. It only ever moves back down to 1 in `put`, on a fresh insert, which is a separate and simpler fact (a brand-new key is always, trivially, tied for the new minimum).
- `put` on a full cache evicts via `bucket.popitem(last=False)` on `_freq_to_keys[self._min_freq]` -- O(1), no scan, because the bucket is already ordered oldest-to-newest at that frequency by construction (every `_bump` appends to the *end* of its new bucket, making "most recently touched at this frequency" and "end of the OrderedDict" the same fact).
- `NaiveLFU` tracks a separate monotonic `_tick` incremented on every access, used purely as the "how recently was this touched" tiebreaker in `min(store, key=lambda k: (freq[k], last_used[k]))` -- deliberately a different mechanism from the real implementation's ordered buckets, so a shared bug between the two would be a coincidence, not a given.
- `SimpleLRU` is a small, self-contained `OrderedDict`-based LRU built fresh in this file -- each day in this repo stands alone, so this isn't an import from 2026-09-20's (more fully-featured, TTL-supporting, thread-safe) `LRUCache`, just the minimum needed to drive the comparison below.
- Not built: any aging/decay mechanism for frequency counts (the usual real-world fix for the staleness-pollution weakness measured below -- e.g. periodically halving all frequencies, or weighting recency into the eviction score directly, as Redis's approximate LFU and ARC-style hybrids do) -- building the fix would be a different, follow-on exercise from measuring the problem it fixes.

## Measured Results

- **Complexity**: `LFUCache` stays flat (~450-550 ns/op) as capacity grows 500 → 2,000 → 8,000 (16x); `NaiveLFU` grows roughly linearly with capacity (16,772 → 66,168 → 268,562 ns/op, a ~16x increase matching the 16x capacity growth) -- the textbook O(1) vs O(n)-per-eviction shape, measured rather than assumed from the code's own structure.
- **Staleness pollution, measured across three regimes** (capacity 5, one key hammered 50 times then never touched again, followed by a cycling "currently hot" working set, 20 rounds):
  - Working set *smaller* than capacity (3 cycling keys): LFU and LRU both hit ≈96% -- no contention, the stale key and the whole working set fit simultaneously regardless of eviction policy.
  - Working set *larger* than capacity (8 cycling keys): LFU and LRU both hit ≈23-29% -- a pure round-robin cycle longer than the cache is LRU's own classic worst case (every access is a miss in steady state), so LRU's usual advantage disappears **because LRU itself collapses to meet LFU**, not because LFU improved.
  - Working set *exactly equal* to capacity (5 cycling keys) -- the one regime where reclaiming the single stale-polluted slot is the entire difference between the working set fitting and not: **LFU 32.67% vs. LRU 96.00%**, a >60-point gap.
- **55 tests, 5/5 consecutive clean runs.**

## Follow-up Questions

**Why?**
LFU shows up anywhere "popular" should mean something more durable than "recent" -- CDN edge caching for genuinely evergreen content, caching compiled query plans, OS page-cache variants -- but it has a real, well-known failure mode (no forgetting) that this exercise measures directly rather than citing, which is also exactly why production LFU variants almost always add some form of aging.

**How exactly?**
Three structures move in lockstep: a value is looked up directly by key (O(1)); a key's frequency is looked up directly by key (O(1)); and "which key is both minimum-frequency and least-recently-used" is answered by reading the front of an `OrderedDict` at a known frequency (O(1)) -- no step anywhere requires comparing against every other stored key.

**Which algorithm?**
The standard O(1) LFU construction (frequency buckets + a `min_freq` pointer) -- the same shape as the well-known "LFU Cache" interview problem (LeetCode 460), with LRU used as the tie-break within each frequency bucket, which is the conventional, not the only possible, choice.

**Which library?**
`collections.OrderedDict` only -- its O(1) `popitem(last=False/True)` and insertion-order-preserving `__setitem__`/`__delitem__` are exactly the primitives both the per-frequency buckets and `SimpleLRU` need.

**What happens internally?**
Every `OrderedDict` bucket is really just a doubly-linked list with O(1) splice operations under a dict's O(1) lookup -- `del bucket[key]` finds the node by key in O(1) and unlinks it in O(1); `bucket[key] = None` (re-)inserts at the tail in O(1). Nothing about moving a key between frequency buckets ever touches any *other* key's position, which is the structural reason this stays O(1) regardless of how many distinct frequencies currently exist.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **A tie at the minimum frequency**: broken by recency, verified with a scenario constructed specifically to produce one (not one where a single key is already the strict minimum, which would pass with no tie-breaking logic at all) -- see `test_tie_break_evicts_the_least_recently_used_among_equal_frequencies`, traced by hand against the real implementation's internal state before trusting the assertion.
- **Capacity of exactly 1**: every `put` of a new key evicts the current sole occupant unconditionally -- verified directly.
- **A workload where the staleness-pollution effect is expected to generalize beyond its actual regime**: it doesn't, and the test suite says so explicitly (`test_staleness_pollution_is_a_narrow_window_not_a_general_lru_advantage`) rather than only reporting the one favorable comparison.
- **`get`/`put` disagreeing with `NaiveLFU` on frequency while still agreeing on returned values**: the differential test checks `frequency_of` on every key after every operation, not just the value returned by the operation itself -- a disagreement here means a *future* eviction decision is already wrong, even though nothing visible has demonstrated it yet.

**What trade-offs did you consider?**
- **LRU as the tie-break within a frequency bucket (chosen) vs. FIFO (insertion order, ignoring subsequent accesses) or no defined tie-break at all:** LRU-within-frequency is the standard choice and the one this exercise's "LFU alone doesn't specify this" framing is about -- FIFO tie-breaking is a legitimate, different policy (and arguably simpler), but it isn't what "LFU" conventionally means absent a stated alternative.
- **Measuring the staleness-pollution effect across three regimes (chosen) vs. reporting only the one dramatic comparison (32.67% vs. 96.00%):** reporting only the favorable number would have been actively misleading -- the first version of this exercise's test suite asserted the gap held "across several workload shapes" using parameters that, on direct recalculation, didn't actually produce a gap at all in two of the three cases (see Key Learnings). The honest result is more useful than the dramatic one.
- **A separate, minimal `SimpleLRU` built fresh (chosen) vs. importing 2026-09-20's fuller `LRUCache`:** this repo's convention is that each day stands alone; the comparison only needs plain LRU semantics, not TTL or thread-safety, so pulling in the fuller implementation would add dependencies and surface area the comparison doesn't need.

**How do you debug it?**
- For an eviction that looks wrong, `cache.frequency_of(key)` for every currently-cached key, printed together, answers "was the victim actually the minimum" immediately -- no need to inspect `_freq_to_keys`/`_min_freq` internals directly unless the frequencies themselves look right but the wrong key still got evicted (which would point at the tie-break, not the frequency tracking).
- For a staleness-pollution measurement that doesn't match expectations, checking whether the cycling working-set size is below, at, or above capacity (relative to the one slot a stale key occupies) is the first thing to check -- the effect's entire presence or absence hinges on that relationship, not on anything else in the workload.
- The random differential test's per-key frequency check, not just the returned-value check, is what would catch a bug that's invisible in normal use but corrupts a future eviction decision -- exactly the shape of bug a purely output-based test would miss entirely.

**How do you evaluate it?**
- **55 tests, 5/5 consecutive clean runs** (30 randomized differential-testing seeds against `NaiveLFU`, checking frequency state, not just values, at every step).
- **Mutation check, 5 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - `_bump` doesn't actually increment frequency (moves within the same bucket): 35/55 failed.
  - Eviction pops the most-recently-used key within the minimum-frequency bucket instead of the least: 26/55 failed.
  - `min_freq` is never reset to 1 after a fresh insert: 37/55 failed.
  - `get` never bumps frequency (reads treated as free): 36/55 failed.
  - `put` on an existing key skips the frequency bump (updates value only): 31/55 failed.
- No zero-failure mutations -- the randomized differential test against `NaiveLFU`, checking internal frequency state rather than only returned values, caught every one of these immediately and heavily, which is itself informative: this is the kind of test that makes a whole class of subtle state-corruption mutation hard to miss.

## Key Learnings

- **An initial version of the staleness-pollution test overclaimed, and recalculating by hand (not just trusting the first favorable number) caught it before it shipped.** The first draft asserted the LFU-loses-to-LRU gap "holds across several workload shapes" using three parameter sets chosen quickly, without checking each one. Measuring all three directly showed the gap only existed in exactly one of them (cycling working-set size equal to capacity); the other two showed either no gap at all (working set smaller than capacity: no contention) or both policies collapsing together (working set larger than capacity: LRU's own round-robin-worse-than-cache-size pathology erases its usual advantage, since a pure round-robin cycle longer than the cache is a well-known LRU worst case in its own right). The fix wasn't to find better parameters that happened to show a bigger gap everywhere -- there aren't any, because the effect is genuinely narrow -- it was to measure and report the actual shape of the regime, including the two ways the dramatic number fails to generalize.
- **A dramatic, cherry-picked-looking number (32.67% vs. 96.00%) turned out to be real and reproducible, but the honest finding is the *characterization* of when it holds, not the number itself.** Reporting "LFU loses badly to LRU here" without the boundary conditions would have been a true statement about one specific workload shape and a misleading implication about LFU vs. LRU in general -- the boundary conditions are the actual content of the finding.
