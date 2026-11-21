## Implementation

- `FenwickTree.update`'s `i += i & (-i)` and `prefix_sum`'s `i -= i & (-i)` are the entire mechanism: `i & (-i)` isolates the lowest set bit of `i`. Adding it climbs toward indices that are "responsible for" a wider range in the tree; subtracting it descends toward indices covering narrower, more recent ranges. The two loops are mirror images of each other, walking the same bit structure in opposite directions.
- `range_sum` is implemented purely in terms of `prefix_sum(right) - prefix_sum(left - 1)`, with no separate range logic of its own -- `prefix_sum(-1)` returning `0` (handled by the explicit `index < 0` guard) is what makes `left = 0` work without a special case.
- `PrecomputedPrefixArray` originally also kept a `self.values` list, updated inside `update` alongside `self.prefix` -- removed once mutation testing showed it was never read by `range_sum` or anything else: dead state that happened to look like reasonable bookkeeping. Keeping it would have meant an attribute that's silently wrong after the first `update` if someone later read it, since nothing enforced it stayed in sync with anything -- removing it was safer than adding a test to justify keeping it, since the stated contract (`update`, `range_sum`, `op_count`) never needed it.
- `UnindexedArray` is the simplest of the three by construction -- `update` does the one obvious thing, `range_sum` sums a slice directly -- deliberately, since its only job in this exercise is to be the obviously-correct, obviously-O(n)-on-queries baseline.
- Not built: range updates with point queries (the Fenwick tree's "dual" mode), a 2D Fenwick tree, a segment-tree comparison -- all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `FenwickTree.update` / `.prefix_sum` / `.range_sum` | O(log n) | O(n) |
| `PrecomputedPrefixArray.update` | O(n) | O(n) |
| `PrecomputedPrefixArray.range_sum` | O(1) | O(n) |
| `UnindexedArray.update` | O(1) | O(n) |
| `UnindexedArray.range_sum` | O(n) | O(n) |

## Measured Results

- **Exact formula match, not asymptotic hand-waving**: at `size = n` a power of two, `update(0, delta)` costs exactly `floor(log2(n)) + 1` counted operations, and `prefix_sum(n - 2)` costs exactly `floor(log2(n))` -- confirmed at `n` = 16, 64, 256, 1024, 4096 by running the real implementation, not derived from the complexity bound alone.
- **The growth curve, recorded, not asserted** (fixed workload: 10 updates + 10 half-array-spanning range-sum queries, varying `size`):

  | size | `FenwickTree` | `PrecomputedPrefixArray` | `UnindexedArray` |
  |---|---|---|---|
  | 16 | 40 | 95 | 90 |
  | 64 | 50 | 335 | 330 |
  | 256 | 70 | 2,255 | 1,290 |
  | 1,024 | 90 | 9,935 | 5,130 |
  | 4,096 | 110 | 40,655 | 20,490 |
  | 16,384 | 130 | 163,535 | 81,930 |

  `FenwickTree`'s total grows by a roughly constant +10 to +20 per doubling of `size` (consistent with O(log n)); both naive structures grow in step with `size` itself. At `size = 16`, the gap is unremarkable (40 vs. 90-95); at `size = 16,384`, `FenwickTree` is **over 1,250x** cheaper than `PrecomputedPrefixArray` and **over 600x** cheaper than `UnindexedArray` on the identical workload.
- **8 tests, 3/3 consecutive clean runs.**

## Follow-up Questions

**Why?**
"Use a Fenwick tree for range queries" is usually justified by quoting O(log n) without ever asking what that actually buys over the much simpler fixed structures most people reach for first -- and the honest answer is "nothing, if your workload only ever does one of the two operations," which is worth knowing before reaching for the more complex structure out of habit.

**How exactly?**
A Fenwick tree's internal array isn't a direct copy of the input -- each index `i` stores the sum of a specific range ending at `i`, whose length is exactly `i`'s lowest set bit. `update` only has to touch the O(log n) indices whose ranges actually include the updated position; `prefix_sum` only has to touch the O(log n) indices whose ranges, laid end to end, exactly cover `[0, index]`. Both are possible simultaneously because the structure never stores a complete prefix sum anywhere, and never stores a raw unprocessed value anywhere either -- it's a genuinely different representation, not a tuned version of either naive one.

**What happens internally?**
`update(0, ...)` is worst case for the update direction because index 0 (1-indexed as `1`, binary `...0001`) has to climb through every power-of-two checkpoint up to `size` -- `1 -> 2 -> 4 -> 8 -> ... -> size`, exactly `floor(log2(size)) + 1` steps. `prefix_sum(n - 2)` is worst case for the query direction for the opposite reason: 1-indexed `n - 1` (binary `...1111`, all its bits set, for `n` a power of two) has to shed one set bit per step, exactly `floor(log2(n))` steps -- the two worst cases are mirror images of the same bit-pattern fact.

**What if this fails?**
- **Someone benchmarks a Fenwick tree against `PrecomputedPrefixArray` using only range-sum queries**: it will look like a worse choice, correctly -- `PrecomputedPrefixArray.range_sum` is O(1) and always will be. The Fenwick tree only wins once `update` enters the workload at all.
- **Someone assumes the crossover point is sizeable at every `size`**: `test_growth_curve_matches_measured_values_exactly` at `size = 16` shows the naive structures aren't actually catastrophic yet (90-95 ops vs. 40) -- the dramatic gap is a `size`-dependent fact, confirmed at `size = 16,384`, not a property of the structures alone.
- **A caller keeps a convenience field in sync with the real state "just in case," without anything ever reading it**: `PrecomputedPrefixArray.values` was exactly this, caught by a mutation that removed its own maintenance and broke nothing.

**What trade-offs did you consider?**
- **Counting elementary operations directly via `op_count` (chosen) vs. wall-clock timing**: the same reasoning 2026-11-06 used -- wall-clock timing on structures this fast would be dominated by measurement noise long before the actual algorithmic difference showed up, especially at `size = 16` where the real gap is genuinely small.
- **Removing `PrecomputedPrefixArray.values` entirely (chosen) vs. keeping it and adding a test that reads it back after `update`**: the second option was briefly considered, since it would have "fixed" the mutation-testing gap too -- but the class's stated contract (`problem.md`'s Requirements) never asks for raw element access, only `update`/`range_sum`/`op_count`. Adding a test to justify state nothing in the spec needs would be testing a feature invented to make a mutation fail, not a feature the exercise actually requires.

