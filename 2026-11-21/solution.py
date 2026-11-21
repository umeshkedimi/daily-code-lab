"""Fenwick tree (Binary Indexed Tree) vs the two naive approaches it
sits between -- measured, by counting actual elementary operations,
not by quoting the textbook complexity."""

from __future__ import annotations

from typing import List


class FenwickTree:
    def __init__(self, size: int) -> None:
        self.size = size
        self.tree = [0] * (size + 1)
        self.op_count = 0

    def update(self, index: int, delta: int) -> None:
        """O(log n): walk up setting lower bits until past size."""
        i = index + 1
        while i <= self.size:
            self.tree[i] += delta
            self.op_count += 1
            i += i & (-i)

    def prefix_sum(self, index: int) -> int:
        """O(log n): sum of elements [0..index], 0 for index < 0."""
        if index < 0:
            return 0
        i = index + 1
        total = 0
        while i > 0:
            total += self.tree[i]
            self.op_count += 1
            i -= i & (-i)
        return total

    def range_sum(self, left: int, right: int) -> int:
        return self.prefix_sum(right) - self.prefix_sum(left - 1)


class PrecomputedPrefixArray:
    """O(1) range_sum, O(n) update."""

    def __init__(self, values: List[int]) -> None:
        self.n = len(values)
        self.prefix = [0] * (self.n + 1)
        for i, v in enumerate(values):
            self.prefix[i + 1] = self.prefix[i] + v
        self.op_count = 0

    def update(self, index: int, delta: int) -> None:
        for i in range(index + 1, self.n + 1):
            self.prefix[i] += delta
            self.op_count += 1

    def range_sum(self, left: int, right: int) -> int:
        self.op_count += 1
        return self.prefix[right + 1] - self.prefix[left]


class UnindexedArray:
    """O(1) update, O(n) range_sum."""

    def __init__(self, values: List[int]) -> None:
        self.values = list(values)
        self.op_count = 0

    def update(self, index: int, delta: int) -> None:
        self.values[index] += delta
        self.op_count += 1

    def range_sum(self, left: int, right: int) -> int:
        total = 0
        for i in range(left, right + 1):
            total += self.values[i]
            self.op_count += 1
        return total
