from solution import FenwickTree, PrecomputedPrefixArray, UnindexedArray

POWERS_OF_TWO = (16, 64, 256, 1024, 4096)


def floor_log2(n: int) -> int:
    return n.bit_length() - 1  # exact for n a power of two


# --- correctness: all three agree, independent of performance -----------


def _build_all(values):
    fen = FenwickTree(len(values))
    for i, v in enumerate(values):
        fen.update(i, v)
    return fen, PrecomputedPrefixArray(values), UnindexedArray(values)


def test_all_three_agree_on_initial_range_sums():
    values = [3, 1, 4, 1, 5, 9, 2, 6]
    fen, pre, unindexed = _build_all(values)
    assert fen.range_sum(0, 7) == pre.range_sum(0, 7) == unindexed.range_sum(0, 7) == 31
    assert fen.range_sum(2, 5) == pre.range_sum(2, 5) == unindexed.range_sum(2, 5) == 19
    assert fen.range_sum(0, 0) == pre.range_sum(0, 0) == unindexed.range_sum(0, 0) == 3


def test_all_three_agree_after_updates():
    values = [3, 1, 4, 1, 5, 9, 2, 6]
    fen, pre, unindexed = _build_all(values)
    for structure in (fen, pre, unindexed):
        structure.update(3, 10)  # values[3]: 1 -> 11
        structure.update(0, -3)  # values[0]: 3 -> 0
    assert fen.range_sum(0, 7) == pre.range_sum(0, 7) == unindexed.range_sum(0, 7) == 38
    assert fen.range_sum(0, 3) == pre.range_sum(0, 3) == unindexed.range_sum(0, 3) == 16


def test_prefix_sum_of_negative_index_is_zero():
    fen = FenwickTree(8)
    fen.update(0, 100)
    assert fen.prefix_sum(-1) == 0
    assert fen.range_sum(0, 0) == 100


# --- exact, formula-matching op counts (worst-case positions) -----------


def test_update_at_index_zero_costs_exactly_floor_log2_plus_one():
    for n in POWERS_OF_TWO:
        fen = FenwickTree(n)
        fen.update(0, 5)
        assert fen.op_count == floor_log2(n) + 1, f"n={n}"


def test_prefix_sum_at_n_minus_two_costs_exactly_floor_log2():
    for n in POWERS_OF_TWO:
        fen = FenwickTree(n)
        for i in range(n):
            fen.update(i, 1)
        fen.op_count = 0
        fen.prefix_sum(n - 2)
        assert fen.op_count == floor_log2(n), f"n={n}"


# --- the growth-curve measurement: fixed workload, varying size ----------

GROWTH_CURVE = {
    16: (40, 95, 90),
    64: (50, 335, 330),
    256: (70, 2255, 1290),
    1024: (90, 9935, 5130),
    4096: (110, 40655, 20490),
    16384: (130, 163535, 81930),
}


def _run_fixed_workload(n):
    values = [1] * n
    fen, pre, unindexed = _build_all(values)
    fen.op_count = 0  # measure the workload only, not construction

    k = 10
    for i in range(k):
        idx = (i * 7) % n
        delta = i + 1
        fen.update(idx, delta)
        pre.update(idx, delta)
        unindexed.update(idx, delta)

        left = 0
        right = n // 2 - 1
        fen.range_sum(left, right)
        pre.range_sum(left, right)
        unindexed.range_sum(left, right)

    return fen.op_count, pre.op_count, unindexed.op_count


def test_growth_curve_matches_measured_values_exactly():
    for n, expected in GROWTH_CURVE.items():
        assert _run_fixed_workload(n) == expected, f"n={n}"


def test_fenwick_total_grows_far_slower_than_either_naive_structure():
    smallest_n, largest_n = 16, 16384
    fen_small, pre_small, unindexed_small = _run_fixed_workload(smallest_n)
    fen_large, pre_large, unindexed_large = _run_fixed_workload(largest_n)

    # size grew 1024x; fenwick's op_count grew far less than 4x
    assert fen_large < fen_small * 4
    # the naive structures grew roughly in step with size (close to 1024x)
    assert pre_large > pre_small * 1000
    assert unindexed_large > unindexed_small * 500


def test_at_largest_size_fenwick_beats_both_naive_structures_by_orders_of_magnitude():
    fen, pre, unindexed = _run_fixed_workload(16384)
    assert fen * 100 < pre
    assert fen * 100 < unindexed
