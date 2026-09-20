"""LRU cache with O(1) get/put and per-key TTL, built from a hash map plus a
doubly linked list (not OrderedDict), thread-safe behind a single lock.

The map gives O(1) lookup; the list keeps recency order so the eviction
victim (the tail) and the "move to most-recent" operation are both O(1)
pointer surgery. Neither structure alone is enough: a dict has no cheap
ordering, and a list has no cheap lookup.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Generic, Hashable, List, Optional, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class _Node:
    __slots__ = ("key", "value", "expires_at", "prev", "next")

    def __init__(self, key: Any, value: Any, expires_at: Optional[float]):
        self.key = key
        self.value = value
        self.expires_at = expires_at
        self.prev: Optional[_Node] = None
        self.next: Optional[_Node] = None


class LRUCache(Generic[K, V]):
    """Most-recent entries sit next to `_head`; the eviction victim is
    `_tail.prev`. Sentinel head/tail nodes mean unlink/insert never need
    a None check for the ends of the list.

    Expiry is lazy: an expired entry is only removed when it is read, when
    it reaches the LRU end and gets evicted, or when purge_expired() runs.
    That keeps get/put strictly O(1) (see notes.md for the trade-off).
    """

    def __init__(
        self,
        capacity: int,
        default_ttl: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if default_ttl is not None and default_ttl <= 0:
            raise ValueError("default_ttl must be > 0")
        self._capacity = capacity
        self._default_ttl = default_ttl
        self._clock = clock  # monotonic by default: wall-clock jumps must not resurrect or kill entries
        self._map: Dict[K, _Node] = {}
        self._head = _Node(None, None, None)
        self._tail = _Node(None, None, None)
        self._head.next = self._tail
        self._tail.prev = self._head
        self._lock = threading.Lock()
        self._hits = self._misses = self._evictions = self._expirations = 0

    # --- linked-list primitives (caller holds the lock) ----------------------

    def _unlink(self, node: _Node) -> None:
        node.prev.next = node.next
        node.next.prev = node.prev

    def _push_front(self, node: _Node) -> None:
        node.prev = self._head
        node.next = self._head.next
        self._head.next.prev = node
        self._head.next = node

    def _remove(self, node: _Node) -> None:
        self._unlink(node)
        del self._map[node.key]

    def _is_expired(self, node: _Node) -> bool:
        return node.expires_at is not None and self._clock() >= node.expires_at

    def _expiry_for(self, ttl: Optional[float]) -> Optional[float]:
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl must be > 0")
        effective = ttl if ttl is not None else self._default_ttl
        return None if effective is None else self._clock() + effective

    # --- public API ------------------------------------------------------------

    def get(self, key: K, default: Any = None) -> Any:
        with self._lock:
            node = self._map.get(key)
            if node is None:
                self._misses += 1
                return default
            if self._is_expired(node):
                self._remove(node)
                self._expirations += 1
                self._misses += 1
                return default
            self._unlink(node)
            self._push_front(node)
            self._hits += 1
            return node.value

    def put(self, key: K, value: V, ttl: Optional[float] = None) -> None:
        """Insert or update. Every put replaces the entry's expiry entirely:
        `ttl` if given, else the cache's default_ttl, else no expiry."""
        expires_at = self._expiry_for(ttl)  # validate before touching any state
        with self._lock:
            node = self._map.get(key)
            if node is not None:
                node.value = value
                node.expires_at = expires_at
                self._unlink(node)
                self._push_front(node)
                return

            node = _Node(key, value, expires_at)
            self._map[key] = node
            self._push_front(node)
            if len(self._map) > self._capacity:
                victim = self._tail.prev
                if self._is_expired(victim):
                    self._expirations += 1
                else:
                    self._evictions += 1
                self._remove(victim)

    def delete(self, key: K) -> bool:
        with self._lock:
            node = self._map.get(key)
            if node is None:
                return False
            self._remove(node)
            return True

    def purge_expired(self) -> int:
        """O(n) sweep -- the escape hatch for the lazy-expiry trade-off."""
        with self._lock:
            removed = 0
            node = self._head.next
            while node is not self._tail:
                nxt = node.next
                if self._is_expired(node):
                    self._remove(node)
                    self._expirations += 1
                    removed += 1
                node = nxt
            return removed

    def __contains__(self, key: object) -> bool:
        """Read-only: does not refresh recency and does not remove."""
        with self._lock:
            node = self._map.get(key)  # type: ignore[arg-type]
            return node is not None and not self._is_expired(node)

    def __len__(self) -> int:
        """Stored entries, including expired ones not yet purged."""
        with self._lock:
            return len(self._map)

    def keys(self) -> List[K]:
        """Stored keys, most-recent first (includes unpurged expired keys)."""
        with self._lock:
            out = []
            node = self._head.next
            while node is not self._tail:
                out.append(node.key)
                node = node.next
            return out

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "size": len(self._map),
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "expirations": self._expirations,
            }

    def check_invariants(self) -> None:
        """Debug/test aid: raises AssertionError if the map and list disagree."""
        with self._lock:
            forward, node = [], self._head.next
            # Bounded walk: on a corrupted list (e.g. a cycle from an unlocked
            # race) an unbounded `while` would hang instead of reporting it.
            limit = len(self._map) + 1
            while node is not self._tail:
                assert len(forward) <= limit, "list is longer than the map: cycle or stray nodes"
                assert node.prev.next is node, "broken prev/next link"
                forward.append(node)
                node = node.next
            assert self._tail.prev is (forward[-1] if forward else self._head), "tail link broken"
            assert len(forward) == len(self._map), "list/map size mismatch"
            assert all(self._map[n.key] is n for n in forward), "map points at a node not in the list"
            assert len(self._map) <= self._capacity, "over capacity"


