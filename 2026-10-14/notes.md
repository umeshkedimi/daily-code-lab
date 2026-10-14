## Implementation

- `hotp`: HMAC over an 8-byte big-endian counter, then RFC 4226 dynamic truncation -- the low nibble of the digest's *last* byte selects a 4-byte window elsewhere in the digest (not a fixed position), the top bit of that 4-byte window is masked off (keeps the result a non-negative 31-bit integer, which is what keeps this algorithm's output identical across platforms regardless of how a given language represents signed integers), and the result is reduced mod `10**digits`. Raises `ValueError` on a negative counter rather than letting a confusing `struct.error` leak through -- found necessary while testing drift near the Unix epoch (see Key Learnings).
- `TOTP.counter_at`: `int(timestamp // period)` -- floor division, so the period boundary is unambiguous (`t=29.999` is period 0, `t=30.0` is period 1, verified directly).
- `TOTP.verify`: stateless, checks `counter-drift` through `counter+drift` inclusive, using `hmac.compare_digest` for the comparison (constant-time, same reasoning as every other HMAC comparison in this repo -- a `==` here would leak timing information about how many digits of a forged code happened to match).
- `TOTPAuthenticator` is a separate class wrapping `TOTP`, not a mode of `TOTP.verify` itself -- `TOTP` owns one secret and is otherwise stateless; `TOTPAuthenticator` owns mutable per-user state (`last_used_counter`, recent failure timestamps) that has nothing to do with the secret and everything to do with *this specific login flow's* history. Replay protection: a `candidate_counter` at or below `last_used_counter` is skipped entirely, regardless of drift -- once counter N has been consumed, nothing at or before N is ever accepted again for that user. Lockout: a fixed-window failure count (`len(recent_failures) >= max_attempts` within `lockout_window` seconds) checked *before* the code is examined at all -- a locked-out call doesn't even get to find out whether its code was right.
- `expected_attempts_to_guess`/`expected_crack_time_seconds`: direct application of the geometric distribution's mean (`1/p`) to "`2*drift+1` valid codes out of `10**digits`" -- cross-checked against a Monte Carlo simulation on a small code space (see Measured Results) specifically so the formula isn't trusted on its own.
- Not built: QR-code image generation (base32 secret encoding is implemented, the `otpauth://` URI format and actual QR rendering are not, since that's a presentation-layer concern, not the algorithm), resynchronization (some real implementations widen the drift window temporarily after detecting persistent clock skew for a specific user -- out of scope here), HOTP's own counter-based (not time-based) mode as a first-class option (`hotp()` is used directly by `TOTP` and is already fully exercised by the RFC 4226 vectors, but there's no separate `HOTPAuthenticator` with its own counter-increment-on-success semantics).

## Measured Results

- **RFC 4226 (HOTP)**: all 10 official test vectors (secret `"12345678901234567890"`, counters 0-9) match exactly.
- **RFC 6238 (TOTP)**: all 6 official test timestamps match exactly, for all three published digest variants (SHA1, SHA256, SHA512) -- 18 checks against the standard's own published expected output, not self-derived values.
- **Brute-force cost, 6 digits, drift=1** (3 simultaneously-valid codes out of 1,000,000): expected attempts to guess = 1,000,000/3 ≈ 333,333.
  - Unthrottled attacker at 10 req/s: expected crack time ≈ 9.26 hours.
  - Unthrottled attacker at 100 req/s: expected crack time ≈ 56 minutes.
  - Unthrottled attacker at 1,000 req/s: expected crack time ≈ 5.6 minutes.
  - With a 5-attempts-per-30-seconds lockout (effective rate ≈0.167 req/s): expected crack time ≈ 23.1 days -- a **~250x** increase over the 100 req/s unthrottled case, confirmed by direct calculation and asserted in the test suite with a conservative (>100x) margin.
- **Monte Carlo cross-check**: 3,000 simulated trials of "guess uniformly at random from a 100-code space until the one valid code is hit" gave a sample mean within 15% of the analytical expectation (100 attempts) -- the formula isn't just algebra, it matches what actually happens when you simulate the attack.

## Follow-up Questions

**Why?**
TOTP is the mechanism behind nearly every "scan this QR code with your authenticator app" second factor in production use today — understanding it means understanding both why it's effective (the attacker needs the secret, not just a stolen password) and where its remaining weaknesses live (a 6-digit space is small enough that online brute-forcing is a real, measurable concern, not a theoretical one).

**How exactly?**
A secret shared once (at setup time, typically via QR code) between the server and the user's authenticator app lets both sides independently compute the same HMAC-derived code from the same current time, without any further communication — the code itself proves possession of the secret, the same way a signature proves possession of a signing key.

**Which algorithm?**
HOTP (RFC 4226, HMAC-based One-Time Password) is the core primitive; TOTP (RFC 6238) is HOTP with the counter replaced by `floor(time / period)` instead of an incrementing counter — the same dynamic-truncation trick either way.

**Which library?**
Standard library only: `hmac` and `hashlib` for the HMAC computation, `struct` for the counter's fixed 8-byte big-endian encoding, `base64` for the standard base32 secret encoding, `secrets` for generating cryptographically random secrets (never `random`).

**What happens internally?**
HMAC-SHA1(secret, counter) produces a 20-byte digest; dynamic truncation picks a 4-byte window from it based on the digest's own last byte (so the window position itself depends on the HMAC output, not a fixed offset, which is part of why this construction resists certain truncation attacks that a fixed-offset scheme wouldn't) and reduces that to a decimal number. The security of the whole scheme reduces to HMAC's security: without the secret, producing a valid code for a given counter is exactly as hard as forging an HMAC, which is why the actual exploitable weakness here isn't the cryptography — it's the small, human-typeable output space (6 digits) combined with how fast an attacker can submit guesses.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **Client clock drifts by a few seconds**: handled by `drift` — verified that a code generated one period early or late is still accepted at `drift=1`, and still rejected at `drift=0` or two periods away.
- **A captured code gets replayed by an attacker** (e.g. intercepted over a compromised channel, or a user's screen was shoulder-surfed) within its own drift-tolerant validity window: rejected — verified directly, including the specific case where the code is *still* lexically "valid" by drift tolerance at a later time but has already been consumed (`test_a_code_cannot_be_replayed_later_within_its_own_drift_window`).
- **An online attacker tries many codes per second with no rate limit**: `TOTPAuthenticator`'s lockout caps this — verified that it triggers unconditionally (rejecting even the *correct* code once triggered) and clears once the failure window ages out.
- **Verification is attempted very close to the Unix epoch, with drift tolerance pushing the counter negative**: `hotp` raises `ValueError` rather than a bare, confusing `struct.error` — found and fixed while writing this exercise's own tests (see Key Learnings), not a scenario with any real-world authentication relevance (no legitimate login happens at `t<0`), but a real robustness gap in the function's own error-handling regardless of why it gets hit.

**What trade-offs did you consider?**
- **Checking against the official RFC test vectors (chosen) vs. only internal cross-checks (e.g. "verify accepts what generate produces"):** internal cross-checks can pass while the implementation is still wrong in a way that's self-consistent (e.g. a byte-order bug that both `generate` and `verify` share) — the official vectors are an independent authority this implementation either matches or doesn't, with zero room for a shared bug to hide.
- **A separate `TOTPAuthenticator` wrapper (chosen) vs. building replay/lockout state directly into `TOTP`:** `TOTP` represents one secret and is naturally stateless and shareable; replay/lockout state is inherently per-user and mutable. Merging them would make `TOTP` itself need a notion of "which user" for no algorithmic reason, and would make the stateless core harder to test and trust independently of the stateful policy wrapped around it.
- **A fixed-window failure count for lockout (chosen) vs. a sliding-window/leaky-bucket counter:** a fixed recency filter (`now - t < lockout_window`) is simpler to reason about and test exactly, at the cost of the same minor boundary burst risk that 2026-10-08's fixed-window rate limiter has — acceptable here because the absolute numbers involved (a handful of attempts) make the burst's practical impact negligible, unlike a high-throughput rate limiter.
- **Measuring brute-force cost via the closed-form expectation, cross-checked by simulation (chosen) vs. only simulating a brute-force attack directly at full 6-digit scale:** a full 1,000,000-code simulation would be slow to run repeatedly and wouldn't actually prove anything the formula doesn't already give exactly; simulating a small (100-code) space is fast enough to run every test suite execution and is what actually validates the formula's assumptions (independence, uniform random guessing) against real simulated behavior.

**How do you debug it?**
- A `hotp`/`TOTP.generate` disagreement with a known-good RFC vector localizes to one of exactly three places: the HMAC construction itself (wrong secret/counter bytes), the truncation offset (wrong nibble, wrong byte), or the final reduction (wrong mask, wrong modulus) — printing the raw HMAC digest and the chosen offset directly distinguishes all three.
- For a replay-protection bug, `state.last_used_counter` read directly after a suspicious verification tells you immediately whether the counter was recorded at all, and at what value — a replay that *should* have been rejected but wasn't almost always traces to this never being updated, or being compared with the wrong inequality (as in mutation M3 below).
- For a lockout bug, printing `recent_failures` (after the recency filter, before the length check) distinguishes "the window isn't filtering old failures" from "the threshold comparison itself is off by one."

**How do you evaluate it?**
- **38 tests, 5/5 consecutive clean runs.**
- **Mutation check, 6 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - Dynamic truncation drops the high-bit mask: 9/38 failed (RFC vectors where the masked bit happened to matter).
  - Truncation offset uses the high nibble instead of the low nibble: 15/38 failed.
  - Replay check `<=` → `<` (off-by-one, would re-accept the exact just-used counter): 4/38 failed.
  - Lockout check `>=` → `>` (off-by-one, allows one extra attempt past the configured limit before locking): 3/38 failed.
  - A successful verification forgets to clear `failed_attempts`: 1/38 failed.
  - `expected_attempts_to_guess` undercounts valid codes (`drift + 1` instead of `2*drift + 1`): 1/38 failed.
- No zero-failure mutations this time — unlike the last several problems in this repo, every mutation attempted here was caught on the first clean run, which is itself worth recording: not every exercise turns up a coverage gap, and forcing one where none exists would be worse than reporting that the suite held.

## Key Learnings

- **Validating against the primitive's own published test vectors (RFC 4226, RFC 6238) caught exactly the class of bug that self-consistency checking cannot: byte-order and truncation-detail mistakes that a self-written "generate then verify" test would never surface, because both sides of that check would share the same mistake.** All three digest variants (SHA1/SHA256/SHA512) were checked, not just the default, specifically because a bug could plausibly live in how the digest length interacts with truncation (a SHA512 digest is 64 bytes; SHA1's is 20) rather than in the counter/HMAC logic shared across all three.
- **A negative-counter crash was found by an unrealistic test scenario, and the right response was judgment about what's actually worth fixing, not a reflex to patch whatever broke.** Probing drift tolerance near `t=0` sent the counter negative and crashed on a confusing `struct.error`. The scenario itself has no real-world authentication relevance — nobody logs in at the Unix epoch — so the fix wasn't to make `verify` handle negative counters gracefully (which would mean *accepting* nonsensical pre-epoch time ranges), it was to make `hotp` fail with a clear, specific error instead of leaking an implementation detail, and to rewrite the test to probe drift tolerance at a realistic timestamp instead. The bug was real (a confusing crash is worse than a clear error) even though the scenario that found it wasn't.
- **This exercise's mutation testing didn't turn up a zero-failure case, and that's worth noting rather than treating as "testing done less thoroughly."** The last several problems in this repo each found at least one mutation that initially passed every test, requiring a direct verification pass to classify it as a real gap or genuinely benign. This time, all 6 attempted mutations were caught immediately, on tests that existed before mutation testing began (the RFC vectors, the replay/lockout boundary tests, the brute-force formula check) — a reminder that the zero-failure pattern in recent days reflects which specific mutations were tried against which specific test suites, not a general law that every mutation set will always turn up a gap.
