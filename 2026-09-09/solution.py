"""Async retry with exponential backoff + full jitter for transient failures.

Retries only what's worth retrying: transport errors and 5xx are transient
and get retried; 4xx client errors are not (retrying a 404 or a bad request
just wastes time and delays failing).
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

import httpx

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 0.2  # seconds
    max_delay: float = 5.0  # seconds -- caps how large a single backoff can grow to


class RetryError(Exception):
    """Raised when every attempt was exhausted. Wraps the final exception."""

    def __init__(self, attempts: int, last_exception: Exception):
        super().__init__(f"gave up after {attempts} attempt(s): {last_exception!r}")
        self.attempts = attempts
        self.last_exception = last_exception


def is_retryable(exc: Exception) -> bool:
    """Default classifier: retry transient/transport failures, never client errors.

    A timeout or connection error may well succeed on the next try. A 4xx
    HTTP status won't -- the request itself is wrong, and retrying it is
    both useless and, for a non-idempotent call, potentially harmful.
    """
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


def compute_backoff(attempt: int, policy: RetryPolicy) -> float:
    """Full-jitter exponential backoff (AWS's recommended formula):
    uniform(0, min(max_delay, base_delay * 2**(attempt - 1))).

    The randomization is the point -- if every failed caller backed off by
    the *same* deterministic delay, they'd all retry in lockstep and hit the
    struggling service with a synchronized wave again (the "thundering herd").
    """
    ceiling = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
    return random.uniform(0, ceiling)


async def retry_async(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    policy: RetryPolicy = RetryPolicy(),
    classify: Callable[[Exception], bool] = is_retryable,
    on_retry: Callable[[int, Exception, float], None] | None = None,
    **kwargs: Any,
) -> T:
    """Call func(*args, **kwargs), retrying on transient failure.

    Non-retryable exceptions propagate immediately on the first occurrence
    -- no wasted attempts, no wasted delay.
    """
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            if not classify(exc):
                raise
            if attempt == policy.max_attempts:
                raise RetryError(attempt, exc) from exc
            delay = compute_backoff(attempt, policy)
            if on_retry:
                on_retry(attempt, exc, delay)
            await asyncio.sleep(delay)


def _log_retry(attempt: int, exc: Exception, delay: float) -> None:
    print(f"      attempt {attempt} failed ({type(exc).__name__}: {exc}) -- retrying in {delay:.2f}s")


def _make_flaky(fail_times: int, exc_factory: Callable[[], Exception] = lambda: TimeoutError("simulated timeout")):
    """Returns an async function that fails `fail_times` times, then succeeds."""
    calls = {"count": 0}

    async def flaky() -> str:
        calls["count"] += 1
        if calls["count"] <= fail_times:
            raise exc_factory()
        return f"succeeded on attempt {calls['count']}"

    return flaky


async def _always_fails(exc_factory: Callable[[], Exception]) -> str:
    raise exc_factory()


async def _fetch(client: httpx.AsyncClient, url: str) -> int:
    response = await client.get(url, timeout=5.0)
    response.raise_for_status()
    return response.status_code


async def demo():
    print("1) Transient failure that recovers: fails twice, then succeeds")
    flaky = _make_flaky(fail_times=2)
    policy = RetryPolicy(max_attempts=4, base_delay=0.2, max_delay=2.0)
    result = await retry_async(flaky, policy=policy, on_retry=_log_retry)
    print(f"   result: {result}")

    print("\n2) Non-retryable failure: fails fast, no wasted attempts or delay")
    start = time.monotonic()
    try:
        await retry_async(_always_fails, ValueError, classify=is_retryable, policy=policy, on_retry=_log_retry)
    except ValueError as exc:
        print(f"   raised immediately after 1 attempt in {time.monotonic() - start:.3f}s: {exc!r}")

    print("\n3) Persistent transient failure: exhausts all attempts, backoff grows each time")
    start = time.monotonic()
    try:
        await retry_async(
            _always_fails,
            lambda: TimeoutError("server never recovers"),
            policy=RetryPolicy(max_attempts=4, base_delay=0.3, max_delay=2.0),
            on_retry=_log_retry,
        )
    except RetryError as exc:
        print(f"   gave up after {exc.attempts} attempts in {time.monotonic() - start:.2f}s total: {exc.last_exception!r}")

    print("\n4) Jitter check: same attempt number, backoff varies across calls (not lockstep)")
    p = RetryPolicy(base_delay=1.0, max_delay=10.0)
    samples = [compute_backoff(attempt=3, policy=p) for _ in range(5)]
    ceiling = min(p.max_delay, p.base_delay * 2 ** 2)
    print(f"   ceiling for attempt 3: {ceiling:.2f}s, 5 sampled delays: {[f'{s:.2f}' for s in samples]}")

    print("\n5) Real HTTP: a 5xx is retried (and exhausts), a 4xx fails immediately")
    async with httpx.AsyncClient() as client:
        real_policy = RetryPolicy(max_attempts=3, base_delay=0.3, max_delay=2.0)

        try:
            await retry_async(_fetch, client, "https://httpbin.org/status/500", policy=real_policy, on_retry=_log_retry)
        except RetryError as exc:
            print(f"   /status/500 -- gave up after {exc.attempts} attempts: {exc.last_exception!r}")

        start = time.monotonic()
        try:
            await retry_async(_fetch, client, "https://httpbin.org/status/404", policy=real_policy, on_retry=_log_retry)
        except httpx.HTTPStatusError as exc:
            print(f"   /status/404 -- raised immediately after 1 attempt in {time.monotonic() - start:.2f}s: HTTP {exc.response.status_code}")


if __name__ == "__main__":
    asyncio.run(demo())
