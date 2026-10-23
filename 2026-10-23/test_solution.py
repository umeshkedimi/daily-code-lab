import pytest

from solution import CircuitBreaker, CircuitOpenError, State


class FakeClock:
    """Deterministic stand-in for time.monotonic; advanced explicitly."""

    def __init__(self, start: float = 0.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def ok():
    return "ok"


def boom():
    raise RuntimeError("boom")


def make_breaker(**overrides):
    defaults = dict(
        failure_threshold=0.5,
        min_requests=4,
        window_size=10,
        reset_timeout=10.0,
        half_open_max_calls=2,
        clock=FakeClock(),
    )
    defaults.update(overrides)
    return CircuitBreaker(**defaults), defaults["clock"]


def make_flaky(outage_length):
    """Fails for the first `outage_length` calls, then always succeeds."""

    def f():
        f.calls += 1
        if f.calls <= outage_length:
            raise RuntimeError("dependency down")
        return "ok"

    f.calls = 0
    return f


# --- basic state machine -----------------------------------------------


def test_starts_closed():
    cb, _ = make_breaker()
    assert cb.state is State.CLOSED


def test_successful_call_returns_value_and_stays_closed():
    cb, _ = make_breaker()
    assert cb.call(ok) == "ok"
    assert cb.state is State.CLOSED


def test_stays_closed_below_min_requests_even_if_all_fail():
    cb, _ = make_breaker(min_requests=4, window_size=10)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            cb.call(boom)
    assert cb.state is State.CLOSED


def test_trips_open_once_min_requests_and_failure_rate_exceeded():
    cb, _ = make_breaker(min_requests=4, window_size=10, failure_threshold=0.5)
    for _ in range(4):
        with pytest.raises(RuntimeError):
            cb.call(boom)
    assert cb.state is State.OPEN


def test_open_rejects_without_calling_func():
    cb, _ = make_breaker(min_requests=1, window_size=10, failure_threshold=0.5)
    with pytest.raises(RuntimeError):
        cb.call(boom)
    assert cb.state is State.OPEN

    calls = []

    def tracked():
        calls.append(1)
        return "ok"

    with pytest.raises(CircuitOpenError):
        cb.call(tracked)
    assert calls == []


def test_open_transitions_to_half_open_after_reset_timeout():
    cb, clock = make_breaker(
        min_requests=1, window_size=10, failure_threshold=0.5, reset_timeout=10.0
    )
    with pytest.raises(RuntimeError):
        cb.call(boom)
    assert cb.state is State.OPEN

    clock.advance(9.999)
    with pytest.raises(CircuitOpenError):
        cb.call(ok)
    assert cb.state is State.OPEN

    clock.advance(0.001)
    assert cb.call(ok) == "ok"
    assert cb.state in (State.HALF_OPEN, State.CLOSED)


def test_half_open_all_trials_succeed_closes_and_resets_window():
    cb, clock = make_breaker(
        min_requests=1,
        window_size=10,
        failure_threshold=0.5,
        reset_timeout=10.0,
        half_open_max_calls=2,
    )
    with pytest.raises(RuntimeError):
        cb.call(boom)
    clock.advance(10.0)

    assert cb.call(ok) == "ok"
    assert cb.state is State.HALF_OPEN
    assert cb.call(ok) == "ok"
    assert cb.state is State.CLOSED

    # window was reset on close: a single failure right after shouldn't trip
    with pytest.raises(RuntimeError):
        cb.call(boom)
    assert cb.state is State.CLOSED


def test_half_open_single_failure_reopens_and_resets_timer():
    cb, clock = make_breaker(
        min_requests=1,
        window_size=10,
        failure_threshold=0.5,
        reset_timeout=10.0,
        half_open_max_calls=2,
    )
    with pytest.raises(RuntimeError):
        cb.call(boom)
    clock.advance(10.0)

    with pytest.raises(RuntimeError):
        cb.call(boom)
    assert cb.state is State.OPEN

    clock.advance(9.999)
    with pytest.raises(CircuitOpenError):
        cb.call(ok)

    clock.advance(0.001)
    assert cb.call(ok) == "ok"


def test_rolling_window_evicts_old_failures_preventing_trip():
    cb, _ = make_breaker(min_requests=4, window_size=4, failure_threshold=0.5)
    with pytest.raises(RuntimeError):
        cb.call(boom)
    with pytest.raises(RuntimeError):
        cb.call(boom)
    # two successes push the two failures out of a window_size=4 deque
    cb.call(ok)
    cb.call(ok)
    cb.call(ok)
    cb.call(ok)
    assert cb.state is State.CLOSED


def test_exception_from_func_propagates():
    cb, _ = make_breaker(min_requests=10)
    with pytest.raises(RuntimeError, match="boom"):
        cb.call(boom)


def test_open_call_raises_circuit_open_error_type():
    cb, _ = make_breaker(min_requests=1, failure_threshold=0.5)
    with pytest.raises(RuntimeError):
        cb.call(boom)
    with pytest.raises(CircuitOpenError):
        cb.call(ok)


# --- measured: breaker vs naive retry under a simulated outage ----------


def test_breaker_reaches_dependency_far_less_than_naive_retry_during_outage():
    outage_length = 50
    tick_interval = 1.0
    max_ticks = 200

    naive_dep = make_flaky(outage_length)
    for naive_attempts in range(1, max_ticks + 1):
        try:
            naive_dep()
            break
        except RuntimeError:
            continue
    else:
        raise AssertionError("naive loop did not recover in time")

    breaker_dep = make_flaky(outage_length)
    clock = FakeClock()
    cb = CircuitBreaker(
        failure_threshold=0.6,
        min_requests=5,
        window_size=10,
        reset_timeout=10.0,
        half_open_max_calls=1,
        clock=clock,
    )

    ticks = 0
    while ticks < max_ticks:
        ticks += 1
        clock.advance(tick_interval)
        try:
            cb.call(breaker_dep)
            break
        except (CircuitOpenError, RuntimeError):
            continue
    else:
        raise AssertionError("breaker loop did not recover in time")

    assert cb.state is State.CLOSED
    # naive touches the dependency on every single attempt (== outage_length + 1);
    # the breaker fails fast while OPEN, so it reaches the dependency far less often.
    assert naive_dep.calls == outage_length + 1
    assert breaker_dep.calls < naive_dep.calls / 2
