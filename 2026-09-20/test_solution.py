import random
import sys
import threading

import pytest

from solution import LRUCache, NaiveLRU


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# --- basic behavior -------------------------------------------------------------


def test_get_put_and_miss_default():
    c = LRUCache(2)
    c.put("a", 1)
    assert c.get("a") == 1
    assert c.get("missing") is None
    assert c.get("missing", "fallback") == "fallback"


def test_none_is_a_storable_value_distinct_from_a_miss():
    c = LRUCache(2)
    c.put("k", None)
    sentinel = object()
    assert c.get("k", sentinel) is None  # a hit that happens to hold None, not the default
    assert c.get("nope", sentinel) is sentinel


def test_evicts_least_recently_used():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    assert c.keys() == ["c", "b"]
    assert "a" not in c


def test_get_refreshes_recency():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")
    c.put("c", 3)  # b is now the LRU
    assert "a" in c and "c" in c and "b" not in c


def test_put_existing_key_updates_and_refreshes_without_evicting():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)
    assert len(c) == 2
    assert c.get("a") == 10
    assert c.keys()[0] == "a"
    assert c.stats()["evictions"] == 0


def test_capacity_one():
    c = LRUCache(1)
    c.put("a", 1)
    c.put("b", 2)
    assert c.keys() == ["b"]
    c.check_invariants()


def test_delete():
    c = LRUCache(3)
    c.put("a", 1)
    assert c.delete("a") is True
    assert c.delete("a") is False
    assert len(c) == 0
    c.check_invariants()


def test_contains_is_read_only_and_does_not_refresh_recency():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    assert "a" in c  # must NOT make a most-recent
    c.put("c", 3)
    assert "a" not in c  # a was still the LRU, so it was the one evicted


@pytest.mark.parametrize("bad", [0, -1])
def test_rejects_non_positive_capacity(bad):
    with pytest.raises(ValueError):
        LRUCache(bad)


def test_rejects_non_positive_ttls():
    with pytest.raises(ValueError):
        LRUCache(2, default_ttl=0)
    c = LRUCache(2)
    with pytest.raises(ValueError):
        c.put("a", 1, ttl=-1)


def test_invalid_ttl_on_update_leaves_existing_entry_untouched():
    c = LRUCache(2)
    c.put("a", 1)
    with pytest.raises(ValueError):
        c.put("a", 999, ttl=0)
    assert c.get("a") == 1  # validation happens before any mutation


# --- TTL --------------------------------------------------------------------------


def test_entry_alive_just_before_expiry_and_gone_exactly_at_it():
    clock = FakeClock()
    c = LRUCache(4, clock=clock)
    c.put("k", "v", ttl=5)
    clock.now = 4.999
    assert c.get("k") == "v"
    clock.now = 5.0
    assert c.get("k") is None


def test_expired_read_counts_as_miss_and_expiration_and_removes_entry():
    clock = FakeClock()
    c = LRUCache(4, clock=clock)
    c.put("k", "v", ttl=1)
    clock.now = 2
    assert c.get("k") is None
    s = c.stats()
    assert (s["misses"], s["expirations"], s["hits"], s["size"]) == (1, 1, 0, 0)


def test_default_ttl_applies_and_per_put_ttl_overrides_it():
    clock = FakeClock()
    c = LRUCache(4, default_ttl=10, clock=clock)
    c.put("default", 1)
    c.put("short", 2, ttl=1)
    clock.now = 5
    assert c.get("short") is None
    assert c.get("default") == 1
    clock.now = 10
    assert c.get("default") is None


def test_put_replaces_expiry_entirely_including_clearing_it():
    clock = FakeClock()
    c = LRUCache(4, clock=clock)  # no default ttl
    c.put("k", 1, ttl=1)
    c.put("k", 2)  # no ttl -> no expiry now
    clock.now = 1000
    assert c.get("k") == 2


def test_expired_lru_victim_is_counted_as_expiration_not_eviction():
    clock = FakeClock()
    c = LRUCache(2, clock=clock)
    c.put("old", 1, ttl=1)
    c.put("b", 2)
    clock.now = 5
    c.put("c", 3)  # over capacity; the LRU victim ("old") is already expired
    s = c.stats()
    assert s["expirations"] == 1 and s["evictions"] == 0


