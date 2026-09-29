# Problem

**Category:** `dsa`

**Note:** second of two small DSA problems for 2026-09-29, each in its own commit — an explicit exception to the repo's "one problem per day" rule, logged here rather than silently breaking it.

## Statement

Implement a **binary min-heap** (array-based, no pointers) with `push`, `pop`, `peek`, O(1) `len`, and O(n) construction from an existing list. Use it for `heap_sort` and for the classic application, **k-way merge** of sorted lists.

**Requirements:**
- The heap property (every parent ≤ both children) must hold after every operation.
- Construction from a list must be O(n), not O(n log n) (i.e. not n calls to `push`).
- `heap_sort` must match `sorted()` exactly, including on adversarial and edge-case input.
- Compare against a naive priority queue to show *why* the array shape matters, not just that it works.

## Constraints

- No use of `heapq` in the implementation itself — it exists only as the correctness oracle.
- A custom ordering key must be supported (sorting/prioritizing by something other than the item's natural order).
- The k-way merge claim (`O(N log k)` beats `O(N log N)` concatenate-and-sort) must be checked against actual wall-clock behavior, not just cited.

## Approach

1. **No nodes, no pointers — just index arithmetic on a flat array.** For index `i`: parent is `(i-1)//2`, children are `2i+1` and `2i+2`. This is what makes the structure cache-friendly and simple to get right; the entire correctness argument is two local operations, sift-up (fix a new/changed element by walking toward the root) and sift-down (fix a changed root by walking toward the leaves).
2. **O(n) construction, not O(n log n).** Sifting down from the last *internal* node (index `n//2 - 1`) back to the root does the same job as `n` individual pushes, but leaves (half the array) are skipped entirely — the classic "build-heap" result, verified directly (not just cited) by an off-by-one mutation test that breaks it.
3. **`replace` as one sift, not pop-then-push.** Overwriting the root and sifting down once is cheaper than a full pop (sift-down) followed by a full push (sift-up) — useful in algorithms (Dijkstra, A*) that repeatedly swap out the current minimum for a new candidate.
4. **A naive O(n)-per-operation priority queue as the reason this matters, not just a filler baseline.** An unsorted list with a linear scan for the minimum is trivially correct and exactly what the heap is an improvement over; the demo measures the gap growing as the naive queue's build-up cost hits O(n²) against the heap's O(n log n).
5. **k-way merge, checked against wall clock rather than asserted from big-O.** `O(N log k)` for the heap-based merge is a real reduction in *comparisons* over sorting the full concatenation (`O(N log N)`), but measured against CPython's `sorted()` — a highly optimized C implementation (Timsort) — the heap-based version, even using the standard library's own `heapq.merge`, is measurably *slower* in wall-clock time on in-memory lists. Both are reported, with the honest conclusion: the real case for heap-based merging isn't raw speed on data that already fits in memory as Python lists — it's merging sources that are lazy or streamed (files, network) and can't be concatenated and sorted as one step.
