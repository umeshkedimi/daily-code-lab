# Problem

**Category:** `security`

## Statement

Implement TOTP (Time-based One-Time Password, RFC 6238), built on HOTP (RFC 4226), from scratch: HMAC-based codes derived from a shared secret and the current time, the way every "scan this QR code" 2FA setup works.

Generating a correct code is the easy 80%. The actual exercise is the other 20%, the part a bare RFC-6238 implementation doesn't give you and a real authentication system needs:

1. **Clock-drift tolerance** — a client's clock running a few seconds fast or slow shouldn't lock a legitimate user out, so the verifier must accept a small window of adjacent time periods, not just the exact current one.
2. **Replay protection** — TOTP is stateless by definition; nothing in the algorithm itself stops the same valid code being submitted twice. A verifier that doesn't track what's already been consumed is vulnerable to a captured code being replayed within its own validity window.
3. **Brute-force cost, measured** — a 6-digit code is 1 in 1,000,000. Whether that's actually secure depends entirely on how many guesses per second an attacker can get away with, which depends entirely on whether there's a lockout/throttle — a number worth computing, not assuming.

**Requirements:**
- The core HOTP/TOTP implementation must be checked against the **official RFC 4226 and RFC 6238 test vectors** (all three digest variants — SHA1, SHA256, SHA512 — for TOTP), not just internal self-consistency.
- Replay protection and clock-drift tolerance are in tension (a wider drift window means more codes an attacker-replaying-a-captured-code could still use) and both must be demonstrated working *together*, not just independently.
- The brute-force cost claim must be backed by the actual math (expected value of a geometric distribution) **and** verified empirically against a Monte Carlo simulation on a small code space, not just asserted.

## Constraints

- Standard library only (`hmac`, `hashlib`, `struct`, `base64`, `secrets`, `time`).
- A clock is injected (`Callable[[], float]`), never read from `time.time()` directly inside core logic, so drift/replay/lockout timing can be tested at exact instants without sleeping.
- `hmac.compare_digest` for the final code comparison, not `==` — constant-time comparison, for the same timing-side-channel reason as every other HMAC comparison in this repo.

## Approach

1. **HOTP first, as its own function**: `hotp(secret, counter, digits, digest)` — HMAC over the 8-byte big-endian counter, then RFC 4226's dynamic truncation (low nibble of the digest's last byte picks a 4-byte window; the top bit of those 4 bytes is masked off; reduce mod `10**digits`). Verified against all 10 official RFC 4226 test vectors before building anything on top of it.
2. **TOTP as HOTP with the counter derived from wall-clock time**: `counter = floor(timestamp / period)`. Verified against all 6 RFC 6238 test vectors, for all three supported digest algorithms (SHA1, SHA256, SHA512) — 18 checks against the published standard, not invented expected values.
3. **Drift tolerance as a parameter, not a hardcoded window**: `verify(code, drift=1)` checks `counter-drift` through `counter+drift`, so a caller chooses their own skew tolerance explicitly.
4. **Replay protection and lockout as a separate, stateful wrapper** (`TOTPAuthenticator`) around the stateless `TOTP` — deliberately not merged into `TOTP.verify` itself, since "does this code match" and "has this code (or an earlier one) already been used by this user, and have they failed too many times recently" are different concerns with different lifetimes (one secret vs. per-user mutable state).
5. **Brute-force cost as a measured, not asserted, claim**: a closed-form expected-attempts formula (geometric distribution mean), cross-checked against a Monte Carlo simulation on a small (2-digit) code space, then applied to the real 6-digit/30-second case to show what an unthrottled attacker's expected crack time actually is — and by how much a simple attempt-lockout changes that number.
