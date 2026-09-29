"""Binary min-heap, array-based, built from scratch.

A heap keeps "find/remove the minimum" at O(log n) using a flat array and
index arithmetic, no pointers or tree nodes: parent(i) = (i-1)//2,
children(i) = 2i+1, 2i+2. Compared throughout against `heapq` (for
correctness) and a naive O(n)-per-op priority queue (for why the shape
matters), then used for k-way merge, the standard application.
"""

from __future__ import annotations

from typing import Any, Callable, Generic, Iterable, List, TypeVar

T = TypeVar("T")


class MinHeap(Generic[T]):
    def __init__(self, items: Iterable[T] = (), *, key: Callable[[T], Any] = lambda x: x):
        self._key = key
        self._data: List[T] = list(items)
        # Bottom-up heapify: sift-down from the last parent to the root.
        # Leaves (half the array) need no work, which is what makes this
        # O(n) overall rather than O(n log n) (n calls to push).
        for i in range(len(self._data) // 2 - 1, -1, -1):
            self._sift_down(i)

    def _less(self, i: int, j: int) -> bool:
        return self._key(self._data[i]) < self._key(self._data[j])

    def _sift_up(self, i: int) -> None:
        data = self._data
        while i > 0:
            parent = (i - 1) // 2
            if self._less(i, parent):
                data[i], data[parent] = data[parent], data[i]
                i = parent
            else:
                break

    def _sift_down(self, i: int) -> None:
        data = self._data
        n = len(data)
        while True:
            left, right, smallest = 2 * i + 1, 2 * i + 2, i
            if left < n and self._less(left, smallest):
                smallest = left
            if right < n and self._less(right, smallest):
                smallest = right
            if smallest == i:
                return
            data[i], data[smallest] = data[smallest], data[i]
            i = smallest

    def push(self, item: T) -> None:
        self._data.append(item)
        self._sift_up(len(self._data) - 1)

    def pop(self) -> T:
        if not self._data:
            raise IndexError("pop from an empty heap")
        top = self._data[0]
        last = self._data.pop()
        if self._data:  # otherwise `last` (now popped) was the only element
            self._data[0] = last
            self._sift_down(0)
        return top

    def peek(self) -> T:
        if not self._data:
            raise IndexError("peek at an empty heap")
        return self._data[0]

    def replace(self, item: T) -> T:
        """pop() the min and push(item), as one O(log n) step -- one sift
        instead of a pop's sift-down followed by a push's sift-up."""
        if not self._data:
            raise IndexError("replace on an empty heap")
        top = self._data[0]
        self._data[0] = item
        self._sift_down(0)
        return top

    def __len__(self) -> int:
        return len(self._data)

    def __bool__(self) -> bool:
        return bool(self._data)

    def is_valid(self) -> bool:
        """Every parent <= both children. O(n); an invariant check, not a
        hot-path operation."""
        n = len(self._data)
        for i in range(n):
            left, right = 2 * i + 1, 2 * i + 2
            if (left < n and self._less(left, i)) or (right < n and self._less(right, i)):
                return False
        return True


class NaivePriorityQueue(Generic[T]):
    """Reference model: an unsorted list, linear scan for the min. Trivially
    correct by construction -- the point of comparing against it is
    complexity, not cleverness."""

    def __init__(self, items: Iterable[T] = (), *, key: Callable[[T], Any] = lambda x: x):
        self._key = key
        self._data: List[T] = list(items)

    def push(self, item: T) -> None:
        self._data.append(item)

    def pop(self) -> T:
        if not self._data:
            raise IndexError("pop from an empty queue")
        i = min(range(len(self._data)), key=lambda idx: self._key(self._data[idx]))
        return self._data.pop(i)

    def peek(self) -> T:
        if not self._data:
            raise IndexError("peek at an empty queue")
        return min(self._data, key=self._key)

    def __len__(self) -> int:
        return len(self._data)


def heap_sort(items: Iterable[T], *, key: Callable[[T], Any] = lambda x: x) -> List[T]:
    h: MinHeap[T] = MinHeap(items, key=key)
    return [h.pop() for _ in range(len(h))]


def merge_sorted_lists(lists: List[List[T]]) -> List[T]:
    """k-way merge: O(N log k) for N total items across k sorted lists, by
    keeping only the current head of each list in the heap at once (a heap
    of size <= k) rather than sorting all N items together."""
    h: MinHeap = MinHeap(key=lambda t: t[0])
    for list_index, lst in enumerate(lists):
        if lst:
            h.push((lst[0], list_index, 0))
    out: List[T] = []
    while h:
        value, list_index, item_index = h.pop()
        out.append(value)
        next_index = item_index + 1
        if next_index < len(lists[list_index]):
            h.push((lists[list_index][next_index], list_index, next_index))
    return out


# --- Demo ------------------------------------------------------------------------------


def demo() -> None:
    import heapq
    import random
    import time

    print("1) Correctness: 500 randomized push/pop/replace sequences, checked against heapq and the invariant")
    rng = random.Random(0)
    mismatches = broken_invariant = 0
    for trial in range(500):
        mine: MinHeap = MinHeap()
        ref: List[int] = []
        for _ in range(60):
            op = rng.choice(["push", "push", "pop", "replace"])
            if op == "push" or not mine:
                v = rng.randint(-1000, 1000)
                mine.push(v)
                heapq.heappush(ref, v)
            elif op == "pop":
                if mine.pop() != heapq.heappop(ref):
                    mismatches += 1
            else:
                v = rng.randint(-1000, 1000)
                if mine.replace(v) != heapq.heapreplace(ref, v):
                    mismatches += 1
            if not mine.is_valid():
                broken_invariant += 1
    print(f"   mismatches vs heapq: {mismatches}/500 trials | heap-property violations observed: {broken_invariant}")

    print("\n2) heap_sort matches Python's sorted() on random and adversarial inputs")
    cases = [
        list(range(200)), list(range(200))[::-1], [5] * 200,
        [rng.randint(-50, 50) for _ in range(300)], [], [1],
    ]
    all_match = all(heap_sort(c) == sorted(c) for c in cases)
    print(f"   all {len(cases)} cases match sorted(): {all_match}")

    print("\n3) Why the array shape matters: MinHeap vs a naive O(n)-per-op priority queue")
    print(f"   {'n':>7} {'MinHeap (s)':>12} {'naive (s)':>10} {'naive/heap':>11}")
    for n in (500, 2_000, 8_000):
        data = [rng.randint(0, 10**9) for _ in range(n)]

        t0 = time.perf_counter()
        h: MinHeap = MinHeap()
        for x in data:
            h.push(x)
        while h:
            h.pop()
        heap_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        q: NaivePriorityQueue = NaivePriorityQueue()
        for x in data:
            q.push(x)
        while len(q):
            q.pop()
        naive_time = time.perf_counter() - t0

        print(f"   {n:>7,} {heap_time:>12.4f} {naive_time:>10.4f} {naive_time / heap_time:>10.1f}x")
    print("   (n grows 16x end to end; MinHeap's time should grow roughly with n log n, naive's with n^2)")

    print("\n4) k-way merge (O(N log k)) vs concatenate-and-sort (O(N log N)): asymptotics vs wall clock")
    import heapq as _heapq

    lists = [sorted(rng.randint(0, 10**9) for _ in range(2000)) for _ in range(50)]  # N=100,000, k=50
    t0 = time.perf_counter()
    merged = merge_sorted_lists(lists)
    merge_time = time.perf_counter() - t0
    t0 = time.perf_counter()
    stdlib_merged = list(_heapq.merge(*lists))
    stdlib_merge_time = time.perf_counter() - t0
    t0 = time.perf_counter()
    baseline = sorted(v for lst in lists for v in lst)
    sort_time = time.perf_counter() - t0
    print(f"   results identical: {merged == stdlib_merged == baseline}")
    print(f"   this MinHeap's merge: {merge_time:.4f}s | stdlib heapq.merge: {stdlib_merge_time:.4f}s | sorted(concat): {sort_time:.4f}s")
    print(f"   sorted() beats even heapq.merge here by {stdlib_merge_time / sort_time:.1f}x, despite doing MORE comparisons")
    print("   (N log k < N log N is real, but sorted()'s Timsort is C, and both merge implementations pay Python-level")
    print("    per-comparison overhead k times as often per item. The honest case for heap-based merging isn't raw")
    print("    speed on in-memory lists -- it's when inputs are lazy/streamed (files, network) and can't all be")
    print("    materialized and concatenated before sorting, e.g. merging sorted runs in an external sort.)")


if __name__ == "__main__":
    demo()
