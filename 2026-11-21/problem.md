# Problem

**Category:** `dsa`

## Statement

Implement a Fenwick tree (Binary Indexed Tree) supporting point updates and range-sum queries, and measure — by counting actual elementary operations, not by quoting the textbook complexity — what it actually buys over the two naive ways of doing the same job, neither of which is bad at *both* operations, only at one of them.

**Requirements:**

- `FenwickTree(size: int)` — `update(index: int, delta: int) -> None` (add `delta` to the element at `index`), `prefix_sum(index: int) -> int` (sum of elements `[0..index]`, `0` for `index < 0`), `range_sum(left: int, right: int) -> int` (sum of elements `[left..right]`, via `prefix_sum(right) - prefix_sum(left - 1)`). Every element starts at `0`.
- `PrecomputedPrefixArray(values: List[int])` — the "optimize queries" naive approach: a precomputed prefix-sum array gives `range_sum` in O(1), at the cost of an O(n) `update` (every prefix-sum entry from `index` onward has to shift).
- `UnindexedArray(values: List[int])` — the "optimize updates" naive approach: `update` is O(1) (just write the one cell), at the cost of an O(n) `range_sum` (sum the raw slice every time).
- All three expose a public `op_count: int`, incremented once per elementary operation (one array read/write, one loop iteration) performed inside `update`/`prefix_sum`/`range_sum` — the actual, countable thing standing in for "elapsed time," the same modeling choice 2026-11-06 made for a different reason (determinism over flakiness). Resettable by assigning `0` to it directly.

## Constraints

- Standard library only, no `random` — every index and query range used anywhere in this exercise is a fixed, hand-chosen value, not a sampled one, so every measured number is exactly reproducible.
- All three structures must agree on every `range_sum` result for the same sequence of updates — the performance comparison is only meaningful once correctness is confirmed independently of it.

## Approach

1. **The actual value proposition, stated precisely:** a Fenwick tree isn't faster than `PrecomputedPrefixArray` at range-sum queries, or faster than `UnindexedArray` at point updates — each naive structure is already optimal (O(1)) at the one operation it was built for. The Fenwick tree's advantage only shows up on a **mixed** workload that needs both operations, where it's the only one of the three that's cheap at *both* simultaneously (O(log n) each), rather than free at one and ruinous at the other.
2. **Exact, formula-matching measured claims, not asymptotic hand-waving:** for `size = n` a power of two, `update(0, delta)` costs exactly `floor(log2(n)) + 1` counted operations, and `prefix_sum(n - 2)` costs exactly `floor(log2(n))` — both confirmed by running the real implementation at several sizes and checking the exact integer, not just "it's small."
3. **A growth-curve measurement, not a single data point:** run the identical fixed workload (10 updates, 10 half-array-spanning range-sum queries, same relative indices every time) against all three structures at `size` = 16, 64, 256, 1,024, 4,096, and 16,384, and record total `op_count` at each size. `FenwickTree`'s total grows by roughly a constant amount per doubling of `size`; both naive structures' totals grow roughly linearly with `size` — confirmed as an actual recorded table, not asserted from Big-O notation.
4. **The crossover is real, not assumed:** at the smallest size tested (16), the naive structures aren't catastrophically worse — the gap only becomes dramatic (several orders of magnitude, measured directly) once `size` reaches the thousands, which is the actual, falsifiable version of "the fixed structure stops mattering for small n."

## Stretch

- Range updates (add `delta` to every element in `[left, right]`) with point queries — the Fenwick tree's "dual" mode, built with a second tree rather than modifying this one.
- A 2D Fenwick tree for rectangle-sum queries.
- Comparing against a segment tree, which matches the Fenwick tree's O(log n) bound on both operations but supports a wider range of associative operations (min, max, gcd) that prefix sums alone can't.
