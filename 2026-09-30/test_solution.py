import random

import pytest

from solution import sliding_window_maximum, sliding_window_maximum_naive


# --- basic behaviour -------------------------------------------------------------


def test_k_equals_1_returns_the_array_itself():
    assert sliding_window_maximum([4, 1, 7, 3], 1) == [4, 1, 7, 3]


def test_k_equals_n_returns_single_global_max():
    assert sliding_window_maximum([4, 1, 7, 3], 4) == [7]


def test_classic_example():
    # LeetCode's canonical example, hand-verified window by window.
    nums = [1, 3, -1, -3, 5, 3, 6, 7]
    assert sliding_window_maximum(nums, 3) == [3, 3, 5, 5, 6, 7]


def test_strictly_increasing_array():
    # Each window's max is always its rightmost (newest) element.
    assert sliding_window_maximum([1, 2, 3, 4, 5], 2) == [2, 3, 4, 5]


def test_strictly_decreasing_array():
    # Each window's max is always its leftmost (oldest, about to expire) element.
    assert sliding_window_maximum([5, 4, 3, 2, 1], 2) == [5, 4, 3, 2]


def test_all_equal_values():
    assert sliding_window_maximum([7, 7, 7, 7], 2) == [7, 7, 7]


def test_duplicates_of_the_max_stay_valid_after_the_first_expires():
    # The max (9) appears twice; once the first copy expires, the second
    # must still be found -- this fails if evicting equal values from the
    # back also discards them from future consideration incorrectly, or if
    # the front-eviction check is off by one.
    nums = [9, 1, 9, 1, 1]
    assert sliding_window_maximum(nums, 2) == [9, 9, 9, 1]


def test_negative_numbers():
    assert sliding_window_maximum([-4, -2, -8, -1, -5], 3) == [-2, -1, -1]


def test_single_element_array_k_1():
    assert sliding_window_maximum([42], 1) == [42]


def test_rejects_non_positive_k():
    with pytest.raises(ValueError):
        sliding_window_maximum([1, 2, 3], 0)
    with pytest.raises(ValueError):
        sliding_window_maximum([1, 2, 3], -1)


def test_rejects_k_larger_than_array():
    with pytest.raises(ValueError):
        sliding_window_maximum([1, 2, 3], 4)


def test_naive_reference_agrees_on_classic_example():
    nums = [1, 3, -1, -3, 5, 3, 6, 7]
    assert sliding_window_maximum_naive(nums, 3) == [3, 3, 5, 5, 6, 7]


def test_naive_reference_rejects_same_invalid_inputs():
    with pytest.raises(ValueError):
        sliding_window_maximum_naive([1, 2, 3], 0)
    with pytest.raises(ValueError):
        sliding_window_maximum_naive([1, 2, 3], 5)


# --- differential testing against the naive O(nk) reference -----------------------


@pytest.mark.parametrize("seed", range(30))
def test_matches_naive_on_random_arrays(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 200)
    nums = [rng.randint(-50, 50) for _ in range(n)]
    k = rng.randint(1, n)
    assert sliding_window_maximum(nums, k) == sliding_window_maximum_naive(nums, k)


@pytest.mark.parametrize("seed", range(10))
def test_matches_naive_on_arrays_with_heavy_duplication(seed):
    # A small value range forces lots of ties, which is exactly where
    # eviction-order bugs (evicting on < instead of <=, or vice versa) show up.
    rng = random.Random(seed)
    n = rng.randint(1, 200)
    nums = [rng.randint(0, 3) for _ in range(n)]
    k = rng.randint(1, n)
    assert sliding_window_maximum(nums, k) == sliding_window_maximum_naive(nums, k)


def test_result_length_matches_number_of_windows():
    rng = random.Random(0)
    for _ in range(20):
        n = rng.randint(1, 100)
        nums = [rng.randint(-10, 10) for _ in range(n)]
        k = rng.randint(1, n)
        assert len(sliding_window_maximum(nums, k)) == n - k + 1
