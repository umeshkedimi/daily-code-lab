"""JWT-based auth: short-lived HS256 access tokens (built from scratch, not
a library) plus long-lived, rotating, hashed-at-rest refresh tokens with
replay detection.

Two independent trust mechanisms, deliberately not one:
- Access tokens are *stateless* -- any server with the shared secret can
  verify one without a database lookup, at the cost of being valid until
  `exp` no matter what (there is no "revoke this specific access token").
- Refresh tokens are *stateful* -- looked up server-side on every use,
  which is what makes rotation and replay detection possible at all: a
  stateless token can't remember "this one was already used."

Rotation + replay detection: every refresh exchanges the presented refresh
token for a new one and marks the old one `used`. Presenting a `used`
token again means either a client retried after losing the response (its
own bug) or an attacker stole an old token after it was already rotated
forward (a real compromise) -- this implementation can't tell those two
apart, so it treats every replay as the dangerous case and revokes the
*entire rotation lineage* ("family"), forcing the real user to log in
again too. See notes.md for why that's the right default trade-off.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


# --- minimal HS256 JWT, built from scratch ----------------------------------------


class JWTError(ValueError):
    """Base for anything wrong with a token -- bad signature, expired, malformed."""


class InvalidSignature(JWTError):
    pass


class TokenExpired(JWTError):
    pass


class MalformedToken(JWTError):
    pass


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


_URLSAFE_TO_STANDARD = str.maketrans("-_", "+/")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * (-len(s) % 4)
    translated = (s + padding).translate(_URLSAFE_TO_STANDARD)
    # `validate=True` is load-bearing here: `base64.urlsafe_b64decode`
    # (and plain `b64decode` without this flag) silently *discards* any
    # character outside the base64 alphabet instead of raising, which
    # would let a corrupted segment decode to something rather than
    # being caught as malformed.
    return base64.b64decode(translated, validate=True)


class JWT:
    """header.payload.signature, each base64url-encoded with padding stripped
    (standard JWT framing). Signature is HMAC-SHA256 over the exact bytes of
    "header_b64.payload_b64" -- `decode` always computes this itself using a
    fixed algorithm; it never trusts the header's own `alg` field to decide
    how to verify. That one decision is what makes the classic "alg: none"
    downgrade attack structurally impossible here, not just blocked by a
    special-case check (see notes.md)."""

    _HEADER = {"alg": "HS256", "typ": "JWT"}

    @classmethod
    def encode(cls, claims: dict, secret: str) -> str:
        header_b64 = _b64url_encode(json.dumps(cls._HEADER, separators=(",", ":"), sort_keys=True).encode())
        payload_b64 = _b64url_encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()
        signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        return f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"

    @classmethod
    def decode(cls, token: str, secret: str, *, now: Optional[float] = None) -> dict:
        parts = token.split(".")
        if len(parts) != 3:
            raise MalformedToken(f"expected 3 dot-separated segments, got {len(parts)}")
        header_b64, payload_b64, sig_b64 = parts

        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        try:
            actual_sig = _b64url_decode(sig_b64)
        except Exception as e:
            raise MalformedToken(f"signature segment is not valid base64url: {e}") from e

        # Constant-time comparison: a `==` here would short-circuit on the
        # first mismatched byte, which leaks (via response timing) how many
        # leading bytes of a forged signature happened to be correct --
        # irrelevant to functional correctness, which is why no unit test
        # below can tell these two apart (see notes.md, Key Learnings).
        if not hmac.compare_digest(expected_sig, actual_sig):
            raise InvalidSignature("signature mismatch -- tampered payload, wrong secret, or forged token")

        try:
            claims = json.loads(_b64url_decode(payload_b64))
        except Exception as e:
            raise MalformedToken(f"payload segment is not valid JSON: {e}") from e

        if "exp" in claims:
            check_time = now if now is not None else time.time()
            if check_time >= claims["exp"]:
                raise TokenExpired(f"token expired at {claims['exp']}, checked at {check_time}")
        return claims


# --- refresh-token rotation + replay detection ------------------------------------


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str


@dataclass
class _RefreshRecord:
    user_id: str
    family_id: str
    expires_at: float
    used: bool = False
    revoked: bool = False


class AuthError(Exception):
    pass


class InvalidRefreshToken(AuthError):
    pass


class ExpiredRefreshToken(AuthError):
    pass


class RevokedRefreshToken(AuthError):
    pass


class ReplayDetected(AuthError):
    pass


class WrongTokenType(AuthError):
    pass


class AuthService:
    """Access tokens are HS256 JWTs (`type: "access"`, `exp` = now +
    access_ttl). Refresh tokens are opaque random strings (`secrets.
    token_urlsafe`); only their SHA-256 hash is ever stored, so a leak of
    the server-side store doesn't hand out directly-usable tokens.

    Every refresh token belongs to a `family_id`, set once at login and
    carried forward unchanged through every rotation in that lineage. A
    family is the unit of revocation: replay detection and logout both
    revoke a whole family, not one token.
    """

    def __init__(
        self,
        secret: str,
        access_ttl: float = 900,
        refresh_ttl: float = 1_209_600,
        clock: Callable[[], float] = time.time,
    ):
        self._secret = secret
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl
        self._clock = clock
        self._refresh_store: Dict[str, _RefreshRecord] = {}  # sha256(raw token) -> record

    @staticmethod
    def _hash(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    def _issue_pair(self, user_id: str, family_id: str) -> TokenPair:
        now = self._clock()
        access = JWT.encode(
            {
                "sub": user_id,
                "type": "access",
                "iat": now,
                "exp": now + self._access_ttl,
                "jti": secrets.token_hex(8),  # makes each issuance unique even at the same clock reading
            },
            self._secret,
        )
        raw_refresh = secrets.token_urlsafe(32)
        self._refresh_store[self._hash(raw_refresh)] = _RefreshRecord(
            user_id=user_id, family_id=family_id, expires_at=now + self._refresh_ttl
        )
        return TokenPair(access_token=access, refresh_token=raw_refresh)

    def login(self, user_id: str) -> TokenPair:
        return self._issue_pair(user_id, family_id=secrets.token_hex(16))

    def refresh(self, raw_refresh_token: str) -> TokenPair:
        record = self._refresh_store.get(self._hash(raw_refresh_token))
        if record is None:
            raise InvalidRefreshToken("unknown refresh token")
        if record.revoked:
            raise RevokedRefreshToken("this token's family has already been revoked")
        if record.used:
            # Already rotated away once -- this presentation is a replay,
            # either a buggy retry or theft. Treat it as theft: shut down
            # the whole lineage, including whatever token is currently
            # "legitimately" valid in it, and force a real re-login.
            self._revoke_family(record.family_id)
            raise ReplayDetected(f"refresh token reuse detected -- family {record.family_id!r} revoked")
        if self._clock() >= record.expires_at:
            raise ExpiredRefreshToken("refresh token expired")
        record.used = True
        return self._issue_pair(record.user_id, record.family_id)

    def logout(self, raw_refresh_token: str) -> None:
        record = self._refresh_store.get(self._hash(raw_refresh_token))
        if record is not None:
            self._revoke_family(record.family_id)

    def _revoke_family(self, family_id: str) -> None:
        for record in self._refresh_store.values():
            if record.family_id == family_id:
                record.revoked = True

    def verify_access_token(self, access_token: str) -> dict:
        claims = JWT.decode(access_token, self._secret, now=self._clock())
        if claims.get("type") != "access":
            raise WrongTokenType(f"expected an access token, got type={claims.get('type')!r}")
        return claims


def demo() -> None:
    auth = AuthService("demo-secret", access_ttl=900, refresh_ttl=1_209_600)

    print("1) Login, then a normal refresh rotation")
    pair1 = auth.login("alice")
    print(f"   access token claims: {JWT.decode(pair1.access_token, 'demo-secret')}")
    pair2 = auth.refresh(pair1.refresh_token)
    print(f"   rotated: refresh token changed = {pair2.refresh_token != pair1.refresh_token}")

    print("\n2) Replaying the now-stale pair1 refresh token (simulated theft)")
    try:
        auth.refresh(pair1.refresh_token)
    except ReplayDetected as e:
        print(f"   caught: {e}")

    print("\n3) The legitimate, currently-valid pair2 token is ALSO dead now")
    try:
        auth.refresh(pair2.refresh_token)
    except RevokedRefreshToken as e:
        print(f"   caught: {e}  (whole family shut down, not just the replayed token)")

    print("\n4) A forged alg:none token is rejected the same way any bad signature is")
    header_b64 = _b64url_encode(b'{"alg":"none","typ":"JWT"}')
    _, payload_b64, _ = JWT.encode({"sub": "alice", "admin": True}, "demo-secret").split(".")
    forged = f"{header_b64}.{payload_b64}."
    try:
        JWT.decode(forged, "demo-secret")
    except JWTError as e:
        print(f"   caught: {type(e).__name__}: {e}")


if __name__ == "__main__":
    demo()
