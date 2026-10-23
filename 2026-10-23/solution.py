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
        self._half_open_failures = 0

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
        raise NotImplementedError
