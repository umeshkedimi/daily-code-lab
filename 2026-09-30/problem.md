# Problem

**Category:** `dsa`

**Note:** first of two small DSA problems for 2026-09-30, each in its own commit — same exception to the "one problem per day" rule used on 2026-09-17 and 2026-09-29, logged here rather than silently breaking it.

## Statement

Given an array `nums` and a window size `k`, return the maximum of every contiguous window of size `k` as it slides from left to right across the array.

**Requirements:**
- `len(result) == len(nums) - k + 1`.
- Total cost across the whole slide should be proportional to `n` (the array length), not `n * k` — the naive approach of scanning all `k` elements of every window is the thing to beat.
- Correctness must hold with duplicate values, including when the current maximum appears more than once in the array and the first occurrence expires out of the window.

## Constraints

- No external libraries beyond the standard library (`collections.deque`).
- `k` must be a positive integer no larger than `len(nums)`; anything else is a usage error (raise, don't silently clamp).
- Correctness checked against an explicit O(nk) reference implementation, not just eyeballed on small examples — including randomized differential testing, not only hand-picked cases.

## Approach

1. **A deque of *indices* (not values), kept in strictly-decreasing order of `nums[index]`.** The front of the deque is always the current window's maximum: anything smaller that arrived earlier than a later, larger-or-equal element is permanently irrelevant, because the later element both outlives it (still in the window longer) and beats or ties it.
2. **Two eviction rules, applied once per index over the whole run:**
   - *Back eviction* (maintains the ordering invariant): before pushing index `i`, pop from the back any index whose value is `<= nums[i]` — those indices can never again be the max while `i` is still in the window.
   - *Front eviction* (maintains window membership): if the front index has fallen out of the window's left edge (`front <= i - k`), pop it.
3. **Amortized O(n) despite a single eviction potentially costing O(k):** every index is pushed exactly once and popped at most once across the entire run (from either end), so total push+pop work across all `n` iterations is O(n), not O(n) per window.
4. **Correctness measured against `sliding_window_maximum_naive`** (direct `max()` over each window slice, O(nk)) via randomized differential testing across array sizes, value ranges, and `k` — plus a duplicate-heavy value range specifically to stress the eviction-ordering logic, since ties are where an `<` vs `<=` mistake would actually surface.
