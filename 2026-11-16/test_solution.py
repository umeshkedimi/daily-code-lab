import sys

from solution import (
    Regular,
    Slotted,
    SlottedChildOfPlain,
    allows_dynamic_attribute,
    instance_memory_bytes,
    total_memory_bytes,
)


def make_regular(i: int = 1) -> Regular:
    return Regular(i, i + 1, i + 2)


def make_slotted(i: int = 1) -> Slotted:
    return Slotted(i, i + 1, i + 2)


def make_child(i: int = 1) -> SlottedChildOfPlain:
    return SlottedChildOfPlain(i, i + 1, i + 2)


# --- instance_memory_bytes must account for a separate __dict__ ---------


def test_instance_memory_bytes_of_regular_includes_its_dict_separately():
    obj = make_regular()
    naive = sys.getsizeof(obj)
    correct = instance_memory_bytes(obj)
    assert correct == naive + sys.getsizeof(obj.__dict__)
    assert correct > naive


def test_instance_memory_bytes_of_slotted_has_no_separate_dict_to_add():
    obj = make_slotted()
    assert not hasattr(obj, "__dict__")
    assert instance_memory_bytes(obj) == sys.getsizeof(obj)


# --- the headline per-instance claim -------------------------------------


def test_slotted_instance_uses_strictly_less_memory_than_regular():
    assert instance_memory_bytes(make_slotted()) < instance_memory_bytes(make_regular())


# --- the saving is per instance, not a fixed one-time cost --------------


def test_aggregate_saving_scales_exactly_linearly_with_instance_count():
    n = 50
    regulars = [make_regular(i) for i in range(n)]
    slotteds = [make_slotted(i) for i in range(n)]
    single_gap = instance_memory_bytes(make_regular()) - instance_memory_bytes(make_slotted())
    aggregate_gap = total_memory_bytes(regulars) - total_memory_bytes(slotteds)
    assert single_gap > 0
    assert aggregate_gap == n * single_gap


def test_aggregate_saving_at_two_different_counts_is_proportional():
    small_n, large_n = 10, 40
    small_gap = total_memory_bytes([make_regular(i) for i in range(small_n)]) - total_memory_bytes(
        [make_slotted(i) for i in range(small_n)]
    )
    large_gap = total_memory_bytes([make_regular(i) for i in range(large_n)]) - total_memory_bytes(
        [make_slotted(i) for i in range(large_n)]
    )
    assert large_gap == (large_n // small_n) * small_gap


# --- subclassing a non-slotted base buys nothing -------------------------


def test_slotted_subclass_of_unslotted_base_still_has_a_dict():
    assert hasattr(make_child(), "__dict__")


def test_slotted_subclass_of_unslotted_base_gets_zero_memory_saving():
    assert instance_memory_bytes(make_child()) == instance_memory_bytes(make_regular())


# --- the flip side: dynamic attribute assignment -------------------------


def test_regular_allows_assigning_an_undeclared_attribute():
    assert allows_dynamic_attribute(make_regular()) is True


def test_slotted_rejects_assigning_an_undeclared_attribute():
    assert allows_dynamic_attribute(make_slotted()) is False


def test_slotted_child_of_unslotted_base_allows_it_too():
    # it inherited the base's __dict__, so the same door is open
    assert allows_dynamic_attribute(make_child()) is True


def test_allows_dynamic_attribute_leaves_no_probe_attribute_behind():
    obj = make_regular()
    before = set(vars(obj))
    allows_dynamic_attribute(obj)
    after = set(vars(obj))
    assert before == after
