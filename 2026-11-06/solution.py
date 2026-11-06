"""Timing-attack resistant string comparison: naive early-exit vs
constant-time, measured via a simulated (deterministic) timing side
channel, not real wall-clock time."""

from __future__ import annotations

from typing import Callable, Set, Tuple

FILLER = 0  # must never appear in the alphabet used by any caller


def naive_compare(a: bytes, b: bytes) -> Tuple[bool, int]:
    """Byte-by-byte comparison that returns False at the first mismatch.

    Returns (matched, ops): ops is the number of byte comparisons
    actually performed -- the simulated timing side channel.
    Raises ValueError if len(a) != len(b).
    """
    if len(a) != len(b):
        raise ValueError("naive_compare requires equal-length inputs")
    for i in range(len(a)):
        if a[i] != b[i]:
            return False, i + 1
    return True, len(a)


def constant_time_compare(a: bytes, b: bytes) -> Tuple[bool, int]:
    """Compares every byte regardless of where a mismatch occurs.

    ops is always len(a), whether it matches or not.
    Raises ValueError if len(a) != len(b).
    """
    if len(a) != len(b):
        raise ValueError("constant_time_compare requires equal-length inputs")
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0, len(a)


class TimingOracle:
    """Wraps a secret and a compare function; exposes only the ops
    count to a caller, never the match result."""

    def __init__(self, secret: bytes, compare_fn: Callable[[bytes, bytes], Tuple[bool, int]]) -> None:
        self._secret = secret
        self._compare_fn = compare_fn

    def probe(self, guess: bytes) -> int:
        """The attacker-visible signal only: the ops count for
        comparing `guess` against the secret. Never reveals whether it
        matched."""
        _, ops = self._compare_fn(guess, self._secret)
        return ops


def recover_secret(oracle: TimingOracle, length: int, alphabet: bytes) -> Tuple[bytes, Set[int]]:
    """Byte-at-a-time recovery using only `oracle.probe`'s ops signal.

    Returns (recovered, undetermined): undetermined is the set of
    positions where every alphabet candidate tied (no signal) -- the
    recovered byte at those positions is a placeholder, not a guess.
    """
    recovered = bytearray(length)
    undetermined: Set[int] = set()

    for pos in range(length):
        scores = {}
        for candidate in alphabet:
            guess = bytes(recovered[:pos]) + bytes([candidate]) + bytes([FILLER]) * (length - pos - 1)
            scores[candidate] = oracle.probe(guess)

        best = max(scores.values())
        winners = [b for b, ops in scores.items() if ops == best]
        if len(winners) == 1:
            recovered[pos] = winners[0]
        else:
            recovered[pos] = alphabet[0]
            undetermined.add(pos)

    return bytes(recovered), undetermined
