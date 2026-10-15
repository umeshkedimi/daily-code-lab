# Problem

**Category:** `python`

## Statement

Reimplement Python's descriptor protocol from scratch: `property`, `classmethod`, and `staticmethod`, each via `__get__`/`__set__`/`__delete__`, checked for **exact behavioral parity** against the real builtins — not just "the obvious case works," but the same error-message wording on the no-setter/no-deleter path and the same attribute-lookup precedence as CPython's actual implementation.

The one fact this exercise exists to measure, not just state: `property` is a **data descriptor** (it defines `__set__`), while a plain function, `classmethod`, and `staticmethod` are all **non-data descriptors** (`__get__` only). That distinction decides who wins when an instance's own `__dict__` happens to have an entry under the same name as a descriptor on its class — and it's the actual mechanism behind a fact most people who use `@property` daily have never had to demonstrate: you can shadow a method by assigning `self.method = something_else`, but you can never shadow a property the same way.

**Requirements:**
- Every from-scratch descriptor must be checked against the real builtin doing the same thing, using the *same* test logic run against both — not two separately-written test suites that could silently drift apart or share a blind spot.
- The data-descriptor-vs-non-data-descriptor precedence claim must be demonstrated directly: poke a value into `instance.__dict__` under the same name as each kind of descriptor, and show which one wins — for both the from-scratch versions and the real builtins.
- A second, independently useful descriptor (`Validated`, using `__set_name__` from PEP 487) must solve a problem `property` alone doesn't: one reusable descriptor class, instantiated per validated attribute, with no duplicated getter/setter/validation boilerplate — and the amount of boilerplate actually avoided should be measured, not just claimed.
- The classic descriptor implementation mistake — storing state on the descriptor object itself instead of in the owning instance's `__dict__`, which would make every instance of a class share one value — must be explicitly tested against, not just avoided by accident.

## Constraints

- Standard library only — no imports beyond `typing` for annotations.
- Checked against whatever CPython version is actually running this suite for the exact `AttributeError` wording (which changed between Python versions) — the test documents which behavior it's pinning, rather than assuming a version-independent guarantee that doesn't exist.

## Approach

1. **`MyProperty`**: a data descriptor (`__get__`, `__set__`, `__delete__` all defined). `obj is None` on `__get__` means access via the class itself (`C.x`, not `C().x`) and returns the descriptor object — exactly how the real `property` supports `help(C.x)` and introspection. Missing setter/deleter raise `AttributeError` with the exact message the real builtin uses on this Python version. `.setter`/`.deleter`/`.getter` return a *new* `MyProperty` with one function swapped, mirroring the real decorator-chaining pattern (`@x.setter`).
2. **`MyClassMethod`** and **`MyStaticMethod`**: both non-data descriptors (only `__get__`). `MyClassMethod` binds the wrapped function's first argument to the class (`objtype` if accessing via the class, else `type(obj)` — so a subclass correctly receives itself, not its parent). `MyStaticMethod` does the least possible work: `__get__` returns the raw function, completely unbound, regardless of access path.
3. **The precedence demonstration, run against both real and from-scratch versions**: construct an instance, poke a same-named entry directly into `instance.__dict__`, and read the attribute back — a data descriptor (property) is unaffected by the poke; a non-data descriptor (classmethod, staticmethod, or a plain method) returns the poked value instead.
4. **`Validated`**: a single, reusable data descriptor class using `__set_name__` to learn its own attribute name automatically at class-body-execution time, storing the validated value in `obj.__dict__[f"_{name}"]` — per-instance, never on `self` (the descriptor object), which is the specific, commonly-made mistake that would silently share one value across every instance of the owning class.
5. **Measure the boilerplate actually avoided**: the same three validated attributes (a positive-number check, a one-of-a-fixed-set check, an adult-age check), written once by hand with `property` + setter + validation per attribute, and once with `Validated` — counted, not estimated.
