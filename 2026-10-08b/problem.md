# Problem

**Category:** `security` / `backend`

**Note:** second problem for 2026-10-08, in its own commit — same exception to the "one problem per day" rule used on 2026-09-17, 2026-09-29, and 2026-09-30.

## Statement

Build a JWT-based auth system with two token kinds:

1. **Access tokens** — short-lived, stateless HS256 JWTs, built from scratch (no JWT library), verified purely by signature + expiry, no server-side lookup.
2. **Refresh tokens** — long-lived, opaque, stored server-side (hashed, not raw), with **rotation**: every use exchanges the presented token for a brand new one and invalidates the one just used.

The actual point of the exercise is **replay detection**: a refresh token that's presented again after it has already been rotated away must be recognized as a reuse, and treated as a likely theft — not just rejected on its own, but used to shut down the entire rotation lineage it belongs to, forcing the legitimate user to re-authenticate too.

**Requirements:**
- Access tokens must be verifiable without a database lookup (stateless); refresh tokens must be checked against server-side state on every use (that's what makes rotation and replay detection possible at all).
- Refresh tokens must never be stored in a form usable if the store leaks (hash before storing, compare by hash).
- Reusing an already-rotated-away refresh token must revoke every token in that lineage, including whatever token is *currently* legitimately valid in it — not just reject the stale one.
- The JWT implementation itself must resist the two classic attacks: signature tampering (any bit flipped in the payload or signature must be caught) and the "alg: none" downgrade (a forged token claiming no signature algorithm must not bypass verification).

## Constraints

- Standard library only (`hmac`, `hashlib`, `base64`, `json`, `secrets`) — no PyJWT or similar, since the mechanics are the point.
- Signature comparison must be constant-time (`hmac.compare_digest`), to avoid a timing side-channel on how many signature bytes match — a property that matters for security but, deliberately, **cannot be verified by a functional unit test** (see notes.md).
- Tested with both hand-picked adversarial cases (alg:none forgery, wrong secret, exact-boundary expiry) and randomized testing (bit-flip fuzzing against tampering, long random sequences of login/refresh/replay checked against a global invariant), not only the happy path.

## Approach

1. **Minimal HS256 JWT**: `header.payload.signature`, each segment base64url-encoded (padding stripped, per the JWT spec). `decode()` always recomputes the expected signature using a fixed algorithm (HMAC-SHA256) over the raw segment bytes — it never reads the header's own `alg` field to decide how to verify. That one decision makes the "alg: none" downgrade attack structurally impossible rather than blocked by a special-case check: a forged token still gets its signature checked against a real HMAC-SHA256 digest, which an attacker without the secret cannot produce.
2. **Access tokens carry `type: "access"`, `exp`, and a `jti` nonce** (so two tokens issued at the same instant are still distinct, useful for audit/future denylisting). `verify_access_token` checks both the signature/expiry (via `JWT.decode`) and the `type` claim — signature validity alone isn't sufficient if the system ever has more than one kind of JWT.
3. **Refresh tokens are opaque random strings** (`secrets.token_urlsafe`), never JWTs — there's nothing to "decode," only a hash to look up. Only `sha256(raw_token)` is ever stored, so a leaked store doesn't hand out directly-usable tokens.
4. **Rotation + family-based replay detection**: every refresh token belongs to a `family_id`, assigned once at login and carried unchanged through every rotation. `refresh()` marks the presented token `used` and issues a new one in the same family; presenting an already-`used` token again revokes every record sharing that `family_id`, not just the one presented. This is a deliberate asymmetry: the system cannot tell "client retried after losing the response" apart from "attacker replayed a stolen token," so it always assumes the dangerous case.
5. **Measured, not just asserted**: a full replay scenario (login → rotate → replay the stale token → confirm the *new, legitimate* token is also now dead), bit-flip fuzzing of both the payload and signature segments to confirm tampering is always caught, and a long randomized operation sequence checked against one global invariant (once any replay is detected in a family, every token that ever belonged to it is rejected from then on).
