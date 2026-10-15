## Implementation

- `MyProperty.__get__` checks `obj is None` first and returns `self` in that case -- this is what makes `C.x` (accessed on the class) return the descriptor object itself rather than trying to call a getter with no instance to call it on, and it's exactly how the real `property` supports things like `help(C.x)` or re-wrapping it with `.setter`.
- `MyProperty.__set__`/`__delete__` raise `AttributeError("can't set attribute")` / `AttributeError("can't delete attribute")` when no `fset`/`fdel` was provided -- pinned to CPython 3.9's exact wording (verified directly against the real `property` before writing a single line of `MyProperty`, not assumed). Later CPython versions reworded this to include the attribute and class name; this test suite documents which behavior it's checking, not a version-independent guarantee that doesn't exist.
- `.setter`/`.deleter`/`.getter` each return a **new** `MyProperty` instance with exactly one function swapped -- matching the real property's behavior where `@x.setter` doesn't mutate `x`, it produces a new descriptor that happens to share the unchanged functions.
- `MyClassMethod.__get__` uses `objtype` when it's given (access via the class) and falls back to `type(obj)` only when accessed via an instance -- both paths end up binding to "the class involved in this lookup," but getting this right matters specifically for subclasses: `Derived.cm()` must bind to `Derived`, not to `Base` just because that's where the descriptor is physically defined.
- `MyStaticMethod.__get__` does the least possible amount of work on purpose: it returns `self.func`, completely unbound, every time -- there's no binding logic to get wrong because there's no binding at all.
- `Validated.__set_name__` is called once, automatically, by the class-creation machinery, the moment the class body finishes executing -- `self.private_name` is set from the attribute name the descriptor was assigned to, so the descriptor never needs the caller to pass the name redundantly (contrast with a hand-written `property`, where the backing-field name `_balance` is typed out by hand in three separate places: the getter, the setter, and implicitly the convention itself).
- `Validated.__get__`/`__set__` read and write `obj.__dict__[self.private_name]`, **never** `self.anything` -- storing on `self` (the descriptor instance, which is a single shared object living on the *class*, not the instance) is the single most common descriptor-implementation mistake, and would make every instance of the owning class share one value. Verified directly (`test_two_instances_of_the_same_class_have_independent_values`), and verified again for the specific case of reusing one `Validated` object across two unrelated classes (`test_the_same_validated_instance_reused_across_two_classes_stays_independent`).
- Not built: descriptor caching (a `cached_property`-style descriptor that only computes once and then stores the result, replacing itself in `__dict__` on first access) -- a genuinely different and interesting descriptor pattern, but a distinct exercise from the parity-checking and precedence-demonstration this one is built around; multiple inheritance interactions with `__set_name__` order (out of scope, since the relevant precedence rules here don't depend on MRO specifics); `__set_name__` error handling (PEP 487 defines what happens if `__set_name__` itself raises during class creation -- not exercised here).

## Measured Results

- **Precedence, demonstrated directly** (poke `instance.__dict__["x"] = ...` under the same name as a class-level descriptor, then read `instance.x`):
  - `MyProperty` (data descriptor): returns the descriptor's own value, poke ignored. Same result for the real `property`.
  - `MyClassMethod`, `MyStaticMethod`, and a plain method (all non-data descriptors): return the poked value, descriptor ignored. Same result for the real `classmethod`, `staticmethod`, and an undecorated method.
- **Boilerplate avoided, counted, not estimated**: the same three validated attributes (positive-number check, one-of-a-fixed-set check, adult-age check), written by hand with `property` + setter + validation per attribute, took **24 non-blank lines**; written with `Validated` (one instantiation per attribute, same validation logic), **3 lines** — an 8x reduction for this specific example, and the gap widens linearly with every additional validated attribute added (each hand-rolled attribute costs a fixed ~8 lines; each `Validated` attribute costs exactly 1).
- **27 tests, 5/5 consecutive clean runs.**

## Follow-up Questions

**Why?**
`@property`, `@classmethod`, and `@staticmethod` are used constantly without most people ever needing to know *how* they intercept attribute access — until a bug (an unexpectedly shadowed method, a validator that doesn't fire, a cached value that leaks across instances) forces the question. Understanding the mechanism turns "huh, weird" into a one-line diagnosis.

**How exactly?**
`type(obj).__getattribute__` (the thing that actually runs for `obj.attr`) checks the MRO for a *data* descriptor first; if one exists, its `__get__` wins outright, before `obj.__dict__` is even consulted. Otherwise, `obj.__dict__` is checked. Only if that misses does the MRO get checked again, this time accepting a *non-data* descriptor (or a plain class attribute). This two-pass structure, not any special-casing of `property` specifically, is the entire mechanism.

**Which algorithm?**
Not an algorithm — the descriptor protocol (`__get__`, `__set__`, `__delete__`, `__set_name__`), a core CPython object-model mechanism formalized in PEP 252 (descriptors) and PEP 487 (`__set_name__`), underlying not just `property`/`classmethod`/`staticmethod` but also `functools.cached_property`, ORMs' model fields, and `__slots__`.

**Which library?**
Standard library only — no imports beyond `typing` for type hints. The entire exercise is reimplementing machinery that itself has zero external dependencies.

**What happens internally?**
`type.__setattr__` during class-body execution is what calls `__set_name__` on every descriptor found in the new class's `__dict__`, once, right after the class object itself is fully constructed — this is why `Validated` never needs the name passed explicitly: by the time any instance of the class exists, every one of its `Validated` attributes already knows its own name. Function objects are themselves non-data descriptors (they define `__get__`, which is exactly what turns `instance.method` into a *bound* method instead of a plain function) — `MyClassMethod` and `MyStaticMethod` aren't special-cased by the language, they're just two different `__get__` implementations.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **An instance's `__dict__` is poked directly, bypassing `__setattr__` entirely** (e.g. `obj.__dict__["x"] = ...`, as every precedence test here does): a data descriptor is still unaffected on read, because `__getattribute__`'s data-descriptor check happens *before* the instance `__dict__` lookup, not after — verified for both the from-scratch and real versions of every descriptor kind tested.
- **A `classmethod` is called on a subclass**: must receive the subclass, not the class where the method is physically defined — verified directly (`test_classmethod_receives_the_subclass_when_called_on_a_subclass`), since a naive implementation that captured the defining class instead of using `objtype`/`type(obj)` at call time would get this wrong.
- **The same `Validated` descriptor object is reused across two unrelated classes**: each class's instances still store independently, because the storage lives in `obj.__dict__`, never on the descriptor — verified directly, specifically because this is the scenario that would expose state accidentally stored on `self` instead.
- **An invalid value is assigned after a valid one**: the old valid value must survive untouched — verified (`test_invalid_value_raises_value_error_and_does_not_mutate_the_stored_value`), which also implicitly confirms `__set__` validates *before* writing to `obj.__dict__`, not after.

**What trade-offs did you consider?**
- **Checking parity against the real builtins directly, in the same test (chosen) vs. two separately-written test suites, one per implementation:** two separate suites could each individually pass while silently testing slightly different things, or share a blind spot neither author noticed; running identical assertions against both in one test makes any divergence an immediate, specific failure rather than a quiet gap.
- **`Validated` storing into `obj.__dict__[private_name]` (chosen) vs. a `WeakKeyDictionary` keyed by `obj` living on the descriptor:** the `WeakKeyDictionary` approach also correctly avoids the "state shared across every instance" mistake, and is the standard alternative when the owning object doesn't support arbitrary attribute storage (e.g. it uses `__slots__`) -- but it adds a dict lookup and weak-reference bookkeeping cost for every access, for no benefit on an ordinary object that already has a `__dict__`. Noted as the fallback this implementation deliberately doesn't need.
- **One reusable `Validated` class with an injected predicate (chosen) vs. a `PositiveNumber`/`OneOf`/`AdultAge` subclass per validation rule:** subclassing per rule reads slightly more descriptively at the call site, at the cost of a new class definition for every new validation rule; the injected-predicate version needs zero new classes ever, trading a small amount of call-site clarity for the DRY-ness this exercise is specifically measuring.

**How do you debug it?**
- For "my descriptor isn't intercepting attribute access the way I expect," `type(obj).__mro__` combined with checking whether the descriptor defines `__set__` (data) or only `__get__` (non-data) answers "who should win" before touching a debugger at all — the precedence rule is static, derivable from the class definition alone, not something that needs to be observed at runtime.
- For a `Validated`-style descriptor silently sharing state across instances, printing `id(descriptor_instance)` vs `id(obj)` across two different instances immediately shows whether storage ended up keyed by the (single, shared) descriptor or by the (per-instance) object — exactly the check `test_two_instances_of_the_same_class_have_independent_values` automates.
- For a `classmethod`/`staticmethod` binding bug, printing what `__get__` actually returns (a bound method? the raw function? something else?) before it's ever called isolates the bug to the descriptor's binding logic, separate from whatever the wrapped function itself does.

**How do you evaluate it?**
- **27 tests, 5/5 consecutive clean runs.**
- **Mutation check, 5 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - `MyProperty.__get__` drops the `obj is None` (class-access) branch entirely: 2/27 failed.
  - `MyClassMethod` ignores `objtype` and always binds to `type(obj)`: 3/27 failed.
  - `MyStaticMethod` accidentally binds like a classmethod instead of returning the raw function: 1/27 failed.
  - `Validated.__set_name__` drops the leading underscore (`private_name = name` instead of `f"_{name}"`): 2/27 failed.
  - `Validated.__set__`/`__get__` store state on `self` (the descriptor) instead of `obj.__dict__` -- the classic pitfall, reintroduced deliberately: 2/27 failed.
- No zero-failure mutations this time, continuing the pattern from 2026-10-14's TOTP exercise rather than reverting to the zero-failure-requiring-investigation pattern from several problems before that -- worth noting again that this varies by exercise, not by any general rule about how thorough mutation testing "should" be.

## Key Learnings

- **The data-descriptor/non-data-descriptor split is not a property of `property` specifically -- it's a direct, mechanical consequence of which dunder methods a class happens to define, and the real builtins and the from-scratch versions agree on every single precedence case tested because they're implementing the identical rule, not independently converging on the same answer.** This is worth stating plainly because it's easy to think of `property`'s precedence-over-instance-`__dict__` as a special carve-out CPython grants specifically to `property` -- it isn't. Any object with `__get__` *and* `__set__` gets the same treatment, which is exactly why `Validated` (a completely unrelated descriptor, built for a different purpose) exhibits the identical precedence behavior with zero extra code written to make that happen.
- **Measuring the boilerplate `Validated` avoids (24 lines → 3, for three attributes) turns "descriptors reduce duplication" from a textbook claim into a number that scales with a *specific*, countable thing: one hand-rolled validated attribute costs a roughly fixed ~8 lines (getter, setter decorator, setter signature, validation check, raise, assignment), while one `Validated` instantiation costs exactly 1 — the gap isn't a one-time constant, it grows with every additional attribute, which is the actual argument for building the reusable descriptor instead of accepting the duplication.
