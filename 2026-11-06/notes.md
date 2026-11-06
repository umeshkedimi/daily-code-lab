## Implementation

- `naive_compare` is the textbook vulnerable pattern: a `for` loop that returns `False` the instant it finds a mismatch, with `ops` as the loop index at that point plus one. The whole exercise rests on this single detail: `ops` is *monotonically informative* about how far the comparison got before giving up.
- `constant_time_compare` accumulates `x ^ y` across every byte pair with `|=` and only checks `diff == 0` at the end — the loop body does the same fixed amount of work regardless of where (or whether) bytes differ, so `ops` is always `len(a)`. This mirrors the real technique (`hmac.compare_digest`), just made exact and countable instead of timed.
- `TimingOracle.probe` discards the boolean from `compare_fn` and returns only `ops` — the one line that encodes "the attacker sees response time, not a yes/no." Everything downstream (`recover_secret`) is built on top of that restriction, not around it.
- `recover_secret`'s correctness depends entirely on `FILLER = 0` never appearing in any `alphabet` a caller uses. With that guarantee, a correct byte at position `pos` always costs exactly one more `ops` than a wrong one at every position except the last, because it advances one step further before mismatching the filler at `pos + 1` — a structural fact proven by case analysis in `problem.md`, not discovered by trial and error.
- Not built: real wall-clock timing, length-hiding, noisy-oracle statistics — all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `naive_compare` / `constant_time_compare` | O(length) | O(1) |
| `recover_secret` | O(length × len(alphabet)) oracle probes | O(length) |

## Follow-up Questions

**Why does the attack stop at exactly one undetermined byte, not zero or several?** Because the comparator stops advancing at the first mismatch, every correctly-guessed byte except the last produces a visible, strictly-larger `ops` than a wrong guess, by exactly one. The last byte has no "next index" to mismatch against, so correct and wrong guesses there both complete the comparison at the same cost — there's nothing left to distinguish them with *this specific signal*. It's not noise; it's the geometry of the comparison running out of positions.

**How exactly would a real attacker finish the last byte?** Directly, against the live system, with up to `len(alphabet)` ordinary attempts — no timing measurement needed, because the search space left is tiny (10, in this exercise) rather than astronomical (10,000). `test_recovered_prefix_plus_tiny_brute_force_finds_the_real_secret` does exactly this with `naive_compare` standing in for "the real system."

**What happens internally when the oracle is backed by `constant_time_compare`?** Every candidate at every position returns `ops == length`. `max(scores.values())` is always a three- or ten-way (here, ten-way) tie, so `recover_secret` falls into the `else` branch at every single position — the function doesn't "fail to notice" a signal, there genuinely is none to notice.

**What if the secret length isn't known?** Out of scope here by design (stated in `problem.md`'s constraints) — a real system that also leaks length (e.g. via a length-dependent comparison cost) needs that closed separately, typically by hashing to a fixed length before comparing (HMAC-then-compare), which is exactly why real libraries combine both rather than offering constant-time comparison alone.

**What trade-offs did you consider?** Simulated `ops` vs. real wall-clock timing: real timing is the thing that actually gets attacked in production, but it's also genuinely noisy (OS scheduling jitter, cache effects, GC pauses), which would turn this into a statistics problem rather than a clean one. Using an exact operation count isolates the *shape* of the leak — monotonic-until-the-last-byte for naive, flat for constant-time — from the separate, real problem of recovering a noisy signal, which is listed as a stretch rather than folded in and left half-done.

**How do you evaluate it?** One number, computed by code, not eyeballed: starting from the same 10,000-value secret space (4 digits, 10 possible values each), the naive comparison's residual search space after the attack is exactly 10 (a 1,000x collapse) using only 40 oracle probes; the constant-time comparison's residual space is the full 10,000 — identical probe budget, opposite outcome. That's the entire "is this a real problem" question answered in one comparable pair of numbers.

## Key Learnings

- Reasoning out the "only the last byte is unrecoverable" result by case analysis *before* writing `recover_secret`, and stating it as an expected, structural outcome in `problem.md` rather than an assertion to be found true or false, meant there was nothing to debug when all 12 tests passed on the first implementation run — the hand-trace and the code agreed because the hand-trace was actually a proof, not a guess.
- The choice to make `TimingOracle.probe` return only `ops` (not the match boolean) is what makes the exercise honest: without that restriction, `recover_secret` could just try full guesses and read the boolean directly, which is normal brute force wearing a timing-attack costume. Restricting the interface to the thing a real attacker would actually have access to is what makes the measured result mean something.
- Consistent with the circuit-breaker and context-truncation days: a strategy's tie-break or stopping rule isn't incidental — here, "ops is monotonic except at the boundary" is the entire reason the attack recovers `length - 1` bytes and not `length` or `0`. The exact mechanics of *where* a signal runs out is the finding, not a detail to abstract away.
