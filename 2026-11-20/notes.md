## Implementation

- `ManagedResource.__exit__` returning `self.suppress` directly is the entire suppression mechanism for the class-based version -- the `with` statement machinery itself checks `__exit__`'s return value and only re-raises the in-flight exception if that value is falsy. There's no separate "should I suppress" branch to write; it's one return statement doing double duty as both the cleanup signal and the suppression signal.
- `correct_resource`'s `except Exception: if not suppress: raise` is deliberately shaped to mirror that: catching broadly, then bare-`raise`-ing (not `raise exc_val` or similar) preserves the original exception's traceback exactly, rather than constructing a new exception object that would show a confusing, re-pointed stack trace.
- The `finally` block is what makes `correct_resource`'s cleanup guarantee hold even when `suppress=False` and the exception is about to propagate out of the generator entirely -- `finally` runs on the way out regardless of which of the three paths (`yield` returns normally, `except` catches and swallows, `except` catches and re-raises) the generator took to get there.
- `leaky_resource` is implemented as the obvious, naive-looking version of a generator context manager -- no `try` at all -- specifically because that's what the real-world mistake actually looks like: not a deliberately broken attempt, just the version someone writes who hasn't yet hit the exception-path bug in practice.
- Not built: the `BaseException`-vs-`Exception` asymmetry between the two protocols, `contextlib.ExitStack`, `@contextlib.asynccontextmanager` -- all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `ManagedResource.__enter__` / `__exit__` | O(1) | O(1) |
| `correct_resource` / `leaky_resource` | O(1) per step | O(1) |

## Measured Results

