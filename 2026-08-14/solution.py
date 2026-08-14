"""Async fan-out: fetch 3 independent external APIs concurrently and combine
their responses into one result, tolerating per-source timeouts/errors.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

Status = Literal["ok", "timeout", "error"]


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    timeout: float = 10.0


@dataclass(frozen=True)
class SourceResult:
    name: str
    status: Status
    data: Any
    error: str | None
    elapsed_seconds: float


@dataclass(frozen=True)
class CombinedResult:
    overall_status: Literal["complete", "partial", "failed"]
    data: dict[str, Any]
    errors: dict[str, str]
    wall_clock_seconds: float
    per_source: list[SourceResult] = field(default_factory=list)


async def fetch_source(client: httpx.AsyncClient, source: Source) -> SourceResult:
    """Fetch one source. Never raises -- every failure mode is captured as a
    SourceResult so one bad source can't take down the whole gather()."""
    start = time.monotonic()
    try:
        response = await client.get(source.url, timeout=source.timeout)
        response.raise_for_status()
        return SourceResult(
            name=source.name,
            status="ok",
            data=response.json(),
            error=None,
            elapsed_seconds=time.monotonic() - start,
        )
    except httpx.TimeoutException:
        return SourceResult(
            name=source.name,
            status="timeout",
            data=None,
            error=f"timed out after {source.timeout}s",
            elapsed_seconds=time.monotonic() - start,
        )
    except httpx.HTTPStatusError as exc:
        return SourceResult(
            name=source.name,
            status="error",
            data=None,
            error=f"HTTP {exc.response.status_code}",
            elapsed_seconds=time.monotonic() - start,
        )
    except httpx.HTTPError as exc:
        return SourceResult(
            name=source.name,
            status="error",
            data=None,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_seconds=time.monotonic() - start,
        )


async def gather_sources(sources: list[Source]) -> CombinedResult:
    """Fetch all sources concurrently and combine into a single result.

    Partial failure is a normal outcome, not an exception path: the caller
    always gets a CombinedResult, never a raised error from a single source.
    """
    start = time.monotonic()
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(fetch_source(client, s) for s in sources))
    wall_clock = time.monotonic() - start

    succeeded = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status != "ok"]

    if not failed:
        overall_status = "complete"
    elif succeeded:
        overall_status = "partial"
    else:
        overall_status = "failed"

    return CombinedResult(
        overall_status=overall_status,
        data={r.name: r.data for r in succeeded},
        errors={r.name: r.error for r in failed},
        wall_clock_seconds=wall_clock,
        per_source=list(results),
    )


def _print_combined(result: CombinedResult) -> None:
    print(f"   overall_status: {result.overall_status}")
    print(f"   wall_clock: {result.wall_clock_seconds:.2f}s "
          f"(sum of individual calls: {sum(r.elapsed_seconds for r in result.per_source):.2f}s)")
    for r in result.per_source:
        detail = "ok" if r.status == "ok" else f"{r.status} -- {r.error}"
        print(f"   - {r.name:12s} {r.elapsed_seconds:5.2f}s  {detail}")


async def demo():
    print("1) Happy path: 3 independent, healthy APIs fetched concurrently")
    healthy_sources = [
        Source("todo", "https://jsonplaceholder.typicode.com/todos/1"),
        Source("weather", "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&current_weather=true"),
        Source("cat_fact", "https://catfact.ninja/fact"),
    ]
    result = await gather_sources(healthy_sources)
    _print_combined(result)

    print("\n2) Partial failure: one slow source (client-side timeout) and one "
          "erroring source (HTTP 500), alongside one healthy source")
    mixed_sources = [
        Source("todo", "https://jsonplaceholder.typicode.com/todos/1"),
        Source("slow", "https://httpbin.org/delay/5", timeout=1.5),
        Source("broken", "https://httpbin.org/status/500"),
    ]
    result = await gather_sources(mixed_sources)
    _print_combined(result)
    print(f"   combined.data keys (only successful sources appear here): {list(result.data.keys())}")
    print(f"   combined.errors: {result.errors}")


if __name__ == "__main__":
    asyncio.run(demo())
