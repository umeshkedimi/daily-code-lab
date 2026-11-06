# Problem

**Category:** `security`

## Statement

Implement a naive, early-exit byte string comparison and a constant-time one, then measure the actual exploitable consequence of the difference: run a byte-at-a-time timing-attack recovery against each, using only a simulated timing side channel (no real wall-clock measurement — see Constraints), and show how much of the secret's search space each comparison leaks.

The actual question this exercise answers: **does "early-exit comparison is timing-unsafe" cash out to a real, quantified advantage for an attacker, or is it a theoretical concern that doesn't actually move the needle?** The answer has to be a number — a before/after search-space size — not a demonstration that "it's possible in principle."

**Requirements:**

- `naive_compare(a: bytes, b: bytes) -> Tuple[bool, int]` — compares byte-by-byte, returning `False` immediately at the first mismatch. Returns `(matched, ops)`, where `ops` is the number of byte comparisons actually performed — the simulated timing side channel (a stand-in for elapsed time; see Constraints for why). Raises `ValueError` if `len(a) != len(b)`.
- `constant_time_compare(a: bytes, b: bytes) -> Tuple[bool, int]` — compares every byte regardless of where (or whether) a mismatch occurs; `ops` is always `len(a)`, whether it matches or not. Raises `ValueError` if `len(a) != len(b)`.
- `TimingOracle(secret: bytes, compare_fn)` — `.probe(guess: bytes) -> int` returns **only** the `ops` count for `compare_fn(guess, secret)`. It never reveals whether the guess matched — a real server's response time is visible to an attacker, but its internal boolean isn't (that's exactly the thing being attacked).
- `recover_secret(oracle: TimingOracle, length: int, alphabet: bytes) -> Tuple[bytes, Set[int]]` — byte-at-a-time recovery using only `oracle.probe`'s `ops` signal: for each position, try every byte in `alphabet` (holding already-recovered positions fixed, padding the rest with a fixed filler byte that is never itself a valid alphabet byte), and take whichever candidate yields a strictly higher `ops` than every other candidate at that position. Returns `(recovered, undetermined)`, where `undetermined` is the set of positions where no candidate was strictly best (every candidate tied) — the recovered byte at those positions is a placeholder, not a real guess.

## Constraints

- Standard library only.
- **Simulated, not measured, timing.** A real wall-clock measurement is noisy and non-reproducible (process scheduling jitter alone can exceed the actual signal at microsecond scale), which would make this exercise's result a flaky "sometimes it works" instead of a falsifiable claim. `ops` (a plain comparison count) is the same qualitative signal — time proportional to work done — made exact and deterministic. This is a modeling choice, not a dodge: the finding is about the shape of the leak, which is identical either way.
- Fixed, known secret length — the length side channel (comparison cost also often leaks `len(secret)` in real systems) is explicitly out of scope here.
- The filler byte used to pad unrecovered positions must never appear in `alphabet`, so it can never accidentally collide with a real secret byte at any position — this is what keeps the measurement exact rather than "usually works."

## Approach

1. **The side channel's shape, reasoned before coding:** at any position `pos` where every earlier position has already been correctly recovered, a *correct* candidate byte at `pos` makes the comparator advance one step further before its inevitable mismatch against the filler at `pos + 1` — **except at the very last position**, where there is no `pos + 1` to mismatch against, so a correct final byte and a wrong one both complete the comparison at the same `ops`. That's not a bug to work around; it's the actual, structural limit of this exact attack, and it's measured directly rather than discovered as a surprise.
2. **Consequence:** `recover_secret` against `naive_compare` recovers every position except the last **exactly**, using `length × len(alphabet)` oracle probes — turning a search space of `len(alphabet) ** length` into a residual space of just `len(alphabet)` (the last byte, brute-forceable directly against the real comparison in `len(alphabet)` tries, no oracle needed).
3. **Consequence against `constant_time_compare`:** `ops` is always `len(secret)` no matter what's guessed, so every candidate at every position ties — `recover_secret` determines nothing, and the residual space is the full, untouched `len(alphabet) ** length`.
4. **The measured, falsifiable claim:** compute the residual search space both ways on the same secret, same alphabet, same probe budget. The naive comparison's residual space must be smaller than the original by a factor of at least `len(alphabet) ** (length - 1)` (losing only the one unrecoverable last byte); the constant-time comparison's residual space must equal the original exactly. Checked by code, not eyeballed from one run.

## Stretch

- Real wall-clock timing with statistical repetition (many trials per guess, compare means) — to see how much noise the simulated model abstracted away.
- A length-hiding scheme (e.g. HMAC-then-compare, as real systems do) to close the length side channel noted as out of scope above.
- An oracle that adds simulated network jitter (random noise added to `ops`) to see how many repeated probes are needed to recover the same signal through noise.
