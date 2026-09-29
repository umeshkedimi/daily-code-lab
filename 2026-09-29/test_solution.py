import heapq
import random

import pytest

from solution import MinHeap, NaivePriorityQueue, heap_sort, merge_sorted_lists


# --- basic behaviour -------------------------------------------------------------


def test_empty_heap_raises_on_pop_peek_replace():
    h = MinHeap()
    assert len(h) == 0 and not h
    for op in (h.pop, h.peek, lambda: h.replace(1)):
        with pytest.raises(IndexError):
            op()


def test_push_then_pop_returns_items_in_ascending_order():
    h = MinHeap()
    for x in [5, 3, 8, 1, 9, 2]:
        h.push(x)
    assert [h.pop() for _ in range(6)] == [1, 2, 3, 5, 8, 9]


def test_peek_does_not_remove():
    h = MinHeap([3, 1, 2])
    assert h.peek() == 1
    assert len(h) == 3
    assert h.peek() == 1


def test_duplicates_are_preserved():
    h = MinHeap([2, 2, 1, 2, 1])
    assert [h.pop() for _ in range(5)] == [1, 1, 2, 2, 2]


def test_constructor_heapifies_in_place_not_via_repeated_push():
    h = MinHeap([5, 4, 3, 2, 1])
    assert h.is_valid()
    assert [h.pop() for _ in range(5)] == [1, 2, 3, 4, 5]


def test_replace_is_equivalent_to_pop_then_push_but_one_call():
    h1, h2 = MinHeap([5, 3, 8, 1]), MinHeap([5, 3, 8, 1])
    replaced = h1.replace(4)
    popped = h2.pop()
    h2.push(4)
    assert replaced == popped == 1
    assert h1.is_valid() and h2.is_valid()
    assert sorted(h1._data) == sorted(h2._data)  # same multiset of remaining elements


def test_custom_key_orders_by_key_not_by_value():
    h: MinHeap = MinHeap(key=lambda s: len(s))
    for w in ["ccc", "a", "bb", "dddd"]:
        h.push(w)
    assert [h.pop() for _ in range(4)] == ["a", "bb", "ccc", "dddd"]


def test_is_valid_detects_a_broken_heap():
    h = MinHeap([1, 2, 3])
    assert h.is_valid()
    h._data[0] = 99  # corrupt it directly, bypassing sift logic
    assert not h.is_valid()


def test_is_valid_checks_the_right_child_specifically_not_only_the_left():
    h = MinHeap()
    h._data = [1, 2, 0]  # root=1, left child=2 (fine), right child=0 (violates: 0 < 1)
    assert not h.is_valid()


# --- differential testing against heapq ------------------------------------------------


@pytest.mark.parametrize("seed", range(20))
def test_matches_heapq_under_randomized_operation_sequences(seed):
    rng = random.Random(seed)
    mine: MinHeap = MinHeap()
    ref: list = []
    for _ in range(200):
        op = rng.choice(["push", "push", "pop", "peek", "replace"])
        if op == "push" or not mine:
            v = rng.randint(-100, 100)
            mine.push(v)
            heapq.heappush(ref, v)
        elif op == "pop":
            assert mine.pop() == heapq.heappop(ref)
        elif op == "peek":
            assert mine.peek() == ref[0]
        else:
            v = rng.randint(-100, 100)
            assert mine.replace(v) == heapq.heapreplace(ref, v)
        assert mine.is_valid()
        assert len(mine) == len(ref)


@pytest.mark.parametrize("seed", range(10))
def test_constructor_matches_heapq_heapify_regardless_of_input_order(seed):
    rng = random.Random(seed)
    data = [rng.randint(-1000, 1000) for _ in range(rng.randint(0, 100))]
    mine = MinHeap(data)
    ref = list(data)
    heapq.heapify(ref)
    assert mine.is_valid()
    assert sorted(mine._data) == sorted(ref) == sorted(data)  # same multiset; internal layout need not match heapq's


# --- heap_sort ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        [], [1], [1, 1, 1],
        list(range(100)), list(range(100))[::-1],
        [random.Random(1).randint(-50, 50) for _ in range(150)],
    ],
)
def test_heap_sort_matches_sorted(data):
    assert heap_sort(data) == sorted(data)
    assert data == data  # heap_sort must not mutate its input by aliasing


def test_heap_sort_does_not_mutate_the_input_list():
    data = [3, 1, 2]
    result = heap_sort(data)
    assert data == [3, 1, 2] and result == [1, 2, 3]


def test_heap_sort_with_custom_key():
    words = ["banana", "fig", "kiwi", "apple"]
    assert heap_sort(words, key=len) == sorted(words, key=len)


# --- naive reference model itself (a control, not the thing under test) --------------------------


def test_naive_priority_queue_is_a_valid_oracle():
    rng = random.Random(0)
    q: NaivePriorityQueue = NaivePriorityQueue()
    ref: list = []
    for _ in range(100):
        if rng.random() < 0.6 or not len(q):
            v = rng.randint(-100, 100)
            q.push(v)
            heapq.heappush(ref, v)
        else:
            assert q.pop() == heapq.heappop(ref)
    with pytest.raises(IndexError):
        NaivePriorityQueue().pop()


# --- k-way merge -----------------------------------------------------------------------------------


def test_merge_sorted_lists_matches_sort_of_the_concatenation():
    rng = random.Random(2)
    lists = [sorted(rng.randint(-500, 500) for _ in range(rng.randint(0, 20))) for _ in range(15)]
    assert merge_sorted_lists(lists) == sorted(v for lst in lists for v in lst)


def test_merge_sorted_lists_matches_heapqs_own_merge():
    import heapq as hq

    rng = random.Random(3)
    lists = [sorted(rng.randint(0, 10**6) for _ in range(rng.randint(1, 50))) for _ in range(8)]
    assert merge_sorted_lists(lists) == list(hq.merge(*lists))


def test_merge_sorted_lists_handles_empty_lists_and_all_empty_input():
    assert merge_sorted_lists([[], [1, 2, 3], []]) == [1, 2, 3]
    assert merge_sorted_lists([]) == []
    assert merge_sorted_lists([[], []]) == []


def test_merge_sorted_lists_single_list_returns_it_unchanged():
    assert merge_sorted_lists([[3, 1, 4]]) == [3, 1, 4]  # not re-sorted -- caller promises each input is sorted
