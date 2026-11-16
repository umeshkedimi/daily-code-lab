## Implementation

- `instance_memory_bytes` is the one function the whole exercise hinges on: `sys.getsizeof(obj)` alone reports only the instance's own header and inline slots, not objects it merely points to. A `Regular` instance's `__dict__` is a separate heap object referenced by a pointer -- skip `getattr(obj, "__dict__", None)` and add its size, and the comparison silently favors `Regular` by undercounting it, not by measuring anything real.
- The `getattr(obj, "__dict__", None)` check (rather than `hasattr`) does double duty: it's `None` for `Slotted` (no dict at all) and a real dict for both `Regular` and `SlottedChildOfPlain` -- the same code path handles "no dict" and "has a dict" without a second branch.
- `allows_dynamic_attribute` uses `setattr`/`delattr` with a fixed probe name (`__dynamic_probe_attr__`) rather than attribute-access syntax, so the same function works uniformly regardless of what `obj`'s own declared attributes are named. `delattr` only runs after a successful `setattr`, so a `Slotted` instance (which raises before anything is written) is never touched at all, and a dict-backed instance is restored to exactly its prior state -- checked directly (`test_allows_dynamic_attribute_leaves_no_probe_attribute_behind`).
- `SlottedChildOfPlain(Regular)` needs no `__init__` of its own -- it inherits `Regular.__init__` unchanged, which is exactly the point: `__slots__ = ()` on the subclass adds nothing, removes nothing, and the subclass still ends up with a `__dict__` because `Regular` (an ancestor in its MRO) never had slots to begin with.
- Not built: `__weakref__` slot interaction, multiple-inheritance slot-layout conflicts, a `tracemalloc`-based cross-check -- all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `instance_memory_bytes` | O(1) | O(1) |
| `total_memory_bytes` | O(n) | O(1) |
| `allows_dynamic_attribute` | O(1) | O(1) |

## Measured Results

