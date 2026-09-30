"""Sliding window maximum, via a monotonic deque.

For every contiguous window of size k sliding left to right across an array,
report the window's maximum. The naive approach scans all k elements of every
window (O(nk)). A deque holding candidate indices in decreasing order of
their values gets this to O(n) total: each index is pushed and popped at most
once across the whole run, so the amortized cost per window is O(1) even
though a single push/pop is O(k) in the worst case.
"""

from __future__ import annotations

from collections import deque
from typing import List, Sequence


def sliding_window_maximum(nums: Sequence[int], k: int) -> List[int]:
    """Returns the max of every contiguous window of size k, left to right.

    len(result) == len(nums) - k + 1 for len(nums) >= k >= 1.
    """
    n = len(nums)
    if k <= 0:
        raise ValueError("k must be positive")
    if k > n:
        raise ValueError("k cannot exceed the length of nums")

    # Indices into `nums`, kept in strictly decreasing order of nums[idx].
    # The front is always the max of the current window: anything it could
    # be beaten by would already have knocked it out the back (see below).
    idx_deque: deque[int] = deque()
    result: List[int] = []

    for i, x in enumerate(nums):
        # Evict from the back: any index whose value is <= x can never be
        # the answer for this or any future window that also contains i,
        # since x is later (so outlives it) and at least as large.
        while idx_deque and nums[idx_deque[-1]] <= x:
            idx_deque.pop()
        idx_deque.append(i)

        # Evict from the front: an index that has fallen out of the
        # current window's left edge is no longer a valid candidate.
        if idx_deque[0] <= i - k:
            idx_deque.popleft()

        if i >= k - 1:
            result.append(nums[idx_deque[0]])

    return result


def sliding_window_maximum_naive(nums: Sequence[int], k: int) -> List[int]:
    """O(nk) reference: scan every element of every window directly."""
    n = len(nums)
    if k <= 0:
        raise ValueError("k must be positive")
    if k > n:
        raise ValueError("k cannot exceed the length of nums")
    return [max(nums[i : i + k]) for i in range(n - k + 1)]
