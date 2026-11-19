"""HMAC webhook signature verification with replay protection --
measures the concrete gap between "signature verified" and "request is
safe to accept," and reuses 2026-11-06's constant-time-compare finding
in the one place it actually matters in a real protocol."""

from __future__ import annotations

import hashlib
import hmac
from typing import Callable, Dict


def canonical_message(payload: bytes, timestamp: int) -> bytes:
    return f"{timestamp}.".encode() + payload


def sign(payload: bytes, secret: bytes, timestamp: int) -> str:
    return hmac.new(secret, canonical_message(payload, timestamp), hashlib.sha256).hexdigest()


def naive_signatures_equal(a: str, b: str) -> bool:
    """Explicit early-exit comparison, included only to contrast with
    secure_signatures_equal below. Never the default -- see 2026-11-06.
    """
    if len(a) != len(b):
        return False
    for i in range(len(a)):
        if a[i] != b[i]:
            return False
    return True


def secure_signatures_equal(a: str, b: str) -> bool:
    """The actually-safe comparator for untrusted signatures."""
    return hmac.compare_digest(a, b)


class WebhookVerifier:
    def __init__(
        self,
        secret: bytes,
        tolerance: int,
        clock: Callable[[], int],
        dedup: bool = True,
        compare_fn: Callable[[str, str], bool] = secure_signatures_equal,
    ) -> None:
        self._secret = secret
        self.tolerance = tolerance
        self._clock = clock
        self.dedup = dedup
        self._compare_fn = compare_fn
        self._seen: Dict[str, int] = {}

    def verify(self, payload: bytes, timestamp: int, signature: str) -> bool:
        """Reject on stale/future timestamp, reject on signature
        mismatch, reject on replay when dedup is enabled; otherwise
        accept and (if dedup) remember the signature.
        """
        now = self._clock()
        if abs(now - timestamp) > self.tolerance:
            return False
        expected = sign(payload, self._secret, timestamp)
        if not self._compare_fn(expected, signature):
            return False
        if self.dedup:
            self._evict_stale(now)
            if signature in self._seen:
                return False
            self._seen[signature] = timestamp
        return True

    def _evict_stale(self, now: int) -> None:
        stale = [sig for sig, ts in self._seen.items() if abs(now - ts) > self.tolerance]
        for sig in stale:
            del self._seen[sig]

    @property
    def seen_count(self) -> int:
        return len(self._seen)