(CPython 3.9.6, this machine -- absolute byte counts are implementation/version-specific; see `problem.md`'s Constraints for why the tests only assert relative/structural facts, not these literals.)

- **Per-instance footprint**, three `int` attributes, same values: `Regular` = 48 (`sys.getsizeof`) + 104 (its `__dict__`) = **152 bytes**. `Slotted` = **56 bytes**, no separate dict to add. That's a **96-byte, 63.2% smaller** footprint per instance.
- **The saving is exactly linear, not a flattening one-time cost**: for 50 instances of each, the aggregate gap is **4800 bytes** -- exactly `50 × 96`, not more, not less. Checked at two different counts (10 and 40) before writing either into a test; both came out exactly proportional.
- **The subclassing pitfall, measured, not asserted**: `SlottedChildOfPlain(Regular)` with `__slots__ = ()` has a footprint of **152 bytes** -- identical to `Regular`, not closer to `Slotted`. Declaring slots on a subclass of an unslotted class buys exactly zero bytes.
- **The flip side**: `allows_dynamic_attribute` returns `True` for `Regular` and `SlottedChildOfPlain` (both have a `__dict__` to absorb an undeclared attribute), `False` for `Slotted` (no `__dict__`, nowhere for an undeclared name to go).
- **11 tests, 3/3 consecutive clean runs.**

## Follow-up Questions

**Why?**
"`__slots__` saves memory" is quoted constantly and checked rarely -- the two ways it quietly stops being true (an unslotted ancestor; mistaking a per-instance saving for a one-time one) are exactly the ways someone adds `__slots__` to a hot-path class, sees no improvement, and concludes the technique doesn't work, when actually it was never in effect.

**How exactly?**
A class without `__slots__` stores its instance attributes in a per-instance `__dict__`, created lazily and referenced by a pointer from the instance's C struct. A class with `__slots__` instead reserves fixed, named pointer slots directly inside the instance's own struct -- no dict, no extra heap object, no hashing to look an attribute up. Removing the dict (not shrinking it) is where the saving comes from; it's categorical, not incremental.

**What happens internally?**
`type.__new__`, while building a class whose body contains `__slots__`, creates one descriptor per declared slot name (a data descriptor, in the same sense as 2026-10-15's `MyProperty` -- it defines `__get__`/`__set__`/`__delete__`, just backed by a fixed C-struct offset instead of a function call) and -- critically -- does **not** add `__dict__` to the new type's instance layout, unless some ancestor already has one. `SlottedChildOfPlain` inherits `Regular`'s instance layout, which already includes a `__dict__` slot; adding `__slots__ = ()` on top changes nothing about that inherited layout.

**What if this fails?**
- **Someone subclasses an unslotted class and expects slots to start saving memory from that point on**: measured directly (`test_slotted_subclass_of_unslotted_base_gets_zero_memory_saving`) -- it doesn't; the subclass's footprint equals the unslotted base's exactly.
- **Someone measures the saving once, at the class level, and assumes it as a fixed constant**: measured directly at two different instance counts (`test_aggregate_saving_at_two_different_counts_is_proportional`) -- the saving is `N × per-instance gap`, so "how much does slots save us" only has a meaningful answer once you know how many instances you're creating.
- **Someone forgets that slots also remove dynamic-attribute assignment**: measured directly (`test_slotted_rejects_assigning_an_undeclared_attribute`) -- any code path that relies on setting an attribute not declared in `__slots__` (a common pattern for caching a computed value onto `self`) breaks with `AttributeError`, not a warning.

**What trade-offs did you consider?**
- **Relative/structural test assertions (chosen) vs. hardcoded byte literals observed on this machine:** hardcoded numbers would be the most direct possible test, but `sys.getsizeof`'s output is CPython-build-and-version-specific (confirmed this matters by the same reasoning 2026-10-15's `AttributeError`-wording pin used) -- a byte literal baked in today could turn a passing suite into a false failure on a different patch release for reasons having nothing to do with whether `__slots__` still works. Relative claims (`A < B`, exact proportionality) are just as falsifiable and don't expire.
- **`setattr`/`delattr` with a probe name (chosen) vs. reading `obj.__slots__` or `type(obj).__mro__` to statically determine whether dynamic assignment is possible:** the static approach would avoid actually mutating `obj`, but it would need to correctly walk the whole MRO and reason about every ancestor's `__dict__`/`__slots__` combination by hand -- exactly the kind of reasoning this exercise is trying to verify empirically rather than trust. Actually attempting the assignment and observing whether it raises is the same check CPython itself performs, so it can't be wrong about what CPython will actually do.

**How do you evaluate it?**
- **11 tests, 3/3 consecutive clean runs.**
- **Measured headline numbers**: 152 bytes (`Regular`) vs. 56 bytes (`Slotted`) per instance (63.2% smaller); the gap scales exactly linearly (4800 bytes saved across 50 instances, exactly `50 × 96`); `SlottedChildOfPlain` measures identically to `Regular` (152 bytes), not `Slotted` -- all three are comparable, falsifiable numbers, not a single "yes it's smaller" checkbox.
- **Mutation check, 2 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - `instance_memory_bytes` reverted to the naive `sys.getsizeof(obj)` alone (drops the `__dict__` size entirely): 3/11 failed -- both the headline comparison and the linear-scaling test, since the "regular is bigger" claim they depend on silently became false once the dict size stopped being counted.
  - `allows_dynamic_attribute` drops its `delattr` cleanup: 1/11 failed -- only the test built specifically to catch this (`test_allows_dynamic_attribute_leaves_no_probe_attribute_behind`), confirming that test earns its place rather than duplicating one of the others.

## Key Learnings

- The actual mechanism ("no `__dict__` in the instance layout" vs. "a smaller `__dict__`") matters more than the headline number: it's what makes the subclassing pitfall *predictable* rather than a separate fact to memorize. Once "slots removes the dict pointer from the instance's C struct, but only if nothing in the MRO already put one there" is understood as the single underlying rule, "subclassing an unslotted class gives zero saving" stops being a gotcha and becomes the obviously-true consequence of that one rule.
- Verifying the exact-linear-scaling claim with a throwaway script *before* writing it into `test_aggregate_saving_scales_exactly_linearly_with_instance_count` (rather than assuming "roughly proportional" and writing a loose `>=` assertion) is what let the test assert `==` instead of a fuzzier bound -- the same discipline that avoided a bug on the timing-attack and context-truncation days, applied here to a claim about CPython's memory layout instead of an algorithm's behavior.
- Consistent with 2026-10-15's descriptor-parity day: a version-dependent implementation detail (there, `AttributeError` wording; here, `sys.getsizeof` byte counts) doesn't mean "don't test it" -- it means test the part of the claim that's actually stable (the *relationship* between the numbers) instead of the part that isn't (their *literal values*).
