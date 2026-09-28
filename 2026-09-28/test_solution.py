import functools
import math
import os
import random
import statistics
import subprocess
import sys

import pytest

from solution import (
    BloomFilter,
    GuardedStore,
    estimate_cardinality,
    expected_fp_rate,
    optimal_params,
    wilson_interval,
)

Z999 = 3.29  # 99.9% two-sided: a correct implementation has a 1-in-1000 chance of tripping this by luck


def member_keys(n, tag="member"):
    return [f"{tag}:{i}" for i in range(n)]


# --- sizing and formulas, checked by hand --------------------------------------------------


def test_optimal_params_match_textbook_values():
    assert optimal_params(1000, 0.01) == (9586, 7)  # ~9.6 bits/item, 7 hashes: the standard worked example
    m, k = optimal_params(1_000_000, 0.001)
    assert 14.37 < m / 1_000_000 < 14.38 and k == 10  # ~14.4 bits/item at 0.1%


def test_optimal_params_always_use_at_least_one_hash_and_reject_nonsense():
    assert optimal_params(10, 0.9)[1] >= 1
    for capacity, p in ((0, 0.01), (-5, 0.01), (10, 0.0), (10, 1.0), (10, -0.1), (10, 2.0)):
        with pytest.raises(ValueError):
            optimal_params(capacity, p)


def test_constructor_validation():
    for m, k, hashing in ((0, 3, "double"), (10, 0, "double"), (10, 3, "bogus")):
        with pytest.raises(ValueError):
            BloomFilter(m, k, hashing)


def test_expected_fp_rate_by_hand():
    assert expected_fp_rate(8, 1, 1) == pytest.approx(0.125)  # one bit of eight set
    assert expected_fp_rate(8, 2, 1) == pytest.approx((1 - (7 / 8) ** 2) ** 2)  # 0.0549...
    assert expected_fp_rate(1000, 3, 0) == 0.0  # nothing inserted, nothing to false-match


def test_estimate_cardinality_edges_and_a_hand_value():
    assert estimate_cardinality(100, 1, 0) == 0.0
    assert estimate_cardinality(100, 3, 100) == math.inf  # completely full: no information left
    assert estimate_cardinality(100, 1, 63) == pytest.approx(-100 * math.log(1 - 0.63))


def test_wilson_interval_known_values_and_behaviour_near_zero():
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    lo0, hi0 = wilson_interval(0, 1000)
    assert lo0 == 0.0 and 0.0 < hi0 < 0.01  # zero observed successes still leaves a non-degenerate upper bound
    assert wilson_interval(100, 100)[1] == pytest.approx(1.0)  # exactly 1 in exact arithmetic; floats land an ulp short
    assert wilson_interval(0, 0) == (0.0, 1.0)


# --- core behaviour --------------------------------------------------------------------------


@pytest.mark.parametrize("hashing", ["double", "independent", "enhanced"])
def test_empty_filter_contains_nothing_and_added_items_are_found(hashing):
    f = BloomFilter.from_capacity(100, 0.01, hashing)
    assert not any(k in f for k in member_keys(200))
    f.add("hello")
    assert "hello" in f


def test_str_and_bytes_are_the_same_item():
    f = BloomFilter.from_capacity(100, 0.01)
    f.add("héllo")
    assert "héllo".encode("utf-8") in f
    f.add(b"raw-bytes")
    assert "raw-bytes" in f


@pytest.mark.parametrize("hashing", ["double", "independent", "enhanced"])
@pytest.mark.parametrize("m,k", [(1, 1), (7, 3), (13, 5), (64, 4), (1024, 7), (9586, 7), (5000, 20)])
def test_never_a_false_negative_at_any_size(m, k, hashing):
    f = BloomFilter(m, k, hashing)
    keys = member_keys(300)
    f.update(keys)
    assert all(key in f for key in keys)  # the one guarantee that is never allowed to fail, even when the filter is saturated


