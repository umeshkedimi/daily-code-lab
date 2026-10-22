import random

import pytest

from solution import LFUCache, NaiveLFU, SimpleLRU, build_staleness_workload, run_workload

IMPLS = [LFUCache, NaiveLFU]


# --- basic behavior, both implementations -----------------------------------------


@pytest.mark.parametrize("impl", IMPLS)
def test_put_then_get_returns_the_value(impl):
    cache = impl(2)
    cache.put("a", 1)
    assert cache.get("a") == 1


@pytest.mark.parametrize("impl", IMPLS)
def test_get_on_missing_key_returns_the_default(impl):
    cache = impl(2)
    assert cache.get("missing") is None
    assert cache.get("missing", "fallback") == "fallback"


@pytest.mark.parametrize("impl", IMPLS)
def test_put_on_an_existing_key_updates_the_value(impl):
    cache = impl(2)
    cache.put("a", 1)
    cache.put("a", 2)
    assert cache.get("a") == 2


@pytest.mark.parametrize("impl", IMPLS)
def test_rejects_non_positive_capacity(impl):
    with pytest.raises(ValueError):
        impl(0)
    with pytest.raises(ValueError):
        impl(-1)


@pytest.mark.parametrize("impl", IMPLS)
def test_frequency_starts_at_one_and_increments_on_each_access(impl):
    cache = impl(2)
    cache.put("a", 1)
    assert cache.frequency_of("a") == 1
    cache.get("a")
    assert cache.frequency_of("a") == 2
    cache.get("a")
    assert cache.frequency_of("a") == 3


@pytest.mark.parametrize("impl", IMPLS)
def test_put_on_an_existing_key_also_bumps_frequency(impl):
    cache = impl(2)
    cache.put("a", 1)
    cache.put("a", 2)  # an update, not a fresh insert -- still counts as a use
    assert cache.frequency_of("a") == 2


# --- the core eviction policy: least frequency, then least-recently-used -------


@pytest.mark.parametrize("impl", IMPLS)
def test_evicts_the_strictly_least_frequent_key(impl):
    cache = impl(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # a: freq 2, b: freq 1
    cache.put("c", 3)  # must evict b (strictly lower frequency), not a
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


@pytest.mark.parametrize("impl", IMPLS)
def test_tie_break_evicts_the_least_recently_used_among_equal_frequencies(impl):
    cache = impl(3)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)  # all three at freq 1
    cache.get("b")  # b -> freq 2
    cache.get("c")  # c -> freq 2
    # a is still at freq 1, alone -- it's the unambiguous victim regardless
    # of tie-breaking; use a second round to actually test the tie-break.
    cache.put("d", 4)  # evicts a (freq 1), now b/c/d all exist
    cache.get("b")  # b -> freq 3
    cache.get("c")  # c -> freq 3
    # d is at freq 1 (just inserted), alone -- still not a tie. Build an
    # actual tie: bump d to freq 3 too, then access b again so b is no
    # longer the least-recently-used of the tied trio.
    cache.get("d")  # d -> freq 2
    cache.get("d")  # d -> freq 3  (b, c, d: all freq 3 now)
    cache.get("b")  # b -> freq 4; access order among the OTHERS: c then d
    cache.put("e", 5)  # evicts the least-recently-used of {c, d} at freq 3 -- that's c
    assert cache.get("c") is None
    assert cache.get("d") == 4
    assert cache.get("b") == 2
    assert cache.get("e") == 5


@pytest.mark.parametrize("impl", IMPLS)
def test_getting_a_key_protects_it_from_the_next_eviction(impl):
    cache = impl(2)
    cache.put("a", 1)
    cache.put("b", 2)  # both freq 1, a inserted first
    cache.get("a")  # a -> freq 2; b remains the sole minimum
    cache.put("c", 3)  # evicts b
    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


@pytest.mark.parametrize("impl", IMPLS)
def test_capacity_one_always_evicts_the_current_sole_occupant(impl):
    cache = impl(1)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") is None
    assert cache.get("b") == 2


# --- stats / introspection ----------------------------------------------------------


def test_lfucache_stats_track_hits_misses_and_evictions():
    cache = LFUCache(1)
    cache.put("a", 1)
    cache.get("a")  # hit
    cache.get("b")  # miss
    cache.put("b", 2)  # evicts a
    stats = cache.stats()
    assert stats == {"hits": 1, "misses": 1, "evictions": 1}


def test_lfucache_len_and_contains():
    cache = LFUCache(2)
    assert len(cache) == 0
    cache.put("a", 1)
    assert len(cache) == 1
    assert "a" in cache
    assert "b" not in cache


