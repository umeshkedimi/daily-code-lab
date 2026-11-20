"""Context manager protocol, two ways -- class-based __enter__/__exit__
vs @contextlib.contextmanager -- checked for parity, and for the one
real-world bug a missing try/finally around yield causes."""

from __future__ import annotations

import contextlib
from typing import Iterator, List


class ManagedResource:
    def __init__(self, log: List[str], name: str, suppress: bool = False) -> None:
        self.log = log
        self.name = name
        self.suppress = suppress

    def __enter__(self) -> "ManagedResource":
        raise NotImplementedError

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        raise NotImplementedError


@contextlib.contextmanager
def correct_resource(log: List[str], name: str, suppress: bool = False) -> Iterator[str]:
    """Same observable contract as ManagedResource: cleanup guaranteed
    via try/finally, exception re-raised unless suppress is True.
    """
    raise NotImplementedError
    yield  # pragma: no cover


@contextlib.contextmanager
def leaky_resource(log: List[str], name: str) -> Iterator[str]:
    """Deliberately missing try/finally around yield -- included only
    to measure what breaks, never to be used for anything else.
    """
    raise NotImplementedError
    yield  # pragma: no cover