def test_adding_an_item_twice_changes_nothing():
    f, g = BloomFilter(2000, 5), BloomFilter(2000, 5)
    f.add("x")
    g.add("x")
    g.add("x")
    assert f._bits == g._bits and "x" in g  # OR is idempotent; XOR would have switched the bits back off


def test_partial_last_byte_never_has_bits_set_beyond_m():
    f = BloomFilter(13, 5)  # 2 bytes, only 13 bits are real
    f.update(member_keys(500))
    assert f.set_bits() <= 13
    assert f._bits[1] >> 5 == 0  # bits 13..15 of the last byte stay clear


def test_probe_indexes_are_in_range_and_k_of_them():
    for hashing in ("double", "independent"):
        f = BloomFilter(1000, 6, hashing)
        for key in member_keys(200):
            idx = f._indexes(key.encode())
            assert len(idx) == 6 and all(0 <= i < 1000 for i in idx)


def test_double_hashing_probes_are_distinct_when_m_is_a_power_of_two():
    """h2 is forced odd, so it is coprime with a power-of-two m and the k
    probes can never collapse onto fewer bits. An even h2 would sometimes
    have a large common factor with m and revisit the same few bits."""
    f = BloomFilter(1024, 7, "double")
    collapsed = sum(1 for key in member_keys(20_000) if len(set(f._indexes(key.encode()))) < 7)
    assert collapsed == 0


