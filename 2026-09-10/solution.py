"""Async Postgres connection pool, built from scratch on top of asyncpg's raw
connection API (not asyncpg.create_pool) so the actual pooling mechanics are
visible: bounded concurrency, exhaustion/timeout, dead-connection recovery,
and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

import asyncpg


class PoolTimeoutError(Exception):
    """Raised when no connection became available within acquire_timeout."""


class PoolClosedError(Exception):
    """Raised when acquire() is called after the pool has been closed."""


class AsyncConnectionPool:
    """Bounded pool of asyncpg connections.

    Invariant maintained at all times: (idle connections) + (checked-out
    connections) <= max_size. A counting semaphore enforces this: every
    acquire() must first win a permit (capped at max_size) before it's
    allowed to either reuse an idle connection or create a new one, and
    every release() gives the permit back -- whether or not the connection
    itself survives.
    """

    def __init__(self, dsn: str, min_size: int = 2, max_size: int = 5, acquire_timeout: float = 5.0):
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.acquire_timeout = acquire_timeout
        self._idle: asyncio.Queue[asyncpg.Connection] = asyncio.Queue()
        self._permits = asyncio.Semaphore(max_size)
        self._live_count = 0  # observability only -- not used for gating
        self._checked_out = 0  # observability only -- not used for gating
        self._closed = False

    async def start(self) -> None:
        """Pre-warm min_size connections so the first callers don't pay
        connection-setup latency."""
        for _ in range(self.min_size):
            conn = await self._new_connection()
            await self._idle.put(conn)

    async def _new_connection(self) -> asyncpg.Connection:
        conn = await asyncpg.connect(self.dsn)
        self._live_count += 1
        return conn

    @asynccontextmanager
    async def acquire(self):
        """`async with pool.acquire() as conn:` -- checks a connection out,
        guarantees it's returned (or discarded, if dead) on the way out."""
        if self._closed:
            raise PoolClosedError("pool is closed")

        try:
            await asyncio.wait_for(self._permits.acquire(), timeout=self.acquire_timeout)
        except asyncio.TimeoutError:
            raise PoolTimeoutError(
                f"no connection available within {self.acquire_timeout}s "
                f"(pool exhausted at max_size={self.max_size})"
            )

        try:
            conn = self._idle.get_nowait()
        except asyncio.QueueEmpty:
            conn = await self._new_connection()

        # An idle connection can go bad while sitting unused (network
        # blip, server-side termination) -- verify before handing it out
        # rather than discovering it mid-query.
        if conn.is_closed():
            self._live_count -= 1
            conn = await self._new_connection()

        self._checked_out += 1
        try:
            yield conn
        finally:
            self._checked_out -= 1
            await self._release(conn)

    async def _release(self, conn: asyncpg.Connection) -> None:
        if self._closed or conn.is_closed():
            if not conn.is_closed():
                await conn.close()
            self._live_count -= 1
            # connection dropped, not returned to idle -- the permit still
            # goes back, so a future acquire() will just create a fresh one
        else:
            await self._idle.put(conn)
        self._permits.release()

    async def close(self) -> None:
        self._closed = True
        while not self._idle.empty():
            conn = self._idle.get_nowait()
            await conn.close()
            self._live_count -= 1

    def stats(self) -> dict:
        return {
            "live_connections": self._live_count,
            "idle": self._idle.qsize(),
            "checked_out": self._checked_out,
            "max_size": self.max_size,
        }


def _make_dsn() -> str:
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5432")
    return f"postgres://postgres:devpass@{host}:{port}/devdb"


async def _run_query(pool: AsyncConnectionPool, label: str, sleep_seconds: float) -> str:
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT pg_sleep($1)", sleep_seconds)
        return f"{label} done"


async def demo():
    dsn = _make_dsn()

    print("1) Bounded concurrency: 6 queries (0.3s each) through a pool with max_size=3")
    pool = AsyncConnectionPool(dsn, min_size=1, max_size=3, acquire_timeout=10.0)
    await pool.start()
    start = time.monotonic()
    results = await asyncio.gather(*(_run_query(pool, f"q{i}", 0.3) for i in range(6)))
    elapsed = time.monotonic() - start
    print(f"   {len(results)} queries completed in {elapsed:.2f}s "
          f"(sequential would be ~1.8s; fully parallel would be ~0.3s; "
          f"max_size=3 bounds it to ~2 batches, so ~0.6s)")
    print(f"   stats after: {pool.stats()}")
    await pool.close()

    print("\n2) Pool exhaustion: max_size=2, acquire_timeout=0.5s, 4 concurrent long queries")
    pool = AsyncConnectionPool(dsn, min_size=1, max_size=2, acquire_timeout=0.5)
    await pool.start()

    async def guarded(i):
        try:
            return await _run_query(pool, f"q{i}", 1.0)
        except PoolTimeoutError as exc:
            return f"q{i} REJECTED: {exc}"

    results = await asyncio.gather(*(guarded(i) for i in range(4)))
    for r in results:
        print(f"   {r}")
    await pool.close()

    print("\n3) Self-healing: a connection that dies while idle is detected and replaced, not reused broken")
    pool = AsyncConnectionPool(dsn, min_size=1, max_size=2, acquire_timeout=5.0)
    await pool.start()
    async with pool.acquire() as conn:
        pid = await conn.fetchval("SELECT pg_backend_pid()")
    print(f"   idle connection has backend pid {pid}; killing it from a separate admin connection")
    admin_conn = await asyncpg.connect(dsn)
    await admin_conn.execute("SELECT pg_terminate_backend($1)", pid)
    await admin_conn.close()
    await asyncio.sleep(0.2)  # let the termination land
    print(f"   stats before reuse attempt: {pool.stats()}")
    async with pool.acquire() as conn:
        new_pid = await conn.fetchval("SELECT pg_backend_pid()")
        print(f"   next acquire() succeeded with a fresh backend pid {new_pid} (old one was discarded)")
    await pool.close()

    print("\n4) Graceful shutdown: acquire() after close() fails clearly, no hang")
    pool = AsyncConnectionPool(dsn, min_size=2, max_size=2, acquire_timeout=2.0)
    await pool.start()
    print(f"   stats before close: {pool.stats()}")
    await pool.close()
    print(f"   stats after close: {pool.stats()}")
    try:
        async with pool.acquire():
            pass
    except PoolClosedError as exc:
        print(f"   acquire() after close raised immediately: {exc!r}")


if __name__ == "__main__":
    asyncio.run(demo())
