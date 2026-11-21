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
        raise NotImplementedError

    def prefix_sum(self, index: int) -> int:
        """O(log n): sum of elements [0..index], 0 for index < 0."""
        raise NotImplementedError

    def range_sum(self, left: int, right: int) -> int:
        return self.prefix_sum(right) - self.prefix_sum(left - 1)


class PrecomputedPrefixArray:
    """O(1) range_sum, O(n) update."""

    def __init__(self, values: List[int]) -> None:
        self.n = len(values)
        self.values = list(values)
        self.prefix = [0] * (self.n + 1)
        for i, v in enumerate(values):
            self.prefix[i + 1] = self.prefix[i] + v
        self.op_count = 0

    def update(self, index: int, delta: int) -> None:
        raise NotImplementedError

    def range_sum(self, left: int, right: int) -> int:
        raise NotImplementedError


class UnindexedArray:
    """O(1) update, O(n) range_sum."""

    def __init__(self, values: List[int]) -> None:
        self.values = list(values)
        self.op_count = 0

    def update(self, index: int, delta: int) -> None:
        raise NotImplementedError

    def range_sum(self, left: int, right: int) -> int:
        raise NotImplementedError
