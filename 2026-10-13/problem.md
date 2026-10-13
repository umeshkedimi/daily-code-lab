# Problem

**Category:** `python`

## Statement

Run the *same* CPU-bound workload and the *same* I/O-bound workload under four concurrency strategies — sequential, threading, multiprocessing, and asyncio — and measure the result with a stopwatch instead of reciting the usual one-paragraph GIL explanation.

**Requirements:**
- The CPU-bound workload must be pure Python (no C-extension call that releases the GIL mid-computation), so the GIL's effect is actually what's being measured.
- The I/O-bound workload must genuinely release control during the wait (`time.sleep` / `asyncio.sleep`), standing in for a real network or disk wait.
- Every strategy must be checked for **correctness** (identical results, in the same order, as calling the function directly) before any performance claim is trusted — a concurrency bug that silently drops or reorders results would make every timing number meaningless.
- The naive, common mistake — wrapping a synchronous CPU-bound function in a coroutine with no `await` inside it and expecting `asyncio.gather` to parallelize it — must be demonstrated to buy literally nothing, alongside the correct fix (delegating to a process pool via `run_in_executor`).
- Pool *startup* overhead (spawning workers, doing no work) must be measured separately from pool *dispatch* overhead (per-task cost), since conflating them hides why multiprocessing can lose to threading on cheap tasks despite real parallelism.

## Constraints

- Standard library only (`asyncio`, `concurrent.futures`, `time`, `math`).
- Performance assertions are real wall-clock measurements, not simulated with a fake clock — margins are chosen with a 2x+ safety factor below the ratios actually observed while calibrating, specifically so the claims hold up under normal machine-load variance without being so loose they'd pass a broken implementation too.
- Checked with a small, fast correctness/edge-case suite (all strategies agree, empty task lists, known prime counts) plus a handful of calibrated, directional performance tests for the actual pedagogical claims — not performance claims asserted in prose with no automated check at all.

## Approach

1. **Two workloads, chosen for what they are, not for convenience**: `count_primes_below` (pure-Python trial division — deliberately not sieve-optimized, so it burns CPU the whole time and never touches C code that would release the GIL) and `simulate_io` / `simulate_io_async` (`time.sleep` / `asyncio.sleep`, which genuinely release control for their duration).
2. **Five runners sharing one measurement pattern**: `run_sequential`, `run_threaded`, `run_multiprocess`, `run_asyncio_naive_cpu` (the mistake), `run_asyncio_with_process_executor` (the fix) and `run_asyncio_io` — each returns `(results, elapsed_seconds)`, so correctness and timing are checked from the exact same call, not two separate runs that could silently diverge.
3. **The naive-asyncio mistake, built faithfully, not as a strawman**: `run_asyncio_naive_cpu` wraps `fn` in a real coroutine and really calls `asyncio.gather` — it looks like concurrent code. The bug is structural (no `await` point exists inside the CPU-bound body for control to yield at), not a deliberately broken toy.
4. **Measure the fix separately, and confirm it converges to the same place as the already-understood tool**: `run_asyncio_with_process_executor` is checked against plain `run_multiprocess`'s timing, not just against sequential — the claim isn't "this is faster," it's specifically "this recovers the exact speedup multiprocessing already gets, because underneath it's doing the same thing."
5. **A worker-count sweep and a startup-overhead-only measurement**, both aimed at one honest point: multiprocessing's speedup is real but sub-linear (efficiency measurably drops as workers increase, even before hitting the task-count ceiling), and a single-worker process pool is strictly worse than not using a pool at all.
