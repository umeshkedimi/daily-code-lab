"""Concurrency models in Python, measured instead of recited: the same
CPU-bound and I/O-bound workload, run under threading, multiprocessing,
and asyncio, with real wall-clock numbers -- not the usual one-paragraph
"the GIL means threads don't help with CPU-bound work" recited from
memory, but that exact claim checked against a stopwatch.

Four things measured, not assumed:
1. Threading barely beats sequential on CPU-bound work (the GIL serializes
   pure-Python bytecode execution across threads).
2. Multiprocessing scales with worker count on CPU-bound work, but
   sub-linearly, and a single worker is pure overhead with zero benefit.
3. Threading and asyncio both scale well on I/O-bound work (sleep/network
   waits release the GIL, or never touch the GIL at all); multiprocessing
   also "works" there but is clearly the wrong tool (it wins on CPU-bound,
   loses on I/O-bound, to the same per-task dispatch overhead).
4. asyncio alone does NOT parallelize CPU-bound work -- a coroutine with no
   `await` inside its CPU-bound body never yields control, so "concurrent"
   coroutines around pure-Python computation run exactly as sequentially as
   calling the function directly. The fix is delegating to a process pool
   via `run_in_executor`, measured here to land in the same range as plain
   multiprocessing.
"""

from __future__ import annotations

import asyncio
import math
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Callable, Dict, List, Sequence, Tuple

# --- the two workloads ---------------------------------------------------------------


def count_primes_below(n: int) -> int:
    """CPU-bound: pure-Python trial division, deliberately not sieve-
    optimized, so each call burns CPU cycles in the Python interpreter the
    whole time -- never calling out to C code that would release the GIL."""
    count = 0
    for candidate in range(2, n):
        is_prime = True
        limit = int(math.isqrt(candidate))
        for divisor in range(2, limit + 1):
            if candidate % divisor == 0:
                is_prime = False
                break
        if is_prime:
            count += 1
    return count


def simulate_io(duration: float) -> float:
    """I/O-bound stand-in: time.sleep releases the GIL for its duration,
    the same way a real network/disk wait would -- the CPU is free for
    other threads the entire time, which is the whole reason threading
    helps here and not on count_primes_below."""
    time.sleep(duration)
    return duration


async def simulate_io_async(duration: float) -> float:
    await asyncio.sleep(duration)
    return duration


# --- runners: same task list, several different concurrency strategies -----------


def run_sequential(fn: Callable, tasks: Sequence) -> Tuple[List, float]:
    start = time.perf_counter()
    results = [fn(t) for t in tasks]
    return results, time.perf_counter() - start


def run_threaded(fn: Callable, tasks: Sequence, max_workers: int) -> Tuple[List, float]:
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(fn, tasks))
    return results, time.perf_counter() - start


def run_multiprocess(fn: Callable, tasks: Sequence, max_workers: int) -> Tuple[List, float]:
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(fn, tasks))
    return results, time.perf_counter() - start


def run_asyncio_naive_cpu(fn: Callable, tasks: Sequence) -> Tuple[List, float]:
    """Wraps a SYNCHRONOUS CPU-bound function in coroutines with no actual
    `await` point inside the CPU work. This is the naive, common mistake:
    it looks concurrent (gather, multiple coroutines) but there is no
    point where control is ever handed back to the event loop, so this
    runs exactly as sequentially as calling `fn` in a plain loop. Included
    specifically to measure that it buys nothing, not to recommend it."""

    async def _wrap(t):
        return fn(t)  # no await inside -- never yields control

    async def _runner():
        return await asyncio.gather(*[_wrap(t) for t in tasks])

    start = time.perf_counter()
    results = asyncio.run(_runner())
    return results, time.perf_counter() - start


def run_asyncio_io(tasks: Sequence[float]) -> Tuple[List, float]:
    async def _runner():
        return await asyncio.gather(*[simulate_io_async(t) for t in tasks])

    start = time.perf_counter()
    results = asyncio.run(_runner())
    return results, time.perf_counter() - start