class NaiveLRU:
    """O(n) reference implementation: a plain list scanned linearly. It's
    the oracle for differential testing and the baseline for the benchmark
    -- structurally too different from LRUCache to share its bugs."""

    def __init__(self, capacity: int, default_ttl: Optional[float] = None, clock: Callable[[], float] = time.monotonic):
        self._capacity = capacity
        self._default_ttl = default_ttl
        self._clock = clock
        self._items: List[list] = []  # [key, value, expires_at]; index 0 = most recent

    def _expired(self, item: list) -> bool:
        return item[2] is not None and self._clock() >= item[2]

    def get(self, key: Any, default: Any = None) -> Any:
        for i, item in enumerate(self._items):
            if item[0] == key:
                if self._expired(item):
                    del self._items[i]
                    return default
                self._items.insert(0, self._items.pop(i))
                return item[1]
        return default

    def put(self, key: Any, value: Any, ttl: Optional[float] = None) -> None:
        effective = ttl if ttl is not None else self._default_ttl
        expires_at = None if effective is None else self._clock() + effective
        for i, item in enumerate(self._items):
            if item[0] == key:
                del self._items[i]
                break
        self._items.insert(0, [key, value, expires_at])
        if len(self._items) > self._capacity:
            self._items.pop()

    def delete(self, key: Any) -> bool:
        for i, item in enumerate(self._items):
            if item[0] == key:
                del self._items[i]
                return True
        return False

    def purge_expired(self) -> int:
        before = len(self._items)
        self._items = [it for it in self._items if not self._expired(it)]
        return before - len(self._items)

    def contains(self, key: Any) -> bool:
        return any(it[0] == key and not self._expired(it) for it in self._items)

    def keys(self) -> List[Any]:
        return [it[0] for it in self._items]


class OrderedDictLRU:
    """The idiomatic Python LRU, for the benchmark: OrderedDict's
    move_to_end is O(1) and implemented in C, so it's the speed to beat."""

    def __init__(self, capacity: int):
        self._capacity = capacity
        self._d: "OrderedDict[Any, Any]" = OrderedDict()

    def get(self, key: Any, default: Any = None) -> Any:
        if key not in self._d:
            return default
        self._d.move_to_end(key, last=False)
        return self._d[key]

    def put(self, key: Any, value: Any) -> None:
        self._d[key] = value
        self._d.move_to_end(key, last=False)
        if len(self._d) > self._capacity:
            self._d.popitem(last=True)


# --- Demo ------------------------------------------------------------------------


def _bench(make: Callable[[int], Any], capacity: int, ops: int) -> float:
    import random

    cache = make(capacity)
    if isinstance(cache, NaiveLRU):
        # Filling via put() would itself be O(n^2) for the baseline; load it
        # directly so setup cost doesn't dwarf the (timed) steady-state ops.
        cache._items = [[i, i, None] for i in reversed(range(capacity))]
    else:
        for i in range(capacity):
            cache.put(i, i)
    rng = random.Random(0)
    keys = [rng.randrange(capacity * 2) for _ in range(ops)]  # ~50% hit rate, steady eviction
    start = time.perf_counter()
    for k in keys:
        if cache.get(k) is None:
            cache.put(k, k)
    return (time.perf_counter() - start) / ops * 1e9  # ns/op


def demo() -> None:
    print("1) Eviction order and recency refresh (capacity 3)")
    c: LRUCache = LRUCache(3)
    for k in "abc":
        c.put(k, k.upper())
    c.get("a")  # refresh a -> b is now the LRU
    c.put("d", "D")  # evicts b
    print(f"   keys MRU->LRU: {c.keys()}  (b evicted, a survived because it was read)")

    print("\n2) TTL with an injected clock (no sleeping)")
    now = [0.0]
    t: LRUCache = LRUCache(10, clock=lambda: now[0])
    t.put("session", "abc", ttl=5)
    now[0] = 4.999
    print(f"   t=4.999 -> {t.get('session')!r}")
    now[0] = 5.0
    print(f"   t=5.000 -> {t.get('session')!r}  (expires_at is exclusive)")
    print(f"   stats: {t.stats()}")

    print("\n3) Per-op cost stays flat as capacity grows (ns/op, mixed get/put)")
    print(f"   {'capacity':>10} {'LRUCache':>10} {'OrderedDict':>12} {'naive list':>11}")
    for capacity in (1_000, 10_000, 100_000):
        ours = _bench(lambda n: LRUCache(n), capacity, 200_000)
        od = _bench(OrderedDictLRU, capacity, 200_000)
        naive = _bench(lambda n: NaiveLRU(n), capacity, 300 if capacity >= 100_000 else 2_000)
        print(f"   {capacity:>10,} {ours:>10,.0f} {od:>12,.0f} {naive:>11,.0f}")

    print("\n4) Thread safety: 8 threads x 20,000 mixed ops on one cache")
    shared: LRUCache = LRUCache(50)

    def worker(seed: int) -> None:
        import random

        rng = random.Random(seed)
        for _ in range(20_000):
            k = rng.randrange(200)
            if rng.random() < 0.5:
                shared.put(k, k * 2)
            else:
                v = shared.get(k)
                assert v is None or v == k * 2, f"torn value for key {k}: {v}"

    threads = [threading.Thread(target=worker, args=(s,)) for s in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    shared.check_invariants()
    print(f"   invariants hold, size={len(shared)} <= 50, stats={shared.stats()}")


if __name__ == "__main__":
    demo()
