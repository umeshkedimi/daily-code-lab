# Problem

**Category:** `dsa` / `backend`

## Statement

Implement a **Bloom filter** — a fixed-size bit array plus *k* hash functions that answers "have I seen this item?" with **no false negatives** and a **tunable false-positive rate**, in a small fraction of the memory an exact set needs. Then verify its quantitative behaviour against theory instead of quoting it.

**Requirements:**
- `add` / `in`, sized from a target capacity and false-positive rate.
- Every closed-form claim measured against the implementation: false-positive rate, memory per item, fill level, and the estimate of how many distinct items were inserted.
- Behaviour beyond capacity (Bloom filters degrade rather than fail).
- Union, and a stable serialization.
- A realistic use: shielding a slow store from lookups for keys that don't exist.

## Constraints

- **The one hard guarantee — no false negatives — must hold in every configuration**, including tiny and saturated filters.
- **Hashing must be deterministic across processes and machines.** A filter is meant to be persisted or shipped; Python's built-in `hash()` on strings is salted per process and would silently give a different filter each run.
- **Claims are checked with confidence intervals, not eyeballed**, and the statistical tests must not fail by bad luck on a correct implementation.
- **Where a well-known equivalence is only asymptotic, find the size at which it stops holding** rather than assume it does.

## Approach

1. **Sizing from first principles.** For `n` items at false-positive rate `p`: `m = -n·ln p / (ln 2)²` bits and `k = (m/n)·ln 2` hashes. At that optimum about half the bits are set, which is what maximizes the information each bit carries. The cost is a fixed 1/ln 2 ≈ 1.44× the information-theoretic minimum of `log₂(1/p)` bits per item.
2. **Three ways to get *k* probe positions.** *Independent*: a separately-salted digest per probe — the textbook model the false-positive formula assumes. *Double hashing* (Kirsch & Mitzenmacher): one digest split into `h1`, `h2`, with `indexᵢ = h1 + i·h2 mod m` and `h2` forced odd so it is coprime with a power-of-two `m`. *Enhanced double hashing*: the same plus a cubic term `(i³−i)/6`, which stops the probes being an arithmetic progression. All three are implemented so they can be compared instead of trusted — and the comparison turned out to matter: plain double hashing is measurably worse at small sizes.
3. **Lazy probing.** A lookup stops at the first unset bit; an absent key usually finds one within ~2 probes at 50% fill. Probe positions are generated on demand so the remaining hashes are never computed.
4. **Prediction functions alongside the structure.** The exact false-positive formula `(1 − (1 − 1/m)^{kn})^k`, the fill-implied rate `(set bits/m)^k`, and the Swamidass–Baldi cardinality estimate `−(m/k)·ln(1 − X/m)` — each then measured against what the filter actually does.
5. **Union as bitwise OR**, exactly equal to inserting both item sets (tested for bit-for-bit identity), refused when `m`, `k` or the hashing scheme differ. **Serialization** with a magic number, parameters, and a length check that rejects truncated or padded payloads.
6. **Statistics done properly:** Wilson score intervals (sane near 0, where false-positive rates live), 99.9% intervals in the tests, and fixed keys so results are deterministic.
7. **A guarded-store application** that counts the database lookups a filter actually saves, including the ones wasted on false positives, and asserts the store never returns a wrong answer.