def test_enhanced_probes_are_double_hashing_plus_the_exact_cubic_term():
    dbl, enh = BloomFilter(4096, 9, "double"), BloomFilter(4096, 9, "enhanced")
    for key in member_keys(50):
        d, e = dbl._indexes(key.encode()), enh._indexes(key.encode())
        assert e == [(d[i] + (i**3 - i) // 6) % 4096 for i in range(9)]
        assert e[:2] == d[:2]  # the term is 0 for i=0 and i=1, so the first two probes coincide


def test_fill_ratio_and_set_bits_are_consistent():
    f = BloomFilter(800, 4)
    assert f.set_bits() == 0 and f.fill_ratio() == 0.0
    f.update(member_keys(100))
    assert f.set_bits() == sum(bin(b).count("1") for b in f._bits)
    assert f.fill_ratio() == pytest.approx(f.set_bits() / 800)
    assert f.memory_bytes == 100


# --- determinism across processes -----------------------------------------------------------------


def _bits_digest_in_subprocess(hashseed):
    code = (
        "import hashlib\n"
        "from solution import BloomFilter\n"
        "f = BloomFilter.from_capacity(500, 0.01)\n"
        "f.update(f'member:{i}' for i in range(500))\n"
        "print(hashlib.sha1(f.to_bytes()).hexdigest())\n"
        "print(hash('member:1'))\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60, check=True,
                         cwd=os.path.dirname(os.path.abspath(__file__))).stdout.splitlines()
    return out[0], out[1]


def test_filter_bits_are_identical_across_processes_despite_per_process_str_hash_salt():
    runs = [_bits_digest_in_subprocess(seed) for seed in ("1", "2", "12345")]
    assert len({hash_value for _, hash_value in runs}) == 3  # control: Python's own str hash really does differ here
    assert len({digest for digest, _ in runs}) == 1  # ...yet the filter built in each process is byte-for-byte the same


# --- union and persistence ----------------------------------------------------------------------------


@pytest.mark.parametrize("hashing", ["double", "independent", "enhanced"])
def test_union_equals_the_filter_built_from_both_item_sets(hashing):
    keys = member_keys(400)
    a, b, both = (BloomFilter.from_capacity(400, 0.01, hashing) for _ in range(3))
    a.update(keys[:150])
    b.update(keys[150:])
    both.update(keys)
    u = a.union(b)
    assert u._bits == both._bits  # exact, not merely "similar"
    assert all(k in u for k in keys)
    assert a.union(a)._bits == a._bits and a.union(b)._bits == b.union(a)._bits  # idempotent and commutative


def test_union_does_not_mutate_its_operands():
    a, b = BloomFilter(500, 3), BloomFilter(500, 3)
    a.add("only-a")
    b.add("only-b")
    before_a, before_b = bytes(a._bits), bytes(b._bits)
    a.union(b)
    assert bytes(a._bits) == before_a and bytes(b._bits) == before_b


def test_union_rejects_filters_with_different_parameters():
    base = BloomFilter(500, 3, "double")
    for other in (BloomFilter(501, 3, "double"), BloomFilter(500, 4, "double"), BloomFilter(500, 3, "independent"), BloomFilter(500, 3, "enhanced")):
        with pytest.raises(ValueError):
            base.union(other)


@pytest.mark.parametrize("hashing", ["double", "independent", "enhanced"])
def test_serialization_round_trip_preserves_everything(hashing):
    f = BloomFilter.from_capacity(300, 0.01, hashing)
    f.update(member_keys(300))
    g = BloomFilter.from_bytes(f.to_bytes())
    assert (g.m, g.k, g.hashing) == (f.m, f.k, f.hashing) and g._bits == f._bits
    assert all(k in g for k in member_keys(300))


def test_from_bytes_rejects_corrupt_input():
    good = BloomFilter(100, 3).to_bytes()
    with pytest.raises(ValueError, match="truncated"):
        BloomFilter.from_bytes(good[:5])
    with pytest.raises(ValueError, match="magic"):
        BloomFilter.from_bytes(b"XXXX" + good[4:])
    with pytest.raises(ValueError, match="payload"):
        BloomFilter.from_bytes(good[:-1])
    with pytest.raises(ValueError, match="payload"):
        BloomFilter.from_bytes(good + b"\x00")
    bad_scheme = bytearray(good)
    bad_scheme[14] = 9  # the hashing-scheme byte
    with pytest.raises(ValueError, match="scheme"):
        BloomFilter.from_bytes(bytes(bad_scheme))


# --- the quantitative claims, against their formulas ---------------------------------------------------------


def _observed(f, probes=100_000):
    fp = sum(1 for j in range(probes) if f"other:{j}" in f)
    return fp, probes


@pytest.mark.parametrize("hashing", ["double", "independent", "enhanced"])
@pytest.mark.parametrize("p", [0.1, 0.01, 0.001])
def test_observed_false_positive_rate_matches_the_exact_formula(p, hashing):
    n = 20_000
    f = BloomFilter.from_capacity(n, p, hashing)
    f.update(member_keys(n))
    lo, hi = wilson_interval(*_observed(f, 200_000), z=Z999)
    assert lo <= expected_fp_rate(f.m, f.k, n) <= hi
    # optimally sized => about half the bits are set; k is rounded to an integer, which at p=10% (k=3, ideal 3.3) leaves fill at ~0.47
    assert f.fill_ratio() == pytest.approx(0.5, abs=0.05)


def test_false_positive_rate_degrades_as_the_filter_is_overfilled_exactly_as_the_formula_says():
    n0 = 10_000
    f = BloomFilter.from_capacity(n0, 0.01)
    inserted, rates = 0, []
    for mult in (1, 2, 4):
        f.update(member_keys(n0 * mult)[inserted:])
        inserted = n0 * mult
        lo, hi = wilson_interval(*_observed(f, 100_000), z=Z999)
        assert lo <= expected_fp_rate(f.m, f.k, inserted) <= hi
        rates.append(lo)
    assert rates[0] < rates[1] < rates[2] and rates[2] > 0.3  # 4x the design load: a quarter of lookups are wrong


def test_estimated_count_tracks_distinct_items_not_number_of_adds():
    n = 20_000
    f = BloomFilter.from_capacity(n, 0.01)
    f.update(member_keys(n))
    assert f.estimated_count() == pytest.approx(n, rel=0.03)
    before = f.set_bits()
    f.update(member_keys(n))  # the same items again: nothing new was learned
    assert f.set_bits() == before and f.estimated_count() == pytest.approx(n, rel=0.03)


def test_estimated_fp_rate_from_fill_agrees_with_what_is_actually_observed():
    n = 20_000
    f = BloomFilter.from_capacity(n, 0.01)
    f.update(member_keys(n))
    lo, hi = wilson_interval(*_observed(f, 200_000), z=Z999)
    assert lo <= f.estimated_fp_rate() <= hi


def test_double_and_independent_hashing_are_statistically_indistinguishable_at_realistic_sizes():
    n = 20_000
    rates, cis = {}, {}
    for hashing in ("double", "independent"):
        f = BloomFilter.from_capacity(n, 0.01, hashing)
        f.update(member_keys(n))
        fp, probes = _observed(f, 300_000)
        rates[hashing] = fp / probes
    sigma = math.sqrt(2 * 0.01 * 0.99 / 300_000)
    assert abs(rates["double"] - rates["independent"]) < Z999 * sigma


@functools.lru_cache(maxsize=None)
def _mean_and_se(m, k, items, hashing, filters=60, probes=10_000):
    rates = []
    for t in range(filters):
        g = BloomFilter(m, k, hashing)
        g.update(f"m{t}:{i}" for i in range(items))
        rates.append(sum(1 for j in range(probes) if f"x{t}:{j}" in g) / probes)
    return statistics.mean(rates), statistics.stdev(rates) / math.sqrt(filters)


def test_at_tiny_sizes_double_hashing_is_measurably_worse_but_independent_hashing_is_not():
    """Kirsch-Mitzenmacher equivalence is asymptotic: at m=128 the plain
    double-hashing false-positive rate runs well above the formula."""
    m, k, items = 128, 4, 12
    theory = expected_fp_rate(m, k, items)
    ind_mean, ind_se = _mean_and_se(m, k, items, "independent")
    dbl_mean, dbl_se = _mean_and_se(m, k, items, "double")
    assert abs(ind_mean - theory) < Z999 * ind_se + 0.0005  # independent hashing follows the formula even here
    assert dbl_mean - ind_mean > Z999 * math.sqrt(ind_se**2 + dbl_se**2)  # double hashing measurably does not


@pytest.mark.parametrize("m,k,items", [(128, 4, 12), (256, 5, 25)])
def test_enhanced_double_hashing_removes_most_of_the_small_size_excess(m, k, items):
    """Same one-digest cost as double hashing; the cubic term breaks the
    arithmetic-progression structure that correlates the probes."""
    theory = expected_fp_rate(m, k, items)
    dbl_mean, dbl_se = _mean_and_se(m, k, items, "double")
    enh_mean, enh_se = _mean_and_se(m, k, items, "enhanced")
    assert dbl_mean - enh_mean > Z999 * math.sqrt(dbl_se**2 + enh_se**2)  # measurably better than plain double hashing
    assert (dbl_mean - theory) > 2 * (enh_mean - theory)  # and closes at least half the gap to the formula


# --- the application: a filter shielding a database ---------------------------------------------------------------


def test_guarded_store_never_returns_a_wrong_answer():
    records = {f"user:{i}": f"row{i}" for i in range(2000)}
    store = GuardedStore(records, fp_rate=0.05)
    rng = random.Random(3)
    for _ in range(5000):
        key = f"user:{rng.randrange(4000)}"  # half exist, half don't
        assert store.get(key) == records.get(key)


def test_guarded_store_sends_every_real_key_to_the_database_and_few_ghosts():
    records = {f"user:{i}": f"row{i}" for i in range(5000)}
    store = GuardedStore(records, fp_rate=0.01)
    for i in range(5000):
        store.get(f"user:{i}")
    assert store.db_lookups == 5000  # a real key can never be filtered out
    ghosts = 100_000
    for j in range(ghosts):
        store.get(f"ghost:{j}")
    wasted = store.db_lookups - 5000
    lo, hi = wilson_interval(wasted, ghosts, z=Z999)
    assert lo <= 0.01 <= hi  # about 1% of unknown keys still reach the database, as sized


def test_guarded_store_with_no_records_still_works():
    store = GuardedStore({}, fp_rate=0.01)
    assert store.get("anything") is None and store.db_lookups == 0
