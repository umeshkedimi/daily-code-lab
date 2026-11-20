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
        self.log.append(f"open:{self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.log.append(f"close:{self.name}")
        return self.suppress


@contextlib.contextmanager
def correct_resource(log: List[str], name: str, suppress: bool = False) -> Iterator[str]:
    """Same observable contract as ManagedResource: cleanup guaranteed
    via try/finally, exception re-raised unless suppress is True.
    """
    log.append(f"open:{name}")
    try:
        yield name
    except Exception:
        if not suppress:
            raise
    finally:
        log.append(f"close:{name}")


@contextlib.contextmanager
def leaky_resource(log: List[str], name: str) -> Iterator[str]:
    """Deliberately missing try/finally around yield -- included only
    to measure what breaks, never to be used for anything else.
    """
    log.append(f"open:{name}")
    yield name
    log.append(f"close:{name}")
