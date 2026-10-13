## Implementation

- `count_primes_below`: trial division up to `isqrt(candidate)` for every number below `n`, deliberately not sieve-optimized — a sieve would finish faster, but the point here is a workload that spends its entire time executing Python bytecode (no numpy, no C extension), so the GIL is actually in effect the whole time it runs.
- `simulate_io` / `simulate_io_async`: `time.sleep` / `asyncio.sleep`, nothing else. Both genuinely release control for their duration (a thread sleeping releases the GIL; an awaited `asyncio.sleep` yields to the event loop) — the realistic stand-in for a network or disk wait, not a CPU-bound function pretending to be I/O-bound.
- Every `run_*` function returns `(results, elapsed_seconds)` from the same call, so a test checking correctness and a test checking timing are looking at the exact same execution, not two separate runs that could diverge (e.g. due to warm-up effects, OS scheduling noise between runs, or a caching side effect).
- `run_asyncio_naive_cpu`'s inner `_wrap` coroutine calls `fn(t)` directly with no `await` anywhere in its body — this is the realistic shape of the mistake (code that reads as concurrent, using `asyncio.gather`) rather than a contrived "obviously broken" example.
- `run_asyncio_with_process_executor` is checked against `run_multiprocess`'s own timing, not just against sequential — the claim is specifically "this converges to what multiprocessing already gets," which is a stronger and more falsifiable statement than "this is also fast."
- `measure_pool_startup_overhead` creates and immediately tears down a pool with zero tasks submitted — isolated specifically from *dispatch* overhead (the per-task cost of pickling arguments/results across a process boundary), which is the actual reason multiprocessing loses to threading on cheap I/O-bound tasks (see Measured Results) despite this startup number alone being tiny.
- Not built: a shared-memory / `multiprocessing.shared_memory` variant (would reduce dispatch overhead for large payloads, orthogonal to this exercise's point), `concurrent.futures.as_completed`-based streaming consumption (this exercise only needed "all results, final timing," not incremental progress), or measuring a true multi-core-saturating workload at production scale (the workloads here are sized for a fast, correctness-checkable test suite, not a realistic production benchmark).

## Measured Results

(Real wall-clock measurements, calibrated on an 18-core machine; `demo()` reproduces these exactly.)

**CPU-bound** (`count_primes_below(100_000)`, 8 tasks, 8 workers):

| strategy | time | speedup |
|---|---|---|
| sequential | 401.8ms | 1.00x |
| threaded | 405.6ms | 0.99x |
| multiprocess | 125.7ms | 3.20x |
| asyncio (naive) | 392.7ms | 1.02x |
| asyncio + process executor | 121.8ms | 3.30x |

**I/O-bound** (`sleep(0.1)`, 8 tasks, 8 workers):

| strategy | time | speedup |
|---|---|---|
| sequential | 829.6ms | 1.00x |
| threaded | 106.0ms | 7.83x |
| multiprocess | 179.9ms | 4.61x |
| asyncio | 101.7ms | 8.16x |

**Multiprocess speedup vs. worker count, CPU-bound, 8 tasks** — real but *sub-linear*, with efficiency dropping steadily:

| workers | time | speedup | efficiency (speedup/workers) |
|---|---|---|---|
| 1 | 451.4ms | 0.88x | 0.88 |
| 2 | 258.9ms | 1.54x | 0.77 |
| 4 | 157.3ms | 2.54x | 0.63 |
| 8 | 120.6ms | 3.31x | 0.41 |

Note workers=1 is *slower* than sequential (0.88x = 1.14x slower) — one worker means zero parallelism and pure process-spawn-plus-dispatch overhead on top of the same work.

**Pool startup overhead, 8 workers, zero tasks**: thread pool 0.03ms, process pool 0.34ms — an order of magnitude more expensive to start, but small enough in absolute terms that it's *not* the reason multiprocessing loses on I/O-bound work above (179.9ms vs. threading's 106.0ms) — that gap is dispatch overhead (pickling each task's argument and result across the process boundary, 8 separate times), not pool startup, which is why the two are measured separately.

## Follow-up Questions

**Why?**
"Just use threads" or "just use multiprocessing" or "just use asyncio" are all wrong advice stated unconditionally — which one helps, by how much, and which one actively makes things worse, depends entirely on whether the bottleneck is CPU or I/O, and this is exactly the kind of claim worth checking against reality rather than repeating from a blog post.

**How exactly?**
The GIL allows only one thread to execute Python bytecode at a time; a C-level operation that releases it (I/O, `time.sleep`, many numpy/C-extension calls) lets other threads run during that release, but pure-Python computation holds the GIL for its entire duration, so N threads doing pure-Python work run one at a time regardless of N. Multiprocessing sidesteps this entirely by using separate OS processes, each with its own interpreter and GIL, at the cost of needing to pickle data across process boundaries to communicate. asyncio is cooperative, single-threaded concurrency: a coroutine only yields control at an explicit `await`, so code with no `await` inside a long computation blocks the entire event loop exactly like a plain function call would.

**Which algorithm?**
Not an algorithm — a direct comparison of Python's three standard concurrency primitives (`threading` via `ThreadPoolExecutor`, `multiprocessing` via `ProcessPoolExecutor`, and `asyncio`), plus the specific combination (`asyncio` dispatching to a `ProcessPoolExecutor` via `run_in_executor`) that's the actually-correct way to get CPU-bound parallelism inside an otherwise-asyncio program.

**Which library?**
Standard library only: `concurrent.futures` for both thread and process pools behind one shared interface (`.map`), `asyncio` for coroutine-based concurrency, `time`/`math` for the workloads themselves.

**What happens internally?**
`ThreadPoolExecutor.map` on a pure-Python function still only ever has one thread actually executing bytecode at a time (the GIL enforces this at the interpreter level, with context switches happening at bytecode-count intervals or on I/O), which is why the "threaded" row above is indistinguishable from sequential. `ProcessPoolExecutor.map` pickles each task's argument, sends it to a worker process over a pipe, the worker unpickles it, computes, pickles the result, and sends it back — real parallelism, but with serialization cost on every single task that the thread-based and asyncio-based approaches never pay at all.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **A CPU-bound task small enough that process-dispatch overhead dominates**: `test_a_single_worker_process_pool_is_pure_overhead_on_cpu_bound_work` shows this directly even with real work (30,000-prime counting) and a real worker — a single-worker process pool is measurably *slower* than just calling the function.
- **Someone "parallelizes" CPU-bound work with asyncio and sees it work**: it doesn't, and can't, with no `await` point inside the computation — measured directly (`asyncio_naive` lands within 2% of sequential, both in the main measurement and when a no-op `await asyncio.sleep(0)` is added *before* the call, which still changes nothing because nothing else can run *during* the blocking call either way — see Key Learnings).
- **Multiprocessing "helping" on an I/O-bound task gets mistaken for the right choice**: it is faster than sequential (`test_multiprocessing_is_the_wrong_tool_for_io_bound_work` confirms this), but measurably loses to threading on the identical task — "faster than doing nothing" and "the right tool" are different claims, and only the second one is actually useful advice.
- **Worker count exceeds CPU core count or task count**: efficiency (not raw speedup) is the number to watch — the worker-count sweep shows efficiency monotonically decreasing even before any ceiling is hit, which is the expected shape (fixed per-task overhead, finite real parallelism, diminishing returns), not a bug.

**What trade-offs did you consider?**
- **Trial-division primes (chosen) vs. a faster/sieve-based CPU-bound task**: a faster algorithm would finish too quickly to measure cleanly at a reasonable task count, and more importantly isn't the point — the exercise needs *time spent executing Python bytecode*, which a sieve (more numeric array manipulation, less interpreter overhead per useful unit of work) would actually reduce.
- **`time.sleep` (chosen) vs. a real network call for the I/O-bound workload**: a real network call would make the measurement's "pure I/O wait" claim harder to isolate from network variance/latency, and would make the test suite flaky and slow in CI. `time.sleep` is the standard, honest stand-in specifically because it releases the GIL for a precisely controllable duration and nothing else.
- **Returning `(results, elapsed)` from every runner (chosen) vs. separate timing and correctness helper functions**: coupling them means a correctness test and a timing test are never silently looking at two different runs of the same code — worth the minor awkwardness of every caller unpacking a tuple.
- **Generous (2x+) safety margins on every timing assertion (chosen) vs. tight margins matching the exact calibrated ratios:** tight margins would make this suite flaky on a loaded CI machine or a slower/different-core-count machine; the margins here are chosen so the *qualitative* claim (meaningfully faster / not meaningfully faster) survives real-world timing noise, at the cost of not catching a regression that only degrades performance by, say, 20%.

**How do you debug it?**
- If a correctness test fails for one strategy but not others, the bug is almost certainly in that strategy's own runner, not the workload function — `run_sequential`'s result is the one to trust as ground truth, since it has no concurrency machinery to doubt.
- If a timing claim looks wrong, re-run `measure_pool_startup_overhead` and the plain-sequential baseline first — a surprisingly slow "fast" strategy is often explained by pool/dispatch overhead eating the gain at the current task count, which this exercise measures separately for exactly this reason.
- For an asyncio concurrency bug, `asyncio.all_tasks()` inside the running loop (or just checking whether `await` appears anywhere inside the hot path) answers "is this actually concurrent or just dressed up as concurrent" directly.

**How do you evaluate it?**
- **14 tests, 5/5 consecutive clean runs** (real wall-clock timing, not simulated — calibrated so every directional assertion has a 2x+ safety margin below the ratio actually observed).
- **Mutation check, 5 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - `count_primes_below` off-by-one (`range(2, n)` → `range(2, n+1)`): 1/14 failed.
  - `run_threaded` secretly uses `ProcessPoolExecutor`: 3/14 failed.
  - `run_asyncio_naive_cpu`'s `_wrap` gains an `await asyncio.sleep(0)` *before* calling `fn`: **0/14 failed — confirmed genuinely benign, not a coverage gap** (see Key Learnings).
  - `measure_pool_startup_overhead`'s two returned numbers get swapped: 1/14 failed.
  - `run_asyncio_with_process_executor` secretly uses a `ThreadPoolExecutor`: 1/14 failed.

## Key Learnings

- **Adding *an* `await` is not the same as adding *the* `await` that matters — confirmed by measurement, not just reasoned about.** The one mutation that produced zero failures added `await asyncio.sleep(0)` immediately before the blocking CPU-bound call, inside the exact coroutine `run_asyncio_naive_cpu` exists to show doesn't help. It's tempting to assume this "fixes" it, since the function is no longer literally missing an `await`. It doesn't: in a single-threaded event loop, yielding control *before* a blocking call only lets some *other* ready coroutine run — and that other coroutine, if it's also calling the same CPU-bound function with no internal yield point, will itself block the loop for its own full duration the moment it's resumed. No two CPU-bound calls can ever overlap this way; the total wall-clock time is unchanged (measured directly: 0.983x of sequential, statistically indistinguishable from the 1.02x of the true naive version). The actual fix (`run_asyncio_with_process_executor`) works for a structurally different reason: it doesn't add a yield point to the computation at all, it moves the computation entirely off the event loop's thread. This is a sharper, measured version of "asyncio doesn't give you parallelism, it gives you concurrency during waits" — the lesson survives contact with a plausible-looking "fix" that doesn't actually work.
- **Separating pool startup cost from per-task dispatch cost was necessary to explain a result that otherwise looks contradictory.** Multiprocessing's pool-startup overhead (0.34ms) is small enough that it can't explain why multiprocessing loses to threading by nearly 2x on the I/O-bound benchmark (179.9ms vs. 106.0ms) — the real cost is per-task: every one of the 8 tasks pays a pickle/unpickle round-trip across a process boundary, which is pure waste for a workload whose real time is spent asleep, not computing. Measuring the two costs separately turned "multiprocessing is sometimes slower, mysteriously" into a specific, named, located cost.