def run_asyncio_with_process_executor(fn: Callable, tasks: Sequence, max_workers: int) -> Tuple[List, float]:
    """The correct pattern for CPU-bound work inside an asyncio program:
    delegate to a process pool via `run_in_executor`, so the event loop
    thread is never blocked and the OS actually runs the work in
    parallel -- this should land in the same range as plain
    `run_multiprocess`, since it's doing the same thing underneath."""

    async def _runner():
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            return await asyncio.gather(*[loop.run_in_executor(pool, fn, t) for t in tasks])

    start = time.perf_counter()
    results = asyncio.run(_runner())
    return results, time.perf_counter() - start


# --- measurement helpers --------------------------------------------------------------


def measure_cpu_bound(n: int, num_tasks: int, workers: int) -> Dict[str, float]:
    tasks = [n] * num_tasks
    _, seq = run_sequential(count_primes_below, tasks)
    _, thr = run_threaded(count_primes_below, tasks, workers)
    _, mp = run_multiprocess(count_primes_below, tasks, workers)
    _, aio_naive = run_asyncio_naive_cpu(count_primes_below, tasks)
    _, aio_exec = run_asyncio_with_process_executor(count_primes_below, tasks, workers)
    return {
        "sequential": seq,
        "threaded": thr,
        "multiprocess": mp,
        "asyncio_naive": aio_naive,
        "asyncio_executor": aio_exec,
    }


def measure_io_bound(duration: float, num_tasks: int, workers: int) -> Dict[str, float]:
    tasks = [duration] * num_tasks
    _, seq = run_sequential(simulate_io, tasks)
    _, thr = run_threaded(simulate_io, tasks, workers)
    _, mp = run_multiprocess(simulate_io, tasks, workers)
    _, aio = run_asyncio_io(tasks)
    return {"sequential": seq, "threaded": thr, "multiprocess": mp, "asyncio": aio}


def measure_pool_startup_overhead(workers: int) -> Dict[str, float]:
    """The pure fixed cost of creating and tearing down a pool, doing no
    work at all -- isolates "pool setup is expensive" from "dispatching
    work to a pool is expensive," which are different costs (see
    notes.md for why multiprocessing loses on I/O-bound work despite this
    number alone looking small)."""
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers):
        pass
    thread_overhead = time.perf_counter() - start

    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers):
        pass
    process_overhead = time.perf_counter() - start

    return {"thread_pool": thread_overhead, "process_pool": process_overhead}


def demo() -> None:
    N, NUM_TASKS, WORKERS = 100_000, 8, 8
    print(f"1) CPU-bound: count_primes_below({N}), {NUM_TASKS} tasks, {WORKERS} workers")
    cpu = measure_cpu_bound(N, NUM_TASKS, WORKERS)
    seq = cpu["sequential"]
    for name, elapsed in cpu.items():
        print(f"   {name:>18}: {elapsed*1000:7.1f}ms  (speedup {seq/elapsed:.2f}x)")

    print(f"\n2) CPU-bound, multiprocess speedup vs. worker count ({NUM_TASKS} tasks)")
    tasks = [N] * NUM_TASKS
    _, seq_time = run_sequential(count_primes_below, tasks)
    for w in [1, 2, 4, 8]:
        _, t = run_multiprocess(count_primes_below, tasks, w)
        print(f"   workers={w}: {t*1000:7.1f}ms  speedup={seq_time/t:.2f}x  efficiency={seq_time/t/w:.2f}")

    DURATION = 0.1
    print(f"\n3) I/O-bound: sleep({DURATION}), {NUM_TASKS} tasks, {WORKERS} workers")
    io = measure_io_bound(DURATION, NUM_TASKS, WORKERS)
    seq = io["sequential"]
    for name, elapsed in io.items():
        print(f"   {name:>18}: {elapsed*1000:7.1f}ms  (speedup {seq/elapsed:.2f}x)")

    print(f"\n4) Pool startup overhead ({WORKERS} workers, no work)")
    overhead = measure_pool_startup_overhead(WORKERS)
    for name, elapsed in overhead.items():
        print(f"   {name:>13}: {elapsed*1000:.2f}ms")


if __name__ == "__main__":
    demo()