**How do you evaluate it?**
- **8 tests, 3/3 consecutive clean runs.**
- **Mutation check, 4 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - `FenwickTree.update`'s `i += i & (-i)` changed to `i += 1` (degrades update to O(n)): 5/8 failed -- every op-count-sensitive test, plus the correctness test (since a linear walk from `index` to `size`, each adding `delta` to `tree[i]`, computes a different, wrong aggregate than the real Fenwick structure).
  - `FenwickTree.range_sum`'s `prefix_sum(left - 1)` changed to `prefix_sum(left)` (classic off-by-one, double-counts or drops the left boundary): 3/8 failed, including `test_prefix_sum_of_negative_index_is_zero`'s sibling check and the growth-curve table (whose expected values assume the correct boundary).
  - `PrecomputedPrefixArray.update` stops maintaining `self.values` (left in, unread): 0/8 failed on first attempt -- the exact finding described above; resolved by removing the dead field entirely rather than adding a test to keep it alive.
  - `UnindexedArray.range_sum`'s `range(left, right + 1)` changed to `range(left, right)` (drops the right boundary element): 3/8 failed, including the correctness tests (wrong sum) and the growth-curve table (wrong op count, since one fewer element is summed each query).

## Key Learnings

- **A mutation that survives every test by deleting an unread write is a direct, specific signal: that state was never load-bearing.** The honest response isn't "add a test to make this mutation fail" -- that inverts the relationship between spec and test, writing a test to justify code instead of code to satisfy a test. The actual fix is almost always deletion, and `problem.md`'s own stated Requirements (not the implementation's incidental choices) is the right place to check whether the deleted thing was ever actually needed.
- Consistent with 2026-10-15/2026-10-14's mutation-testing pattern: a zero-failure mutation isn't automatically "the test suite missed a bug" -- sometimes it's "the mutated code was dead," and the two have opposite correct responses (write a new test; delete the code), which only tracing the mutation by hand can distinguish.
- The two worst-case bit patterns (`update(0, ...)` and `prefix_sum(n - 2)`) weren't obvious from the O(log n) bound alone -- the first attempt at finding a "worst case" index picked an alternating-bit pattern that turned out to be nowhere near worst, and only running the real implementation and reading off the actual counts revealed that the true worst cases sit at the structure's own boundaries (index 0; index n - 1), not somewhere in the middle that merely looks adversarial.
