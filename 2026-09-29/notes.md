## Implementation

- `MinHeap` stores items in a plain Python list; `_less(i, j)` compares by an injectable `key`, defaulting to identity. `_sift_up`/`_sift_down` are the only two primitives that touch heap structure; everything else (`push`, `pop`, `replace`, construction) is built from them.
- Construction sifts down from `len(data)//2 - 1` (the last node with a child) to `0` — leaves need no work, which is what makes building a heap from `n` items O(n) rather than O(n log n).
- `pop()` swaps the last element into the root, shrinks the list, then sifts down once — the standard "swap with the last, then fix" pattern that avoids shifting every remaining element.
- `replace(item)` overwrites the root directly and sifts down once, skipping the sift-up a separate `pop()` + `push()` would do — a real, if small, saving used by algorithms that repeatedly extract-and-reinsert (Dijkstra's relaxation step).
- `is_valid()` is a pure O(n) invariant reader, never called from the hot path — used only to verify the structure in tests and the demo.
- `NaivePriorityQueue` (list + linear scan for the min) exists purely as a complexity contrast; it is not "an alternative implementation," it's the thing the heap improves on.
- `merge_sorted_lists` keeps only the current head of each of the `k` lists in the heap at once (heap size ≤ k), pulling in each list's next element only after its current head is popped.
- Not built: `decrease_key` (needs a value→index map to be efficient — real for Dijkstra with a mutable heap, skipped here as it changes the data structure's shape); a max-heap variant (trivial — negate the key — not worth a second class); thread safety.

## Complexity

- `push`, `pop`, `replace`: O(log n). `peek`, `len`: O(1). Construction from `n` items: O(n).
- `heap_sort`: O(n log n) — O(n) build + n pops at O(log n) each.
- `merge_sorted_lists`: O(N log k) — N total items, a heap of size at most k.
- Measured (`NaivePriorityQueue` vs `MinHeap`, full push-everything-then-pop-everything cycle): n=500 → naive 5.6× slower; n=2,000 → 17.4×; n=8,000 → 60.0× — consistent with O(n²) vs O(n log n) as n grows 16× end to end.

## Follow-up Questions

**Why?**
Anything that repeatedly needs "the current smallest/highest-priority item, and more items may arrive before it's fully drained" wants this shape: a scheduler picking the next task by priority, Dijkstra's shortest path always expanding the closest unvisited node, event simulation processing events in time order, and k-way merging (this exercise's own application). A sorted list would also answer "give me the min," but keeping it sorted after every insert costs O(n); the heap keeps the *access* to the min at O(1) and pays only O(log n) to fix things up after a change, without maintaining full order.

**How exactly?**
Store items in an array where every parent is ≤ both its children — the heap property. `push`: append, then walk the new item up past any parent larger than it (`sift_up`). `pop`: swap the last item into the root's place (which was just vacated), shrink, then walk it down into place, always swapping into whichever child is smaller (`sift_down`).

**Which algorithm?**
The binary heap (Williams, 1964), built bottom-up in O(n) (Floyd's build-heap); heap sort as its direct application; k-way merge as the classic use of a heap to avoid re-comparing already-ordered data.

**Which library?**
Standard library only, and deliberately not `heapq` for the implementation itself — `heapq` is used exclusively as the correctness oracle and, in the k-way-merge comparison, as a second, official baseline.

**What happens internally?**
Every `sift_up`/`sift_down` step is one array-index comparison and (at most) one swap — no allocation, no pointer chasing, good cache locality since children are always nearby in the same array. This is a real, if unmeasured-here, reason binary heaps often beat asymptotically-equivalent pointer-based structures in practice.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **Wrong comparison direction** (a max-heap accidentally instead of a min-heap): caught immediately by `heap_sort` no longer matching `sorted()`, and by the differential test against `heapq` diverging on the very first pop.
- **A missing sift after `pop`/`replace`:** caught by `is_valid()` returning `False` on the very next check — verified directly via mutation (see below).
- **`is_valid()` itself only checking one child:** a real gap this exercise's own mutation check found (see Key Learnings) — now covered by a test that constructs a heap valid on the left but broken on the right.
- **`merge_sorted_lists` given an unsorted input list:** silently produces a wrong (not-fully-sorted) result — the function trusts its precondition and does not re-validate it, matching how `heapq.merge` itself behaves.

**What trade-offs did you consider?**
- **Array-based (chosen) vs a pointer-based binary tree:** the array needs no per-node object/pointer overhead and keeps related elements close in memory; the cost is that arbitrary-position deletion or arbitrary-key decrease needs a separate index map to be efficient (not built here — this heap only ever removes the root).
- **`replace()` as one sift (chosen) vs `pop()` + `push()`:** functionally identical result, but one sift-down instead of a sift-down *and* a sift-up. Verified equivalent in results, not just faster.
- **Heap-based k-way merge vs concatenate-and-sort — measured, not assumed.** `O(N log k) < O(N log N)` is a real reduction in comparisons, but on 50 sorted lists of 2,000 items each (N=100,000, k=50), `sorted()` on the concatenation beat even the standard library's own `heapq.merge` by 3.4×, and this hand-written `MinHeap`'s merge was slower still. The honest read: CPython's `sorted()` is a highly tuned C implementation (Timsort), while both merge approaches pay Python-level per-comparison overhead proportionally more often relative to the work saved. The algorithmic argument for heap-based merging is real, but its practical payoff shows up when inputs are lazy/streamed and can't be concatenated and sorted as a single in-memory operation (external sort merging sorted runs from disk, merging live sorted streams) — not as a speed win on data that already fits comfortably as Python lists.

**How do you debug it?**
- `is_valid()` after any suspicious sequence is the first check — it names *that* something is broken, though not *what specific operation* broke it.
- The differential test against `heapq` (assert equality after every single operation, not just at the end) is what actually localizes a bug to a specific operation and a specific randomized sequence — far more useful than a final-state-only comparison.
- For the k-way merge, comparing against `heapq.merge` in addition to `sorted()` separates "is my heap-merge logic correct" (yes, matches `heapq.merge` exactly) from "is heap-based merging fast in Python" (measured: no, not at this scale) — collapsing those two questions into one comparison would have been misleading.

**How do you evaluate it?**
- **52 tests, 15/15 consecutive clean runs.** 20 seeds × 200 randomized operations each, asserting equality against `heapq` and `is_valid()` after *every single operation* — not just a final-state check.
- **`heap_sort` matches `sorted()` exactly** on random data, already-sorted, reverse-sorted, all-duplicates, empty, and single-element inputs.
- **Construction correctness:** 10 seeds of random-length random data, checked as a multiset match against `heapq.heapify` (internal array layout need not match `heapq`'s exactly — only the heap property and the contents matter).
- **`merge_sorted_lists` matches both `sorted(concat)` and `heapq.merge`** across randomized inputs including empty lists, all-empty input, and a single list.
- **Mutation check — 9 of 9 real breaks caught** (wrong comparison direction, an ignored right child in `sift_down`, `pop` returning the wrong element, `pop`/`replace` skipping their sift, an off-by-one in the build-heap starting index, a merge that drops a list's remaining elements, a merge that reads from the wrong list) after fixing one real gap this check exposed — see Key Learnings. A tenth, deliberately harmless mutation (iterating over leaves too during construction — wasteful, not incorrect) was confirmed to *not* fail any test, which is itself the expected, correct outcome.
- **Not evaluated:** heap performance under adversarial input specifically designed to maximize sift-path length (worst case is still O(log n), just not empirically stressed here); memory use versus `heapq`'s C-level array (not measured — both are Python lists of Python objects, so no meaningful difference was expected).

## Key Learnings

- **The mutation check found a real gap, not just confirmed known-good tests.** `is_valid()` checking only the left child slipped past every existing test, because the one test that intentionally broke the heap (`test_is_valid_detects_a_broken_heap`) happened to corrupt an element whose *left* child was the violation. A targeted test constructing a heap valid on the left but broken specifically on the right closed the gap — and is a small, concrete reminder that a single "detects corruption" test can pass while covering only half of what it claims to check.
- **An asymptotic win is not automatically a wall-clock win in a specific runtime.** `O(N log k)` beating `O(N log N)` is true and checkable, and yet CPython's `sorted()` beat both this hand-written heap merge *and the standard library's own* `heapq.merge` by a wide margin on realistic in-memory data. The right conclusion isn't "the algorithm is wrong," it's "the algorithmic advantage and the practical advantage answer different questions" — one is about comparison count, the other is about where the real cost lives in a specific language implementation. Reporting the honest number here (and checking it against the standard library's own implementation, not just my own) was more useful than asserting the textbook result and moving on.
- **A differential test that checks after every operation catches far more than one that checks only the final state.** Every randomized test in this exercise compares against `heapq` step by step specifically because a heap bug (e.g., a missing sift) can silently corrupt structure for several operations before finally producing a wrong `pop()` result — checking only at the end would have found *that* something was eventually wrong, not *when*.
