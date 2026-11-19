# Problem

**Category:** `security`

## Statement

Implement HMAC-based webhook signature verification (the Stripe/GitHub-style pattern: a sender signs `timestamp + payload` with a shared secret; the receiver recomputes the signature and checks it, plus a freshness window) and measure the actual, concrete consequence of the one layer that's easy to skip: **replay protection**. A correct signature over a correct payload is not enough on its own — an attacker who simply captures one valid, already-used request and resends it verbatim must be stopped by something other than the signature check, since the signature itself is still perfectly valid on the second delivery.

This is also where 2026-11-06's finding cashes in somewhere real: signature comparison is exactly the place an early-exit string comparison turns into an actual exploitable timing side channel in production systems, which is why this implementation is required to use `hmac.compare_digest`, not `==`, and a naive contrasting implementation is built specifically to make that choice non-default and visible rather than assumed.

**Requirements:**

- `canonical_message(payload: bytes, timestamp: int) -> bytes` — the exact bytes that get signed: `f"{timestamp}.".encode() + payload`. Binding the timestamp into the signed message (not just checking it separately) is what stops an attacker from pairing a captured signature with a different timestamp.
- `sign(payload: bytes, secret: bytes, timestamp: int) -> str` — `hmac.new(secret, canonical_message(payload, timestamp), hashlib.sha256).hexdigest()`.
- `naive_signatures_equal(a: str, b: str) -> bool` — an explicit, early-exit character-by-character loop (mirroring 2026-11-06's `naive_compare`), included specifically to contrast with the one actually used below. Must never be the default comparator.
- `secure_signatures_equal(a: str, b: str) -> bool` — `hmac.compare_digest(a, b)`. The one that's actually safe to use on untrusted input, and the default.
- `WebhookVerifier(secret: bytes, tolerance: int, clock: Callable[[], int], dedup: bool = True, compare_fn=secure_signatures_equal)` — `.verify(payload: bytes, timestamp: int, signature: str) -> bool`:
  1. Reject if `abs(clock() - timestamp) > tolerance` (catches both a stale replay of an old request *and* a forged, far-future timestamp — the check is symmetric, not just "not too old").
  2. Recompute the expected signature and reject unless `compare_fn(expected, signature)` is true.
  3. If `dedup` is enabled: evict any remembered signature whose recorded timestamp is now more than `tolerance` away from the current clock time, then reject if `signature` is already remembered — otherwise remember it and accept.
  4. If `dedup` is disabled: accept (used only to measure the gap dedup closes — never the production default).
- `.seen_count` — the number of currently-remembered signatures, to measure that eviction actually bounds memory under continuous traffic rather than growing forever.

## Constraints

- Standard library only (`hashlib`, `hmac`).
- `tolerance` is inclusive on both sides (`abs(now - timestamp) <= tolerance` is accepted, not just `<`) — an exact-boundary request is a legitimate one that happened to arrive right at the edge of the window, not an attack.
- Eviction must not create a security gap: an entry is only ever evicted once its own age exceeds `tolerance`, which is exactly the point at which the timestamp check in step 1 would reject a replay of it anyway — so evicting it early would be a bug, and evicting it late would let the seen-set grow without bound.

## Approach

1. **The headline measured claim**: a captured, valid `(payload, timestamp, signature)` triple is replayed 5 times, both with `dedup=True` and `dedup=False`, against the same clock and tolerance. `dedup=False` accepts all 5 (`5/5` — the signature alone never expires until the timestamp does, and nothing else stops a resend). `dedup=True` accepts exactly the first (`1/5`) — not "fewer," not "most," exactly one. This turns "you need replay protection, not just signature verification" from a security-blog platitude into a specific, falsifiable before/after count.
2. **Bounded memory under continuous legitimate traffic**: 50 distinct, individually valid requests are sent back-to-back, each spaced `tolerance + 1` apart so that by the time each new one arrives, the previous one has just aged out of the freshness window. `seen_count` must stay at exactly 1 throughout — not grow to 50 — proving the eviction rule from the Constraints actually fires in practice, not just in theory.
3. **Symmetric freshness, checked at both edges**: a timestamp exactly `tolerance` in the past is accepted; one `tolerance + 1` in the past is rejected. A timestamp exactly `tolerance` in the future is accepted; one `tolerance + 1` in the future is rejected. The same single `abs(...)` check produces both halves — it is not two separate rules that could drift apart.
4. **`naive_signatures_equal` and `secure_signatures_equal` must agree on every functional outcome tested** (same signature: both `True`; wrong secret, tampered payload, or a single flipped character: both `False`) — the point 2026-11-06 already made is that the vulnerability was never about *correctness* of the naive version, only about the side channel its timing leaks. Nothing here re-runs that side-channel recovery; it only re-confirms the premise that makes skipping `hmac.compare_digest` dangerous rather than merely sloppy — a wrong answer delivered safely is not the bug; a right answer delivered on a schedule an attacker can read is.

## Stretch

- Actually mounting 2026-11-06's byte-at-a-time recovery against `WebhookVerifier` configured with `compare_fn=naive_signatures_equal`, to show the two days compose into one real exploit chain rather than two separate observations.
- A nonce-based alternative to timestamp+signature dedup (a client-supplied, server-tracked unique ID) — trades the TTL-bounded memory argument here for a different, caller-managed uniqueness guarantee.
- Clock skew tolerance negotiation (receiver and sender clocks drifting apart over long-lived deployments) — assumed perfectly synchronized here.
