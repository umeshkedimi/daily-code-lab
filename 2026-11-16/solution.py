"""__slots__ memory savings, measured against a regular dict-backed
class -- including the two ways the saving quietly disappears:
subclassing a non-slotted base, and treating it as a one-time cost
instead of a per-instance one."""

from __future__ import annotations

import sys
from typing import Any, Iterable


class Regular:
    def __init__(self, x: int, y: int, z: int) -> None:
        self.x = x
        self.y = y
        self.z = z


class Slotted:
    __slots__ = ("x", "y", "z")

    def __init__(self, x: int, y: int, z: int) -> None:
        self.x = x
        self.y = y
        self.z = z


class SlottedChildOfPlain(Regular):
    __slots__ = ()


def instance_memory_bytes(obj: Any) -> int:
    """Actual heap footprint of one instance: the instance itself,
    plus its __dict__'s own size if it has one (sys.getsizeof(obj)
    alone doesn't follow that pointer).
    """
    total = sys.getsizeof(obj)
    instance_dict = getattr(obj, "__dict__", None)
    if instance_dict is not None:
        total += sys.getsizeof(instance_dict)
    return total


def total_memory_bytes(objs: Iterable[Any]) -> int:
    """instance_memory_bytes summed over objs."""
    return sum(instance_memory_bytes(obj) for obj in objs)


def allows_dynamic_attribute(obj: Any) -> bool:
    """True if assigning an attribute name declared nowhere on obj's
    class or its ancestors succeeds; False if it raises AttributeError.
    Leaves obj unchanged either way.
    """
    probe_name = "__dynamic_probe_attr__"
    try:
        setattr(obj, probe_name, 1)
    except AttributeError:
        return False
    delattr(obj, probe_name)
    return True
