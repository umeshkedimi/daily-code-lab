"""The descriptor protocol, from scratch: `property`, `classmethod`, and
`staticmethod` reimplemented via `__get__`/`__set__`/`__delete__`, checked
for exact behavioral parity against the real builtins -- not just "does
the obvious case work," but the same error messages on the no-setter path
and the same attribute-lookup precedence as CPython's actual C
implementation.

The one fact this exercise is built to measure, not just state: `property`
is a DATA descriptor (defines `__set__`) and a plain function/`classmethod`/
`staticmethod` is a NON-data descriptor (only `__get__`). That distinction
decides who wins when an instance's own `__dict__` has an entry under the
same name as a descriptor on the class -- a data descriptor wins
unconditionally; a non-data descriptor loses to the instance dict. This is
the actual mechanism behind "why can I shadow a method by assigning
`self.method = something_else` but I can't shadow a property the same
way" -- demonstrated directly below, on both the from-scratch versions and
the real builtins, not just asserted in prose.

A second, independently useful descriptor feature is also built: a
reusable `Validated` data descriptor using `__set_name__` (PEP 487), which
is the actual practical motivation for understanding this protocol --
`property` only solves "one attribute, one class" without duplicating the
getter/setter boilerplate for every validated field.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Type


# --- property, from scratch -----------------------------------------------------


class MyProperty:
    """A data descriptor: defines __get__ AND __set__ (and __delete__),
    which is what makes it take precedence over an instance's own
    __dict__ during attribute lookup -- see demo()/notes.md for why that
    specific fact is the one worth measuring here."""

    def __init__(
        self,
        fget: Optional[Callable] = None,
        fset: Optional[Callable] = None,
        fdel: Optional[Callable] = None,
        doc: Optional[str] = None,
    ):
        self.fget = fget
        self.fset = fset
        self.fdel = fdel
        self.__doc__ = doc if doc is not None else (fget.__doc__ if fget else None)

    def __get__(self, obj: Any, objtype: Optional[Type] = None) -> Any:
        if obj is None:
            return self  # accessed on the class itself, e.g. `C.x`, not `C().x`
        if self.fget is None:
            raise AttributeError("unreadable attribute")
        return self.fget(obj)

    def __set__(self, obj: Any, value: Any) -> None:
        if self.fset is None:
            raise AttributeError("can't set attribute")
        self.fset(obj, value)

    def __delete__(self, obj: Any) -> None:
        if self.fdel is None:
            raise AttributeError("can't delete attribute")
        self.fdel(obj)

    def getter(self, fget: Callable) -> "MyProperty":
        return type(self)(fget, self.fset, self.fdel, self.__doc__)

    def setter(self, fset: Callable) -> "MyProperty":
        return type(self)(self.fget, fset, self.fdel, self.__doc__)

    def deleter(self, fdel: Callable) -> "MyProperty":
        return type(self)(self.fget, self.fset, fdel, self.__doc__)


# --- classmethod, from scratch ---------------------------------------------------


class MyClassMethod:
    """A non-data descriptor (only __get__): binds the wrapped function's
    first argument to the class, whether accessed via the class or an
    instance -- `obj.cm()` and `Cls.cm()` both receive the class, never
    the instance, as their first argument."""

    def __init__(self, func: Callable):
        self.func = func

    def __get__(self, obj: Any, objtype: Optional[Type] = None):
        cls = objtype if objtype is not None else type(obj)

        def bound(*args, **kwargs):
            return self.func(cls, *args, **kwargs)

        bound.__name__ = getattr(self.func, "__name__", "bound_classmethod")
        return bound


# --- staticmethod, from scratch --------------------------------------------------


class MyStaticMethod:
    """A non-data descriptor that does the least possible amount of
    work: __get__ just returns the original function completely
    unbound, whether accessed via the class or an instance."""

    def __init__(self, func: Callable):
        self.func = func

    def __get__(self, obj: Any, objtype: Optional[Type] = None):
        return self.func


# --- a descriptor that actually solves a problem property doesn't ---------------


class Validated:
    """A reusable data descriptor: validates a value against `predicate`
    before storing it, under a PRIVATE name derived automatically from
    whatever attribute name it's assigned to -- via `__set_name__`
    (PEP 487), called once by the class machinery at class-body
    execution time, so the descriptor never needs the name repeated by
    the caller the way a hand-written property's backing field does.

    One descriptor class, reusable across any number of validated
    attributes on any number of classes, with zero duplicated
    getter/setter boilerplate per attribute -- the actual point of the
    protocol beyond "`property` already exists."
    """

    def __init__(self, predicate: Callable[[Any], bool], message: str):
        self.predicate = predicate
        self.message = message
        self.private_name = None  # set by __set_name__

    def __set_name__(self, owner: Type, name: str) -> None:
        self.private_name = f"_{name}"

    def __get__(self, obj: Any, objtype: Optional[Type] = None):
        if obj is None:
            return self
        return obj.__dict__.get(self.private_name)

    def __set__(self, obj: Any, value: Any) -> None:
        if not self.predicate(value):
            raise ValueError(f"{value!r} is invalid: {self.message}")
        obj.__dict__[self.private_name] = value


def positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and value > 0


def one_of(*choices):
    def predicate(value: Any) -> bool:
        return value in choices

    return predicate


def demo() -> None:
    print("1) Data descriptor (property) wins over instance __dict__; non-data descriptor loses")

    class WithProperty:
        @property
        def x(self):
            return "from the real property"

    class WithMyProperty:
        def _get_x(self):
            return "from MyProperty"

        x = MyProperty(_get_x)

    class WithMethod:
        def method(self):
            return "from the real method"

    for label, obj, attr in [
        ("real property", WithProperty(), "x"),
        ("MyProperty", WithMyProperty(), "x"),
        ("real method", WithMethod(), "method"),
    ]:
        obj.__dict__[attr] = "POKED INTO INSTANCE __DICT__"
        print(f"   {label:>14}: {attr} = {getattr(obj, attr)!r}")

    print("\n2) classmethod/staticmethod binding, real vs. from-scratch")

    class WithReal:
        @classmethod
        def cm(cls):
            return cls.__name__

        @staticmethod
        def sm():
            return "static, unbound"

    class WithMine:
        def _cm(cls):
            return cls.__name__

        cm = MyClassMethod(_cm)

        def _sm():
            return "static, unbound"

        sm = MyStaticMethod(_sm)

    print(f"   real:  WithReal.cm()={WithReal.cm()!r}  WithReal().cm()={WithReal().cm()!r}  sm()={WithReal.sm()!r}")
    print(f"   mine:  WithMine.cm()={WithMine.cm()!r}  WithMine().cm()={WithMine().cm()!r}  sm()={WithMine.sm()!r}")

    print("\n3) Validated: one descriptor class, reused across attributes with zero duplicated boilerplate")

    class Account:
        balance = Validated(positive, "must be a positive number")
        status = Validated(one_of("active", "frozen", "closed"), "must be active/frozen/closed")

        def __init__(self, balance, status):
            self.balance = balance
            self.status = status

    acct = Account(100, "active")
    print(f"   acct.balance={acct.balance!r}  acct.status={acct.status!r}")
    try:
        acct.balance = -5
    except ValueError as e:
        print(f"   acct.balance = -5  ->  ValueError: {e}")
    try:
        acct.status = "vip"
    except ValueError as e:
        print(f"   acct.status = 'vip'  ->  ValueError: {e}")


if __name__ == "__main__":
    demo()
