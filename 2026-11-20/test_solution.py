import pytest

from solution import ManagedResource, correct_resource, leaky_resource


# --- happy path: all three are indistinguishable ---------------------------


def test_happy_path_identical_log_for_all_three_implementations():
    log_a: list = []
    with ManagedResource(log_a, "x") as r:
        pass
    assert log_a == ["open:x", "close:x"]

    log_b: list = []
    with correct_resource(log_b, "x") as r:
        pass
    assert log_b == ["open:x", "close:x"]

    log_c: list = []
    with leaky_resource(log_c, "x") as r:
        pass
    assert log_c == ["open:x", "close:x"]


# --- exception path, no suppression: cleanup guaranteed for the two ------
# correct implementations, but not for the leaky one ----------------------


def test_managed_resource_guarantees_cleanup_and_propagates_exception():
    log: list = []
    with pytest.raises(ValueError):
        with ManagedResource(log, "x") as r:
            raise ValueError("boom")
    assert log == ["open:x", "close:x"]


def test_correct_resource_guarantees_cleanup_and_propagates_exception():
    log: list = []
    with pytest.raises(ValueError):
        with correct_resource(log, "x") as r:
            raise ValueError("boom")
    assert log == ["open:x", "close:x"]


def test_leaky_resource_propagates_exception_but_misses_cleanup():
    log: list = []
    with pytest.raises(ValueError):
        with leaky_resource(log, "x") as r:
            raise ValueError("boom")
    # the exception still propagates correctly -- only cleanup is lost
    assert log == ["open:x"]


# --- suppression: blanket, not type-specific, for either protocol --------


def test_managed_resource_suppresses_exception_when_suppress_true():
    log: list = []
    with ManagedResource(log, "x", suppress=True) as r:
        raise ValueError("boom")
    assert log == ["open:x", "close:x"]


def test_correct_resource_suppresses_exception_when_suppress_true():
    log: list = []
    with correct_resource(log, "x", suppress=True) as r:
        raise ValueError("boom")
    assert log == ["open:x", "close:x"]


def test_managed_resource_suppress_true_swallows_unrelated_exception_types_too():
    for exc in (ValueError("a"), TypeError("b")):
        log: list = []
        with ManagedResource(log, "x", suppress=True):
            raise exc
        assert log == ["open:x", "close:x"]


def test_correct_resource_suppress_true_swallows_unrelated_exception_types_too():
    for exc in (ValueError("a"), TypeError("b")):
        log: list = []
        with correct_resource(log, "x", suppress=True):
            raise exc
        assert log == ["open:x", "close:x"]


# --- reusability is not symmetric between the two protocols ---------------


def test_class_based_context_manager_instance_is_reusable_across_with_blocks():
    log: list = []
    resource = ManagedResource(log, "x")
    with resource:
        pass
    with resource:
        pass
    assert log == ["open:x", "close:x", "open:x", "close:x"]


def test_generator_based_context_manager_object_cannot_be_reused():
    log: list = []
    cm = correct_resource(log, "x")
    with cm:
        pass
    assert log == ["open:x", "close:x"]

    with pytest.raises(Exception):
        with cm:
            pass

    # the failed second attempt never re-ran the resource's own open logic
    assert log == ["open:x", "close:x"]
