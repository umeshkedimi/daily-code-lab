# Problem

**Category:** `python`

## Statement

Measure, not assume, the memory saving `__slots__` actually gives a class over a regular dict-backed one — and specifically measure the two ways that saving quietly stops being true even though the class still says `__slots__ = (...)`: subclassing a class that doesn't have slots, and treating the saving as a fixed one-time cost instead of something that scales with how many instances exist.

The question this exercise answers with a number, not a quote from the docs: **for a class with a handful of plain attributes, how much smaller is one instance, and does that gap actually multiply the way "per instance" implies, or does it secretly flatten out?**

**Requirements:**

- `Regular(x, y, z)` — an ordinary class, no `__slots__`, three attributes assigned in `__init__`.
- `Slotted(x, y, z)` — identical attributes and `__init__` body, but declares `__slots__ = ("x", "y", "z")`.
- `SlottedChildOfPlain(Regular)` — subclasses `Regular` and declares `__slots__ = ()` (no new attributes of its own), inheriting `Regular`'s `__init__`. This is the common mistake made concrete: adding `__slots__` to a subclass of a class that doesn't have it.
- `instance_memory_bytes(obj) -> int` — the actual heap footprint of one instance. `sys.getsizeof(obj)` alone **undercounts** any object that has a `__dict__`, because the dict is a separate heap-allocated object referenced by a pointer, not inlined into the instance — this function must add `sys.getsizeof(obj.__dict__)` whenever `obj` has one, and must not add anything when it doesn't (a slotted instance with no inherited dict).
- `total_memory_bytes(objs) -> int` — `instance_memory_bytes` summed over an iterable, for measuring the effect at scale instead of one instance at a time.
- `allows_dynamic_attribute(obj) -> bool` — attempts to assign an attribute name that is declared nowhere on `obj`'s class or its ancestors; returns `True` if the assignment succeeds, `False` if it raises `AttributeError`. Must leave `obj` exactly as it found it either way (no leftover probe attribute on success).

## Constraints

- Standard library only (`sys`).
- `sys.getsizeof` returns CPython-implementation-specific, version-and-build-dependent byte counts — there is no portable "slots save exactly N bytes" number. The test suite therefore makes only **relative** and **structural** claims (A is smaller than B; a scaled count is exactly proportional) that hold within one interpreter run, rather than hardcoding byte literals observed on one machine — the same reasoning 2026-10-15's `AttributeError`-wording pin used for a different version-dependent detail.
- All instances compared against each other must be constructed the same way (same attribute count, same insertion order), so that any underlying dict each one owns is sized identically — otherwise a scaling claim would be comparing differently-shaped dicts by accident.

## Approach

1. **Why `sys.getsizeof(obj)` alone is the wrong measurement for a dict-backed instance:** it reports only the instance header plus its slot/pointer layout, not objects the instance merely *points to*. A `Regular` instance's three attributes live in `obj.__dict__`, a separate dict object elsewhere on the heap — `instance_memory_bytes` has to chase that pointer and add its size, or the comparison silently favors `Regular` by omission rather than by fact.
2. **The headline comparison:** `instance_memory_bytes(Slotted(...))` must come out strictly smaller than `instance_memory_bytes(Regular(...))` for the same three attribute values — slots replace a pointer-to-a-dict with fixed, inline pointer slots, and inline is cheaper than indirect-plus-a-whole-dict-object for a small, fixed attribute set.
3. **The saving is per instance, not per class:** building `N` instances of each and comparing `total_memory_bytes` must show the aggregate gap is **exactly** `N ×` the single-instance gap — not roughly proportional, not flattening out as `N` grows. Verified at two different values of `N` before writing it into a test, specifically to rule out the saving being some fixed one-time class-level overhead that would show up once and then stop scaling.
4. **The subclassing pitfall, measured directly:** `SlottedChildOfPlain` declares `__slots__ = ()`, yet still has a `__dict__` — inherited from `Regular`, which never had slots in the first place. Its `instance_memory_bytes` must come out **equal to** `Regular`'s, not `Slotted`'s: declaring slots on a subclass buys nothing if any ancestor in the MRO lacks them, and "equal to the no-slots class" is the falsifiable way to say that rather than just asserting it.
5. **The trade-off slots make you pay for:** `Regular` and `SlottedChildOfPlain` both still have a `__dict__`, so both allow assigning an attribute name declared nowhere (`allows_dynamic_attribute` returns `True`). `Slotted` has no `__dict__` at all, so the identical assignment raises `AttributeError` (`allows_dynamic_attribute` returns `False`) — the other side of the same mechanism that produces the memory saving in the first place.

## Stretch

- `__weakref__`: a pure-slots class (no inherited `__dict__`) also can't be weakly referenced unless `__weakref__` is explicitly added to its `__slots__` — a second, independent cost of the same mechanism, not explored here.
- Two unrelated slotted classes combined via multiple inheritance can raise `TypeError: multiple bases have instance lay-out conflict` — not exercised here, since it needs two non-trivial, incompatible slot layouts to trigger.
- Cross-checking `sys.getsizeof`-based numbers against `tracemalloc`'s actual allocation tracking, to see whether they agree on the size of the gap or only on its direction.
