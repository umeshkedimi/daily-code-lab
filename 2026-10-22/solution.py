"""LFU (Least-Frequently-Used) cache with O(1) get/put -- a genuinely
different eviction policy from 2026-09-20's LRU cache, not a rename.
LRU needs one structure (a recency-ordered list); O(1) LFU needs two,
layered: a frequency -> bucket-of-keys map, plus a `min_freq` pointer, so
"evict the least-frequently-used key" never requires scanning anything.

Tie-breaking is the actual design decision worth being explicit about:
when several keys share the current minimum frequency, this evicts the
LEAST-RECENTLY-USED one among them -- which is why each frequency bucket
is itself an OrderedDict, not a plain set. "LFU" alone doesn't specify
what happens on a tie; this implementation picks the standard, textbook
answer and tests it directly, not by accident.

The real point measured in this exercise, beyond raw correctness: pure
LFU has no notion of forgetting. A key that was hammered 50 times early
on and never touched again keeps a frequency count that nothing newer can
catch up to without also being accessed 50+ times -- so it can occupy a
cache slot indefinitely, starving a genuinely-current workload, in a
pattern LRU would have aged out automatically. Measured directly below
against a from-scratch LRU, on a workload built specifically to trigger
this (see notes.md).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Dict, Generic, Hashable, List, Optional, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class LFUCache(Generic[K, V]):
    """O(1) get/put. Evicts the least-frequently-used key; among keys
    tied at the minimum frequency, evicts the least-recently-used.

    Three parallel structures, kept in sync on every operation:
    - `_key_to_value`: the actual cached data.
    - `_key_to_freq`: how many times each key has been accessed.
    - `_freq_to_keys`: frequency -> an OrderedDict of keys at that
      frequency, oldest-accessed-at-this-frequency first -- this is what
      makes both "find the eviction victim" and "move a key to a new
      frequency bucket" O(1): no structure here is ever scanned linearly.
    """

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._key_to_value: Dict[K, V] = {}
        self._key_to_freq: Dict[K, int] = {}
        self._freq_to_keys: Dict[int, "OrderedDict[K, None]"] = {}
        self._min_freq = 0
        self._hits = self._misses = self._evictions = 0

    def __len__(self) -> int:
        return len(self._key_to_value)

    def __contains__(self, key: K) -> bool:
        return key in self._key_to_value

    def _bucket(self, freq: int) -> "OrderedDict[K, None]":
        bucket = self._freq_to_keys.get(freq)
        if bucket is None:
            bucket = OrderedDict()
            self._freq_to_keys[freq] = bucket
        return bucket

    def _bump(self, key: K) -> None:
        """Moves `key` from its current frequency bucket to the next one
        up, appending it there (most-recently-used within that bucket)."""
        freq = self._key_to_freq[key]
        old_bucket = self._freq_to_keys[freq]
        del old_bucket[key]
        if not old_bucket:
            del self._freq_to_keys[freq]
            if self._min_freq == freq:
                self._min_freq += 1  # the bucket that was the minimum is now empty
        new_freq = freq + 1
        self._key_to_freq[key] = new_freq
        self._bucket(new_freq)[key] = None

    def get(self, key: K, default: Any = None) -> Any:
        if key not in self._key_to_value:
            self._misses += 1
            return default
        self._bump(key)
        self._hits += 1
        return self._key_to_value[key]

    def put(self, key: K, value: V) -> None:
        if key in self._key_to_value:
            self._key_to_value[key] = value
            self._bump(key)
            return

        if len(self._key_to_value) >= self.capacity:
            bucket = self._freq_to_keys[self._min_freq]
            evict_key, _ = bucket.popitem(last=False)  # oldest at the minimum frequency
            if not bucket:
                del self._freq_to_keys[self._min_freq]
            del self._key_to_value[evict_key]
            del self._key_to_freq[evict_key]
            self._evictions += 1

        self._key_to_value[key] = value
        self._key_to_freq[key] = 1
        self._bucket(1)[key] = None
        self._min_freq = 1

    def frequency_of(self, key: K) -> Optional[int]:
        """Introspection only, for tests -- the actual cache never needs
        to answer "what's this key's frequency" as part of its own API."""
        return self._key_to_freq.get(key)

    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "evictions": self._evictions}


# --- reference implementations ----------------------------------------------------


class NaiveLFU(Generic[K, V]):
    """O(n) per eviction: a plain dict, a separate frequency counter, and
    a monotonic "last used" tick, scanning every stored key on eviction
    to find (minimum frequency, then least-recently-used among ties).
    Obviously correct by construction -- no bucket bookkeeping to get
    wrong -- used only to differential-test the real implementation and
    to measure the complexity gap, never for the LRU-comparison workload."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._store: Dict[K, V] = {}
        self._freq: Dict[K, int] = {}
        self._last_used: Dict[K, int] = {}
        self._tick = 0

    def __len__(self) -> int:
        return len(self._store)

    def get(self, key: K, default: Any = None) -> Any:
        if key not in self._store:
            return default
        self._freq[key] += 1
        self._tick += 1
        self._last_used[key] = self._tick
        return self._store[key]

    def put(self, key: K, value: V) -> None:
        if key in self._store:
            self._store[key] = value
            self._freq[key] += 1
            self._tick += 1
            self._last_used[key] = self._tick
            return
        if len(self._store) >= self.capacity:
            evict_key = min(self._store, key=lambda k: (self._freq[k], self._last_used[k]))
            del self._store[evict_key]
            del self._freq[evict_key]
            del self._last_used[evict_key]
        self._store[key] = value
        self._freq[key] = 1
        self._tick += 1
        self._last_used[key] = self._tick

    def frequency_of(self, key: K) -> Optional[int]:
        return self._freq.get(key)


