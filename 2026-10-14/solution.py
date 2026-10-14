"""TOTP (RFC 6238, built on HOTP/RFC 4226) from scratch: HMAC-based one-
time codes derived from a shared secret and the current time, validated
against the algorithm's own published test vectors -- not just internal
self-consistency.

TOTP generation alone is NOT a complete authentication defense. Two
things it does not give you, built and measured separately here:
1. Replay protection: TOTP is stateless by definition -- nothing stops the
   same valid code being submitted twice within its own validity window
   unless the verifier itself tracks what's already been consumed.
2. Brute-force resistance: a 6-digit code is a 1-in-a-million guess, which
   sounds secure until multiplied by how many guesses an unthrottled
   attacker can submit per second. Measured below, with and without a
   simple attempt-lockout defense, for exactly this reason.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# --- HOTP (RFC 4226): the primitive TOTP is built on ------------------------------


def hotp(secret: bytes, counter: int, *, digits: int = 6, digest=hashlib.sha1) -> str:
    """HMAC(secret, counter) -> dynamically-truncated decimal code.

    `counter` is packed as an 8-byte big-endian integer (RFC 4226, section 5.2).
    Dynamic truncation (RFC 4226, section 5.3): the low nibble of the digest's
    last byte picks a 4-byte offset into the digest; the top bit of those
    4 bytes is masked off (keeps the result a non-negative 31-bit int,
    avoiding sign-related cross-platform inconsistency); the result is
    reduced mod 10^digits and zero-padded to a fixed width.
    """
    if counter < 0:
        raise ValueError(f"counter must be non-negative, got {counter}")
    counter_bytes = struct.pack(">Q", counter)
    mac = hmac.new(secret, counter_bytes, digest).digest()
    offset = mac[-1] & 0x0F
    truncated = mac[offset : offset + 4]
    code_int = struct.unpack(">I", truncated)[0] & 0x7FFFFFFF
    code = code_int % (10**digits)
    return str(code).zfill(digits)


# --- TOTP (RFC 6238): HOTP with the counter derived from wall-clock time --------


@dataclass
class TOTP:
    secret: bytes
    digits: int = 6
    period: int = 30
    digest: Callable = hashlib.sha1
    clock: Callable[[], float] = time.time

    @staticmethod
    def generate_secret(length: int = 20) -> bytes:
        return secrets.token_bytes(length)

    @staticmethod
    def secret_to_base32(secret: bytes) -> str:
        """Base32 is the standard encoding for manual entry / QR-code
        provisioning (RFC 6238's own examples use it) -- plain base64
        includes characters (`+`, `/`) that read ambiguously when
        hand-typed or spoken aloud; base32's alphabet avoids that."""
        return base64.b32encode(secret).decode("ascii")

    @staticmethod
    def secret_from_base32(encoded: str) -> bytes:
        padding = "=" * (-len(encoded) % 8)
        return base64.b32decode(encoded.upper() + padding)

    def counter_at(self, timestamp: float) -> int:
        return int(timestamp // self.period)

    def generate(self, *, at: Optional[float] = None) -> str:
        timestamp = at if at is not None else self.clock()
        return hotp(self.secret, self.counter_at(timestamp), digits=self.digits, digest=self.digest)

    def verify(self, code: str, *, at: Optional[float] = None, drift: int = 1) -> bool:
        """Accepts the code for the current period, or up to `drift`
        periods before/after it -- clock-skew tolerance. This method is
        intentionally stateless and does NOT protect against replay; see
        `TOTPAuthenticator` for that."""
        timestamp = at if at is not None else self.clock()
        counter = self.counter_at(timestamp)
        for delta in range(-drift, drift + 1):
            candidate = hotp(self.secret, counter + delta, digits=self.digits, digest=self.digest)
            if hmac.compare_digest(candidate, code):
                return True
        return False


# --- stateful verification: replay protection + brute-force lockout -------------


class AccountLockedError(Exception):
    pass


@dataclass
class _UserState:
    last_used_counter: int = -1
    failed_attempts: List[float] = field(default_factory=list)


class TOTPAuthenticator:
    """Wraps a stateless `TOTP` with the two things a real verifier needs
    and bare TOTP doesn't provide:

    1. Replay protection: once a counter value has been successfully
       used, it (and everything at or before it) is rejected from then on
       -- a code can't be captured and resubmitted within its own
       drift-tolerant validity window.
    2. Attempt lockout: after `max_attempts` failures within
       `lockout_window` seconds, further attempts are rejected (raising
       `AccountLockedError`) without even checking the code, capping how
       fast an online brute-force attacker can test guesses.
    """

    def __init__(self, totp: TOTP, *, max_attempts: int = 5, lockout_window: float = 30.0):
        self._totp = totp
        self.max_attempts = max_attempts
        self.lockout_window = lockout_window
        self._state: Dict[str, _UserState] = {}

    def _state_for(self, user_id: str) -> _UserState:
        return self._state.setdefault(user_id, _UserState())

    def verify(self, user_id: str, code: str, *, at: Optional[float] = None, drift: int = 1) -> bool:
        now = at if at is not None else self._totp.clock()
        state = self._state_for(user_id)

        recent_failures = [t for t in state.failed_attempts if now - t < self.lockout_window]
        state.failed_attempts = recent_failures
        if len(recent_failures) >= self.max_attempts:
            raise AccountLockedError(
                f"{user_id!r} locked out: {len(recent_failures)} failed attempts in the last {self.lockout_window}s"
            )

        counter = self._totp.counter_at(now)
        for delta in range(-drift, drift + 1):
            candidate_counter = counter + delta
            if candidate_counter <= state.last_used_counter:
                continue  # already consumed this window (or an earlier one) -- replay
            candidate_code = hotp(
                self._totp.secret, candidate_counter, digits=self._totp.digits, digest=self._totp.digest
            )
            if hmac.compare_digest(candidate_code, code):
                state.last_used_counter = candidate_counter
                state.failed_attempts.clear()
                return True

        state.failed_attempts.append(now)
        return False


# --- brute-force cost: measured, not just claimed -------------------------------


def expected_attempts_to_guess(digits: int, drift: int) -> float:
    """At any instant, `2*drift + 1` codes are simultaneously valid out of
    `10**digits` possible strings. Each independent random guess an
    attacker submits has that fraction as its success probability,
    regardless of timing -- the number of attempts until the first
    success is geometrically distributed, with mean 1/p."""
    valid_codes = 2 * drift + 1
    p = valid_codes / (10**digits)
    return 1 / p


def expected_crack_time_seconds(digits: int, drift: int, requests_per_second: float) -> float:
    return expected_attempts_to_guess(digits, drift) / requests_per_second


def demo() -> None:
    secret = TOTP.generate_secret()
    totp = TOTP(secret)
    code = totp.generate()
    print(f"1) Generated code: {code}  (secret, base32: {TOTP.secret_to_base32(secret)})")
    print(f"   verify(code) -> {totp.verify(code)}")

    print("\n2) Brute-force cost, 6 digits, drift=1 (3 valid codes at any instant)")
    for rps in [10, 100, 1000]:
        seconds = expected_crack_time_seconds(6, 1, rps)
        print(f"   attacker at {rps:>5} req/s, no lockout: expected crack time = {seconds/3600:.2f} hours")

    effective_rps = 5 / 30.0  # max_attempts=5 per lockout_window=30s
    locked_seconds = expected_crack_time_seconds(6, 1, effective_rps)
    print(f"\n   with a 5-attempts-per-30s lockout (effective rate {effective_rps:.3f} req/s):")
    print(f"   expected crack time = {locked_seconds/86400:.1f} days")

    print("\n3) Replay protection")
    auth = TOTPAuthenticator(totp)
    now = time.time()
    code = totp.generate(at=now)
    print(f"   first use:  {auth.verify('alice', code, at=now)}")
    print(f"   replay (same instant): {auth.verify('alice', code, at=now)}")


if __name__ == "__main__":
    demo()
