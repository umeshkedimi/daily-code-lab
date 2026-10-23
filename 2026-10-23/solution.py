"""Circuit breaker (closed/open/half-open) guarding calls to an unreliable
dependency, measured against naive retry-with-backoff under a simulated
outage."""

from __future__ import annotations

import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Deque


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""


class CircuitBreaker:
    """Wraps calls to a dependency; trips to OPEN on a sustained failure
    rate, fails fast while open, and probes for recovery via HALF_OPEN."""

    def __init__(
        self,
        failure_threshold: float = 0.5,
        min_requests: int = 10,
        window_size: int = 20,
        reset_timeout: float = 30.0,
        half_open_max_calls: int = 5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.min_requests = min_requests
        self.window_size = window_size
        self.reset_timeout = reset_timeout
        self.half_open_max_calls = half_open_max_calls
        self._clock = clock

        self._state = State.CLOSED
        self._window: Deque[bool] = deque(maxlen=window_size)
        self._opened_at: float | None = None
        self._half_open_calls = 0

    @property
    def state(self) -> State:
        return self._state

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute `func` through the breaker.

        Returns func's result on success. Re-raises func's exception on
        failure (the breaker observes failures, it never swallows them).
        Raises CircuitOpenError without calling func when the circuit is
        OPEN and the reset_timeout has not yet elapsed.
        """
        now = self._clock()

        if self._state is State.OPEN:
            if now - self._opened_at < self.reset_timeout:
                raise CircuitOpenError("circuit is open")
            self._state = State.HALF_OPEN
            self._half_open_calls = 0

        if self._state is State.HALF_OPEN:
            if self._half_open_calls >= self.half_open_max_calls:
                raise CircuitOpenError("half-open trial budget exhausted")
            self._half_open_calls += 1
            try:
                result = func(*args, **kwargs)
            except Exception:
                self._trip(now)
                raise
            if self._half_open_calls >= self.half_open_max_calls:
                self._reset_closed()
            return result

        # CLOSED
        try:
            result = func(*args, **kwargs)
        except Exception:
            self._window.append(False)
            if self._should_trip():
                self._trip(now)
            raise
        self._window.append(True)
        return result

    def _should_trip(self) -> bool:
        if len(self._window) < self.min_requests:
            return False
        failures = self._window.count(False)
        return failures / len(self._window) >= self.failure_threshold

    def _trip(self, now: float) -> None:
        self._state = State.OPEN
        self._opened_at = now

    def _reset_closed(self) -> None:
        self._state = State.CLOSED
        self._window.clear()
        self._half_open_calls = 0