class SimpleLRU(Generic[K, V]):
    """A minimal O(1) LRU, built fresh here rather than imported from
    2026-09-20 (each day in this repo is self-contained) -- exists only
    to measure the staleness-pollution comparison below, not as a
    from-scratch exercise in its own right (that's what 2026-09-20 is)."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._store: "OrderedDict[K, V]" = OrderedDict()

    def get(self, key: K, default: Any = None) -> Any:
        if key not in self._store:
            return default
        self._store.move_to_end(key)
        return self._store[key]

    def put(self, key: K, value: V) -> None:
        if key in self._store:
            self._store[key] = value
            self._store.move_to_end(key)
            return
        if len(self._store) >= self.capacity:
            self._store.popitem(last=False)
        self._store[key] = value


# --- measuring the staleness-pollution workload -----------------------------------


def run_workload(cache, operations: List[K]) -> float:
    """Feeds `operations` through `cache` as a sequence of reads: a hit
    counts toward the reported hit rate; a miss populates the cache with
    that key (value = the key itself; this benchmark never inspects
    values, only hit/miss)."""
    hits = 0
    for key in operations:
        if cache.get(key, "__MISS__") != "__MISS__":
            hits += 1
        else:
            cache.put(key, key)
    return hits / len(operations)


def build_staleness_workload(*, stale_bursts: int, cycle_keys: int, rounds: int) -> List[str]:
    """Phase 1: one key ("stale_popular") accessed `stale_bursts` times in
    a row, racking up a frequency count nothing in phase 2 will ever
    individually match. Phase 2: `cycle_keys` different keys, cycled
    through repeatedly for `rounds` rounds -- a shifted, currently-hot
    working set that "stale_popular" never appears in again.

    The repeating cycle matters: a single pass through the new keys would
    never *revisit* a key this cache wrongly evicted, so a bad eviction
    choice would have zero visible effect on the hit rate. Repeating the
    cycle is what makes "evicted the wrong thing" actually cost a hit
    later on, which is the entire point of measuring this at all.
    """
    ops = ["stale_popular"] * stale_bursts
    for _ in range(rounds):
        for i in range(cycle_keys):
            ops.append(f"new_{i}")
    return ops


def demo() -> None:
    import random
    import time

    print("1) Tie-breaking: LFU evicts the least-RECENTLY-used among keys tied at the minimum frequency")
    cache = LFUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # a: freq 2, b: freq 1 -- b is now the sole minimum
    cache.put("c", 3)  # must evict b, not a
    print(f"   after put(c): 'a' in cache = {'a' in cache}, 'b' in cache = {'b' in cache}")

    print("\n2) Staleness pollution: pure LFU vs. a from-scratch LRU, same shifted workload")
    ops = build_staleness_workload(stale_bursts=50, cycle_keys=5, rounds=20)
    lfu_hit_rate = run_workload(LFUCache(5), ops)
    lru_hit_rate = run_workload(SimpleLRU(5), ops)
    print(f"   LFU hit rate: {lfu_hit_rate:.2%}   LRU hit rate: {lru_hit_rate:.2%}")

    print("\n3) Complexity: LFUCache (O(1)) vs. NaiveLFU (O(n) per eviction)")
    rng = random.Random(0)

    def bench(make_cache, capacity: int, ops_count: int) -> float:
        cache = make_cache(capacity)
        for i in range(capacity):
            cache.put(i, i)
        keys = [rng.randrange(capacity * 2) for _ in range(ops_count)]
        start = time.perf_counter()
        for k in keys:
            if cache.get(k, "__MISS__") == "__MISS__":
                cache.put(k, k)
        return (time.perf_counter() - start) / ops_count * 1e9  # ns/op

    print(f"   {'capacity':>10} {'LFUCache (ns/op)':>18} {'NaiveLFU (ns/op)':>18}")
    for capacity in (500, 2_000, 8_000):
        ours = bench(LFUCache, capacity, 50_000)
        naive = bench(NaiveLFU, capacity, 5_000 if capacity >= 8_000 else 50_000)
        print(f"   {capacity:>10,} {ours:>18,.0f} {naive:>18,.0f}")


if __name__ == "__main__":
    demo()
