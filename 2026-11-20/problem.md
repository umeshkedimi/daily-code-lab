# Problem

**Category:** `python`

## Statement

Build the same resource-management context manager two ways — a hand-written class with `__enter__`/`__exit__`, and a generator wrapped in `@contextlib.contextmanager` — and measure, rather than assume, where the two protocols actually agree and where they quietly diverge. Then build a third, deliberately buggy generator-based version that's missing exactly one thing (`try`/`finally` around `yield`) and measure the specific, real-world failure that single omission causes: it looks completely correct on the happy path and silently stops guaranteeing cleanup the moment an exception is raised inside the `with` block.

**Requirements:**

- `ManagedResource(log: List[str], name: str, suppress: bool = False)` — a class-based context manager. `__enter__` appends `"open:{name}"` to `log` and returns `self`. `__exit__` appends `"close:{name}"` to `log` and returns `suppress` (so `suppress=True` swallows whatever exception, if any, is in flight).
- `correct_resource(log: List[str], name: str, suppress: bool = False)` — a `@contextlib.contextmanager`-decorated generator with the same observable contract: appends `"open:{name}"` before `yield`, appends `"close:{name}"` in a `finally` block so cleanup is guaranteed regardless of what happens at the `yield`, and re-raises any exception unless `suppress` is `True`.
- `leaky_resource(log: List[str], name: str)` — a `@contextlib.contextmanager`-decorated generator **missing** the `try`/`finally`: appends `"open:{name}"`, `yield`s, then appends `"close:{name}"` — included specifically to measure what breaks, not to be used for anything else.

## Constraints

- Standard library only (`contextlib`).
- Reusing an already-exhausted `@contextlib.contextmanager` object for a second `with` block raises an exception — but which exception, and at exactly which internal line, is a CPython-version-specific implementation detail of `contextlib`'s generated wrapper (confirmed directly against the interpreter actually running this suite, the same pin 2026-10-15 used for `AttributeError` wording). The test for this only asserts that *some* exception is raised and that the attempted reuse never re-ran the resource's own `open:` logic — not the exact exception type.

## Approach

1. **The happy path can't tell any of the three apart.** With no exception raised inside the `with` block, `ManagedResource`, `correct_resource`, and `leaky_resource` all produce the identical log: `open` then `close`. This is worth stating plainly before measuring the failure, because it's exactly why the bug in `leaky_resource` is the kind that survives code review and a quick manual test — it only shows up once something inside the block actually raises.
2. **The measured gap, exact and falsifiable:** raise an exception inside a `with leaky_resource(...)` block. The exception still propagates correctly (nothing here breaks exception delivery) but the log stops at exactly `["open:x"]` — `close:x` is never appended, because a bare `yield` with nothing wrapping it lets the thrown exception exit the generator immediately, skipping every line physically written after the `yield`. `ManagedResource` and `correct_resource`, under the identical exception, both still produce `["open:x", "close:x"]` — the `with` statement's own guarantee to always call `__exit__` protects the class-based version automatically; the generator-based version needs its own `try`/`finally` to get the same guarantee, because nothing enforces it for you.
3. **Suppression is blanket, measured with two unrelated exception types, not asserted from one:** `suppress=True` on either `ManagedResource` or `correct_resource` swallows a `ValueError` *and* an unrelated `TypeError` equally — there's no mechanism in either implementation that suppresses only the exception type the caller actually anticipated. That's not a bug in this exercise's implementations specifically; it's the real shape of what "return a truthy value from `__exit__`" or "catch `Exception` and don't re-raise" actually does.
4. **Reusability is not symmetric between the two protocols, measured directly:** the same `ManagedResource` instance can be used in a second, independent `with` block after the first one finishes — `__enter__`/`__exit__` don't consume anything that a second use needs. The same generator-based context-manager *object* (not the factory function — calling `correct_resource(...)` again produces a fresh one) cannot: its underlying generator is already exhausted after one trip through `yield`, and reusing the object raises on the second `__enter__` rather than silently reopening the resource.

## Stretch

- `ManagedResource.__exit__` returning a truthy `suppress` swallows *any* `BaseException`, including ones `except Exception:`-based code (like `correct_resource`) would never catch at all (`SystemExit`, `KeyboardInterrupt`) — a sharper asymmetry than the one measured in Approach 3, not exercised here to avoid raising control-flow exceptions inside a test suite.
- `contextlib.ExitStack` for managing a variable number of context managers at once, rather than a fixed, known set.
- `@contextlib.asynccontextmanager`'s equivalent `async with` version of the same leaky-cleanup pitfall.