# --- differential testing: LFUCache vs. NaiveLFU, many random operation sequences ---


@pytest.mark.parametrize("seed", range(30))
def test_matches_naive_reference_on_random_operation_sequences(seed):
    rng = random.Random(seed)
    capacity = rng.randint(1, 8)
    fast = LFUCache(capacity)
    naive = NaiveLFU(capacity)
    keys = [f"k{i}" for i in range(capacity * 2)]

    for _ in range(300):
        key = rng.choice(keys)
        if rng.random() < 0.5:
            fast_result = fast.get(key, "__MISS__")
            naive_result = naive.get(key, "__MISS__")
            assert fast_result == naive_result
        else:
            value = rng.randint(0, 10_000)
            fast.put(key, value)
            naive.put(key, value)
        assert len(fast) == len(naive)
        # Every key naive knows about must agree on frequency, and vice
        # versa -- not just "the same values," but the same internal
        # state that future eviction decisions depend on.
        for k in keys:
            assert fast.frequency_of(k) == naive.frequency_of(k)


# --- the staleness-pollution finding: measured, not asserted from memory --------


def test_staleness_pollution_workload_shows_lfu_losing_badly_to_lru():
    # A key hammered early and never touched again keeps a frequency
    # count nothing in a later, genuinely-current workload can catch up
    # to without matching that same count -- so pure LFU keeps it cached
    # indefinitely, at the expense of the actually-hot working set.
    ops = build_staleness_workload(stale_bursts=50, cycle_keys=5, rounds=20)
    lfu_hit_rate = run_workload(LFUCache(5), ops)
    lru_hit_rate = run_workload(SimpleLRU(5), ops)
    assert lfu_hit_rate < 0.5  # measured: 32.67%
    assert lru_hit_rate > 0.9  # measured: 96.00%
    assert lru_hit_rate > lfu_hit_rate * 2  # LRU is not just better, it's dramatically better here


def test_staleness_pollution_disappears_when_the_stale_key_is_revisited():
    # Sanity check against over-claiming: if "stale_popular" is ALSO part
    # of the ongoing cycle (still genuinely relevant), LFU has no
    # particular problem with it -- the issue is specifically about a key
    # that stops being accessed, not about high-frequency keys in general.
    ops = ["stale_popular"] * 50
    for _ in range(20):
        ops.extend(["stale_popular", "new_0", "new_1", "new_2", "new_3"])
    lfu_hit_rate = run_workload(LFUCache(5), ops)
    assert lfu_hit_rate > 0.9


def test_staleness_pollution_is_a_narrow_window_not_a_general_lru_advantage():
    # The dramatic gap above is NOT "LRU is just generally better" -- it
    # only shows up when the cycling working set's size is close to
    # exactly `capacity` (just enough that reclaiming the one
    # stale-polluted slot is the difference between everything fitting
    # and something thrashing). Measured directly on both sides:
    capacity = 5

    # Fewer cycle keys than capacity - 1: no contention either way, both
    # policies have room for the stale key AND the whole working set.
    ops_low = build_staleness_workload(stale_bursts=50, cycle_keys=3, rounds=20)
    lfu_low = run_workload(LFUCache(capacity), ops_low)
    lru_low = run_workload(SimpleLRU(capacity), ops_low)
    assert lfu_low == pytest.approx(lru_low, abs=0.05)

    # More cycle keys than capacity: the working set alone doesn't fit in
    # the cache at all, regardless of the stale key -- a pure round-robin
    # cycle longer than the cache is LRU's own classic worst case, so its
    # usual advantage over LFU disappears, not because LFU improved, but
    # because LRU collapsed to meet it.
    ops_high = build_staleness_workload(stale_bursts=50, cycle_keys=8, rounds=15)
    lfu_high = run_workload(LFUCache(capacity), ops_high)
    lru_high = run_workload(SimpleLRU(capacity), ops_high)
    assert lfu_high == pytest.approx(lru_high, abs=0.05)
    assert lru_high < 0.4  # LRU itself is now doing badly, not just "no longer better"

    # Exactly at the boundary (cycle_keys == capacity): this is the one
    # regime where reclaiming the stale slot is the entire difference
    # between the working set fitting and not fitting.
    ops_boundary = build_staleness_workload(stale_bursts=50, cycle_keys=capacity, rounds=20)
    lfu_boundary = run_workload(LFUCache(capacity), ops_boundary)
    lru_boundary = run_workload(SimpleLRU(capacity), ops_boundary)
    assert lru_boundary - lfu_boundary > 0.5
