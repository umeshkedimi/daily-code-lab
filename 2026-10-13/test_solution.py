import time

from solution import (
    count_primes_below,
    measure_cpu_bound,
    measure_io_bound,
    measure_pool_startup_overhead,
    run_asyncio_io,
    run_asyncio_naive_cpu,
    run_asyncio_with_process_executor,
    run_multiprocess,
    run_sequential,
    run_threaded,
    simulate_io,
)

# Calibrated once (see notes.md) so these are fast enough for a normal test
# run (a few seconds total) while still landing comfortably inside the
# safety margins the assertions below use -- these are real wall-clock
# measurements, not simulated/fake-clock ones, so the margins are
# deliberately generous (2x+ on every claim below the ratio actually
# observed while calibrating) rather than tight.
CPU_N = 80_000
CPU_TASKS = 8
CPU_WORKERS = 4

IO_DURATION = 0.03
IO_TASKS = 6
IO_WORKERS = 4


# --- correctness: the workloads themselves ----------------------------------------


def test_count_primes_below_small_known_values():
    assert count_primes_below(2) == 0
    assert count_primes_below(3) == 1  # just "2"
    assert count_primes_below(10) == 4  # 2, 3, 5, 7
    assert count_primes_below(30) == 10  # 2,3,5,7,11,13,17,19,23,29


def test_simulate_io_returns_the_duration_and_actually_sleeps():
    start = time.perf_counter()
    result = simulate_io(0.02)
    elapsed = time.perf_counter() - start
    assert result == 0.02
    assert elapsed >= 0.02


# --- correctness: every strategy agrees on the actual results ---------------------


def test_all_cpu_bound_strategies_agree_on_results():
    tasks = [100, 200, 300]
    expected = [count_primes_below(t) for t in tasks]
    seq_results, _ = run_sequential(count_primes_below, tasks)
    thr_results, _ = run_threaded(count_primes_below, tasks, max_workers=2)
    mp_results, _ = run_multiprocess(count_primes_below, tasks, max_workers=2)
    aio_results, _ = run_asyncio_naive_cpu(count_primes_below, tasks)
    aio_exec_results, _ = run_asyncio_with_process_executor(count_primes_below, tasks, max_workers=2)
    assert seq_results == expected
    assert thr_results == expected
    assert mp_results == expected
    assert aio_results == expected
    assert aio_exec_results == expected


def test_all_io_bound_strategies_agree_on_results_and_preserve_order():
    tasks = [0.01, 0.02, 0.01, 0.03]
    seq_results, _ = run_sequential(simulate_io, tasks)
    thr_results, _ = run_threaded(simulate_io, tasks, max_workers=4)
    mp_results, _ = run_multiprocess(simulate_io, tasks, max_workers=4)
    aio_results, _ = run_asyncio_io(tasks)
    assert seq_results == tasks
    assert thr_results == tasks  # pool.map preserves input order even with concurrent execution
    assert mp_results == tasks
    assert aio_results == tasks


def test_empty_task_list_is_handled_by_every_strategy():
    assert run_sequential(count_primes_below, [])[0] == []
    assert run_threaded(count_primes_below, [], max_workers=2)[0] == []
    assert run_multiprocess(count_primes_below, [], max_workers=2)[0] == []
    assert run_asyncio_naive_cpu(count_primes_below, [])[0] == []
    assert run_asyncio_io([])[0] == []


# --- the actual point: measured, directional performance claims -----------------


def test_threading_does_not_meaningfully_speed_up_cpu_bound_work():
    # The GIL serializes pure-Python bytecode execution across threads --
    # threaded should be close to sequential, never a large speedup.
    result = measure_cpu_bound(CPU_N, CPU_TASKS, CPU_WORKERS)
    assert result["threaded"] >= result["sequential"] * 0.85


def test_multiprocessing_meaningfully_speeds_up_cpu_bound_work():
    result = measure_cpu_bound(CPU_N, CPU_TASKS, CPU_WORKERS)
    assert result["multiprocess"] < result["sequential"] * 0.6


def test_naive_asyncio_does_not_speed_up_cpu_bound_work_at_all():
    # A coroutine with no `await` inside its CPU-bound body never yields
    # control -- "concurrent" coroutines around pure computation run just
    # as sequentially as a plain loop.
    result = measure_cpu_bound(CPU_N, CPU_TASKS, CPU_WORKERS)
    assert result["asyncio_naive"] >= result["sequential"] * 0.85


def test_asyncio_with_a_process_executor_matches_plain_multiprocessing():
    # The FIX for the previous test: delegating to a process pool gets
    # back the same speedup plain multiprocessing gets, because it's
    # doing the same underlying thing.
    result = measure_cpu_bound(CPU_N, CPU_TASKS, CPU_WORKERS)
    assert result["asyncio_executor"] < result["sequential"] * 0.6


def test_threading_and_asyncio_both_scale_well_on_io_bound_work():
    # time.sleep and asyncio.sleep both release control during the wait,
    # so concurrency actually helps here, unlike the CPU-bound case.
    result = measure_io_bound(IO_DURATION, IO_TASKS, IO_WORKERS)
    assert result["threaded"] < result["sequential"] * 0.5
    assert result["asyncio"] < result["sequential"] * 0.4


def test_multiprocessing_is_the_wrong_tool_for_io_bound_work():
    # It still "works" (faster than sequential), but measurably loses to
    # threading on the exact same task, because of per-task dispatch
    # overhead (pickling args/results across a process boundary) that
    # dwarfs a cheap I/O wait -- the same overhead that's worth paying for
    # an expensive CPU-bound task is pure waste here.
    result = measure_io_bound(IO_DURATION, IO_TASKS, IO_WORKERS)
    assert result["multiprocess"] < result["sequential"]  # still beats sequential...
    assert result["multiprocess"] > result["threaded"] * 1.3  # ...but clearly loses to threading


def test_a_single_worker_process_pool_is_pure_overhead_on_cpu_bound_work():
    # With exactly one worker there is no parallelism to gain, only the
    # cost of spawning a process and shipping work to it -- this should be
    # slower than just calling the function directly, not faster.
    small_n, small_tasks = 30_000, 4
    tasks = [small_n] * small_tasks
    _, sequential_time = run_sequential(count_primes_below, tasks)
    _, one_worker_time = run_multiprocess(count_primes_below, tasks, max_workers=1)
    assert one_worker_time > sequential_time * 1.1


def test_multiprocess_speedup_increases_with_worker_count_up_to_task_count():
    tasks = [CPU_N] * CPU_TASKS
    _, t1 = run_multiprocess(count_primes_below, tasks, max_workers=1)
    _, t4 = run_multiprocess(count_primes_below, tasks, max_workers=4)
    _, t8 = run_multiprocess(count_primes_below, tasks, max_workers=8)
    assert t4 < t1 * 0.7  # meaningfully faster with 4 workers than 1
    assert t8 < t4 * 0.95  # still improves a bit from 4 to 8, even if sub-linearly


def test_process_pool_startup_overhead_exceeds_thread_pool_startup_overhead():
    # Spawning an OS process is inherently more expensive than spawning an
    # OS thread -- this is part of why multiprocessing needs a genuinely
    # expensive task per dispatch to be worth it.
    overhead = measure_pool_startup_overhead(workers=8)
    assert overhead["process_pool"] > overhead["thread_pool"]