- **The headline gap, exact**: under an identical `ValueError` raised inside the `with` block, `ManagedResource` and `correct_resource` both produce `["open:x", "close:x"]` -- cleanup runs, exception still propagates. `leaky_resource` produces exactly `["open:x"]` -- the exception still propagates correctly (nothing here breaks exception delivery itself), but `close:x` is permanently lost. One missing `try`/`finally` is the entire difference between these two outcomes.
- **The happy path proves nothing**: with no exception, all three implementations produce the identical `["open:x", "close:x"]` -- confirmed directly, not just asserted, which is the reason this specific bug survives casual testing in real code.
- **Suppression is blanket for both protocols, confirmed with two unrelated exception types**: `suppress=True` swallows both a `ValueError` and a `TypeError` equally, for `ManagedResource` and `correct_resource` alike -- neither implementation has any mechanism that would suppress only the exception type a caller actually anticipated.
- **Reusability is asymmetric, confirmed directly, not assumed from "generators are one-shot"**: the same `ManagedResource` instance is reused across two independent `with` blocks and produces `["open:x", "close:x", "open:x", "close:x"]` -- fully reusable. The same `correct_resource(...)` *object* (not a fresh call to the factory) raises on a second `with` and never re-runs its own `open:` logic the second time, confirmed by checking the log is still exactly `["open:x", "close:x"]` after the failed second attempt, on this CPython version (3.9.6) -- the exact exception type is version-specific contextlib-internals and deliberately not pinned (see `problem.md`'s Constraints).
- **10 tests, 3/3 consecutive clean runs.**

## Follow-up Questions

**Why?**
`@contextlib.contextmanager` is reached for constantly because it turns a context manager into "write a generator," which looks like it should be strictly easier than writing a class with two dunder methods -- and on the happy path, it is. The exception path is where the simplicity is actually hiding a manual obligation (wrap `yield` in `try`/`finally` yourself) that the class-based protocol never asks for, because `with` calls `__exit__` unconditionally no matter what.

**How exactly?**
`with` is defined to call `__exit__` in a `finally`-equivalent way at the language level -- it's not something `ManagedResource.__exit__` has to opt into, it's guaranteed by the `with` statement's own bytecode regardless of what's inside the block. For a generator-based context manager, `contextlib.contextmanager`'s wrapper calls `gen.throw(exc)` (on exception) or `next(gen)` (on normal exit) to resume the generator past its `yield` -- but *what code runs* after being resumed is entirely up to what the generator itself wrote there. If that's a bare `yield` with nothing wrapping it, `gen.throw()` propagates the exception straight out of the generator at the `yield` line, and every line physically written after it (like `leaky_resource`'s `log.append("close:...")`) is simply never reached.

**What happens internally?**
Reusing an already-exhausted `_GeneratorContextManager` instance fails because its `__enter__` method (in this CPython version) deletes `self.args`, `self.kwds`, and `self.func` the first time it's called, after using them to construct the generator -- the second `__enter__` call tries to delete attributes that are already gone, raising `AttributeError` rather than something more descriptive like `RuntimeError("generator didn't yield")`. Confirmed by direct traceback inspection before writing the test, not assumed from documentation -- which is also why the test only asserts "raises *some* exception," not that specific one.

**What if this fails?**
- **Someone tests a generator-based context manager only on the happy path and ships it**: `leaky_resource` is exactly this scenario, and the only thing that reveals the bug is deliberately raising inside the `with` block -- `test_leaky_resource_propagates_exception_but_misses_cleanup` is that test.
- **Someone assumes `suppress=True` only swallows the specific exception they were thinking about when they wrote it**: `test_managed_resource_suppress_true_swallows_unrelated_exception_types_too` and its generator-based sibling both show an unrelated `TypeError` gets swallowed exactly as readily as the intended `ValueError`.
- **Someone reuses a generator-based context-manager object across two `with` blocks, expecting it to behave like a reusable class instance**: fails on the second `__enter__`, confirmed directly, and confirmed that the resource's own `open:` logic never ran a second time -- it's not that the second use does something subtly wrong, it's that it never starts.

**What trade-offs did you consider?**
- **A single `leaky_resource` included specifically as the wrong example (chosen) vs. only building the two correct implementations**: the exercise's actual point is the measured *gap* between "looks right" and "is right under exception," which only exists if the wrong version is built, run, and measured side-by-side with the correct one -- stating the bug in prose without a concrete `leaky_resource` object and a log to inspect would be exactly the kind of unfalsifiable claim this repo's house style avoids.
- **`except Exception: if not suppress: raise` (chosen) vs. no `except` at all, relying only on `finally` for cleanup and letting any exception propagate unconditionally**: the no-`except` version would still guarantee cleanup correctly (that's what `finally` is for), but it would never support `suppress=True` at all -- `correct_resource` needs the explicit `except` specifically to match `ManagedResource`'s suppression contract, not merely its cleanup guarantee.

**How do you evaluate it?**
- **10 tests, 3/3 consecutive clean runs.**
- **Mutation check, 3 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - `ManagedResource.__exit__` returns `False` unconditionally instead of `self.suppress`: 2/10 failed -- both suppression tests for the class-based version, exactly as expected.
  - `correct_resource` drops the `if not suppress:` check and always re-raises: 2/10 failed -- both suppression tests for the generator-based version.
  - `correct_resource`'s cleanup moved out of `finally`, placed after the `try`/`except` block instead: 1/10 failed (`test_correct_resource_guarantees_cleanup_and_propagates_exception`) -- the exception, once re-raised inside the `except` block, exits the function immediately, so an append written after the `try`/`except` (rather than inside a `finally`) never runs; the suppression-path tests didn't catch this one because when `suppress=True`, execution falls through normally either way.

## Key Learnings

- **The class-based and generator-based protocols aren't two equally-easy ways to write the same thing -- they make different things automatic.** `with` guarantees `__exit__` runs for you; `@contextlib.contextmanager` only guarantees the generator gets resumed, and what happens next is whatever you wrote. `leaky_resource` isn't a contrived bug; it's the default shape of a generator-based context manager written by someone who hasn't needed `try`/`finally` yet, which is exactly why it's worth measuring rather than just stating as a documentation footnote.
- Consistent with 2026-10-15's descriptor-parity day: the real finding isn't "here are two implementations of the same interface," it's the *specific, falsifiable place* where supposedly-equivalent implementations stop agreeing -- here, that place is the exception path and object reusability, not the happy path, which is exactly where neither prose nor a quick manual test would have caught it.
- The reuse-failure's exact exception type (`AttributeError`, not something clearer like `RuntimeError`) was worth confirming by traceback before writing the test rather than guessing from how `contextlib` *should* behave -- a second, smaller instance of the same discipline 2026-10-15 used for `AttributeError` wording, applied here to a different corner of the same module.