def test_purge_expired_removes_only_expired_and_reports_count():
    clock = FakeClock()
    c = LRUCache(10, clock=clock)
    c.put("a", 1, ttl=1)
    c.put("b", 2, ttl=100)
    c.put("c", 3, ttl=1)
    c.put("d", 4)
    clock.now = 10
    assert len(c) == 4  # lazy: nothing removed yet
    assert c.purge_expired() == 2
    assert sorted(c.keys()) == ["b", "d"]
    c.check_invariants()


def test_contains_is_false_for_expired_entry():
    clock = FakeClock()
    c = LRUCache(2, clock=clock)
    c.put("k", 1, ttl=1)
    clock.now = 2
    assert "k" not in c


# --- differential test against the O(n) oracle ---------------------------------------


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("capacity, default_ttl", [(1, None), (3, None), (5, 4), (12, None)])
def test_matches_naive_reference_across_random_operation_sequences(seed, capacity, default_ttl):
    clock = FakeClock()
    ours = LRUCache(capacity, default_ttl, clock)
    ref = NaiveLRU(capacity, default_ttl, clock)
    rng = random.Random(seed * 1000 + capacity)
    miss = object()

    for step in range(1500):
        op = rng.choice(["put", "put", "get", "get", "get", "delete", "contains", "advance", "purge"])
        key = rng.randrange(15)
        if op == "put":
            ttl = rng.choice([None, None, 1, 3, 7])
            ours.put(key, step, ttl)
            ref.put(key, step, ttl)
        elif op == "get":
            assert ours.get(key, miss) == ref.get(key, miss), f"get({key}) diverged at step {step}"
        elif op == "delete":
            assert ours.delete(key) == ref.delete(key)
        elif op == "contains":
            assert (key in ours) == ref.contains(key)
        elif op == "advance":
            clock.now += rng.choice([0.5, 1, 2, 5])
        else:
            assert ours.purge_expired() == ref.purge_expired()
        assert ours.keys() == ref.keys(), f"recency order diverged after {op} at step {step}"

    ours.check_invariants()


# --- thread safety --------------------------------------------------------------------


@pytest.fixture
def tiny_switch_interval():
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # force frequent thread switches so races actually surface
    yield
    sys.setswitchinterval(old)


def _hammer(cache, threads=8, ops=8000):
    """Runs a mixed put/get workload from several threads and returns the
    exceptions any worker hit. Workers catch their own exceptions instead
    of letting them escape: with many threads crashing at once, pytest's own
    thread-exception hook races on linecache while formatting tracebacks and
    fails the test for reasons unrelated to the cache."""
    errors = []

    def worker(seed):
        rng = random.Random(seed)
        try:
            for _ in range(ops):
                k = rng.randrange(200)
                if rng.random() < 0.5:
                    cache.put(k, k * 2)
                else:
                    cache.get(k)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)  # list.append is atomic under the GIL

    ts = [threading.Thread(target=worker, args=(s,)) for s in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return errors


def test_concurrent_mixed_operations_preserve_invariants(tiny_switch_interval):
    cache = LRUCache(50)
    assert _hammer(cache) == []  # no worker crashed
    cache.check_invariants()
    assert len(cache) <= 50


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_negative_control_the_same_hammer_corrupts_an_unlocked_cache(tiny_switch_interval):
    """Proves the test above has teeth: with the lock removed, the identical
    workload must corrupt the structure (a worker crashing mid-operation, or
    dangling links / size mismatch afterwards). Stops at the first corrupted
    trial, so it is usually fast; the trial budget exists because a race is
    probabilistic and one lucky trial must not fail the suite."""
    for _ in range(40):
        cache = LRUCache(50)
        cache._lock = _NoLock()
        errors = _hammer(cache, ops=20000)
        if errors:
            return  # a worker crashed on a corrupted structure
        try:
            cache.check_invariants()
        except Exception:
            return  # structure left inconsistent
    pytest.fail("unlocked cache survived 40 hammer trials; the concurrency test may not be exercising races")
