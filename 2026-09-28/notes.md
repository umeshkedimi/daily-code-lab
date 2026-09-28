## Implementation

- **Sizing.** `optimal_params(n, p)` returns `m = ceil(-n ln p / (ln 2)²)` and `k = max(1, round(m/n · ln 2))`; it validates `n ≥ 1` and `0 < p < 1`. Reference check: `(1000, 0.01) → (9586, 7)`, the standard worked example.
- **Storage.** A `bytearray` of `ceil(m/8)` bytes; bit `i` lives at byte `i>>3`, mask `1 << (i & 7)`. Bits past `m` in the last byte are never set, so the popcount is exact.
- **Hashing (three schemes).** BLAKE2b throughout, never `hash()`. `_iter_indexes` is a generator. `"independent"` yields one salted 8-byte digest per probe. `"double"` takes one 16-byte digest, splits it into `h1`/`h2` (with `h2 |= 1`) and yields `(h1 + i·h2) mod m`. `"enhanced"` is the same plus `(i³ − i)/6` (Dillinger–Manolios), so the probes stop being an arithmetic progression. `add` consumes every probe (`|=`, so re-adding is idempotent); `__contains__` is `all(...)` over the generator, which stops at the first unset bit.
- **Predictions.** `expected_fp_rate(m, k, n)` (the exact form, not the `e^{-kn/m}` approximation), `estimate_cardinality(m, k, X)` (→ ∞ when full), and the filter methods `fill_ratio`, `estimated_count`, `estimated_fp_rate`. `wilson_interval` supplies the confidence intervals used to compare observed against predicted.
- **Combining/persistence.** `union` ORs the byte arrays (refuses mismatched `m`/`k`/scheme; leaves operands untouched). `to_bytes`/`from_bytes` use a `BLM1` magic, `m`, `k` and a scheme id, and reject truncated headers, bad magic, unknown scheme ids, and payloads whose length disagrees with `m`.
- **`GuardedStore`.** A dict ("database") behind a filter sized for its records; keys the filter has never seen return immediately, everything else costs one counted lookup.
- **Not built:** deletion (a counting Bloom filter), growth beyond capacity (a scalable Bloom filter), cuckoo filters, blocked/cache-line-aligned Bloom filters, and any concurrency control.

## Complexity

- `add`, `in`: O(k) — at most `k` bit operations. An absent key averages about 2 probes at 50% fill because lookups exit early. Space: `m` bits, i.e. `≈ 1.44·log₂(1/p)` bits per item at the optimum.
- `set_bits`/`fill_ratio`/`estimated_count`, `union`, `to_bytes`/`from_bytes`: O(m/8).
- Measured: 200,000 items at 1% need 234 KB (9.59 bits/item). A Python `set` holding the same 200,000 string keys needs 8.0 MB for its hash table alone, *before* counting the key strings — 35× more, and 18–70× across the 0.01%–10% targets.

## Follow-up Questions

**Why?**
Many systems ask "is this key even present?" far more often than they get a yes, and an exact answer is expensive — a disk seek, a remote call, a cache fill. A structure that answers *"definitely not"* correctly and cheaply lets those requests skip the expensive step. LSM-tree storage engines use exactly this to avoid reading files that can't contain a key; a cache in front of a database uses it to blunt requests for keys that don't exist. The application built here avoided 89.1% of database lookups on a workload where 90% of the requested keys were absent (54,489 lookups instead of 500,000; the sizing predicts 54,500).

**How exactly?**
`k` hash functions map an item to `k` bit positions. Insert sets them; lookup reports "possibly present" only if all `k` are set, and "definitely absent" as soon as one is clear. It can be wrong in only one direction: bits set by *other* items can make an absent item look present.

**Which algorithm?**
The Bloom filter (Bloom, 1970) with sizing derived from minimizing false positives for a given memory; Kirsch–Mitzenmacher double hashing (and its enhanced variant) as the cheap way to get `k` probes; the Swamidass–Baldi estimator for the number of distinct items.

**Which library?**
Standard library only: `hashlib` (BLAKE2b), `struct`, `math`; `tracemalloc` for the memory comparison. No bit-array or Bloom-filter package.

**What happens internally?**
At the optimum about half the bits are set, so a lookup for an absent key finds an unset bit on each probe with probability ~½ — which is why it typically stops after ~2 probes. A false positive needs *all* `k` probes to land on set bits, so the rate is `(fill)^k`.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **Overfilling — measured.** Sized for 50,000 items at 1%, the observed false-positive rate was 0.0096 at 1× capacity, 0.0588 at 1.5×, 0.1570 at 2× (formula 0.1575), 0.4369 at 3× (0.4360), 0.8321 at 5× (0.8319) and 0.9790 at 8× (0.9799). There is no error and no capacity check — the filter just becomes progressively less useful: about 44% of absent keys pass at 3×, and more than 80% by 5×. Capacity has to be monitored by the caller, and `estimated_count()` is the tool for it: it stayed accurate far past capacity (398,911 vs a true 400,000 at 8×).
- **A "maybe" is not a "yes".** A positive answer must be confirmed against the source of truth. `GuardedStore` does so, and the demo asserts on 500,000 requests that it never returns a wrong value.
- **No deletion.** Clearing an item's bits could clear bits other items share, creating false negatives; deletion needs a counting variant.
- **Changing the hash, `m`, `k` or the scheme invalidates a filter.** Serialization records `m`, `k` and the scheme, and `from_bytes`/`union` check them. The hash function itself is fixed (BLAKE2b); a cross-process test confirms the bits are identical regardless of `PYTHONHASHSEED`.
- **Small `m` with plain double hashing** — a measured accuracy loss (next section), and the reason the enhanced scheme exists.

**What trade-offs did you consider?**
- **Which way to derive `k` probes.** Mean observed false-positive rate over 30 filters (± standard error) against the exact formula:

  | m | k | items | formula | independent | double | enhanced |
  |---:|--:|---:|---:|---:|---:|---:|
  | 128 | 4 | 12 | 0.0097 | 0.0098 ± 0.0004 | **0.0122 ± 0.0004 (+26%)** | 0.0095 ± 0.0003 |
  | 256 | 5 | 25 | 0.0087 | 0.0088 ± 0.0003 | **0.0106 ± 0.0004 (+22%)** | 0.0091 ± 0.0002 |
  | 1,024 | 7 | 100 | 0.0073 | 0.0076 ± 0.0002 | **0.0083 ± 0.0002 (+13%)** | 0.0075 ± 0.0002 |
  | 8,192 | 7 | 800 | 0.0073 | 0.0071 ± 0.0001 | 0.0075 ± 0.0001 (+3%) | 0.0072 ± 0.0001 |
  | 65,536 | 7 | 6,000 | 0.0053 | 0.0054 ± 0.0001 | 0.0054 ± 0.0001 (+1%) | 0.0054 ± 0.0001 |

  Independent hashing stayed within about two standard errors of the formula at every size. Plain double hashing runs high at small `m`, with the excess decaying as `m` grows; at a realistic size (n = 20,000, 1%) it is statistically indistinguishable from independent hashing, so its equivalence is real but asymptotic. (An exploratory run at m=1,024 gave +9% instead of +13%, so that row carries a few points of uncertainty.) **Cause:** the `k` probes of double hashing form an arithmetic progression mod `m`, which correlates them. This was tested, not assumed: the enhanced scheme adds one cubic term to break the progression, costs the same one digest, and stays within about two standard errors of the formula at every size.
- **What each scheme costs.** With lazy probing, absent-key lookups cost about the same either way (k=7: 1.24 µs independent vs 1.08 µs double; k=20: 1.20 vs 1.06) because both exit early. The savings from one digest show up on operations that must evaluate every probe: adds (2.94 vs 1.64 µs at k=7; 7.46 vs 3.33 at k=20 — 1.8× and 2.2×) and lookups of present keys (3.00 vs 1.78; 7.61 vs 3.66). Nowhere near `k×`, because fixed per-item Python overhead is a large share of the cost.
- **Eager vs lazy probing.** The first version computed every probe before checking any bit. Independent-hashing absent-key lookups measured 2.66 µs at k=7 and 6.15 µs at k=20; after making probing lazy: 1.24 and 1.20 µs — flat in `k`, and for the most common real-world lookup (an absent key) most of the gap to double hashing disappeared.
- **Memory vs exactness.** 18–70× less memory than a hash set's table, in exchange for no enumeration, no deletion, no stored values, and a chance of a wrong "yes". The comparison is deliberately conservative: it excludes the key strings a real set must also hold.
- **A `bytearray` of bits (chosen) vs a Python big integer as a bit set.** The `bytearray` supports O(1) in-place bit sets; an `int` would rebuild an immutable multi-kilobyte object on every insertion.

**How do you debug it?**
- `fill_ratio()` first: near 0.5 means correctly sized for its load; well above means overfilled (`estimated_count()` says by how much); near 0 means oversized or barely used.
- Compare `estimated_fp_rate()` (implied by the current fill) with a measured rate on known-absent probe keys — they should agree; if not, the hash is not behaving uniformly.
- A reported "false negative" is impossible by construction, so suspect the *caller* (different hash/params/scheme, or a rebuilt filter) before the structure — which is why `from_bytes` and `union` fail loudly on mismatched parameters.
- The OR-vs-XOR bug class only shows up as false negatives on repeated inserts; the duplicate-add test exists specifically to catch it.

**How do you evaluate it?**
- **66 tests, 10/10 consecutive clean runs (~8 s).** The statistical tests use fixed keys and 99.9% intervals, so they are deterministic and a correct implementation has only a 1-in-1000 chance of tripping any single interval.
- **False-positive rate vs formula** for all three schemes at 10%, 1% and 0.1%; fill ≈ 0.5 at the optimum (0.466 at 10%, where `k` rounds from 3.3 to 3); overfill at 1×/2×/4× against the formula; fill-implied rate vs observed; estimated count within 3% and unchanged by re-inserting duplicates.
- **No false negatives** across sizes from `m=1` (a one-bit filter) to `m=9,586`, `k` up to 20, all three schemes, plus after union and after serialization round-trips.
- **Exact properties:** union's bit array is *identical* to the filter built from both item sets (commutative, idempotent); double-hashing probes are distinct for a power-of-two `m` across 20,000 items; the enhanced probe is exactly the double probe plus `(i³−i)/6`; bits beyond `m` are never set; filter bytes are identical across processes with different `PYTHONHASHSEED` (a control confirms Python's own `hash()` differs between them).
- **Small-size behaviour:** independent hashing follows the formula at m=128, plain double hashing is measurably above it, and enhanced double hashing measurably closes the gap (60 filters × 10,000 probes each).
- **Hand-checked formulas:** sizing `(9586, 7)`, the exact false-positive formula on tiny cases, the cardinality estimator, Wilson intervals (50/100 → [0.404, 0.596]).
- **Mutation check — 19 of 19 deliberate breaks caught**, including OR→XOR (also caught independently by the duplicate-add test alone), a set/check bit-mapping mismatch, independent hashing ignoring the probe number, dropping the odd-`h2` fix, an unused last bit, three separate breaks to the enhanced formula, un-squared `ln 2`, `k` floored instead of rounded, `k = 0`, union via AND, a popcount counting zeros, a flipped-sign estimator, a dropped exponent in the formula, an altered Wilson interval, skipped payload validation, `any` for `all`, and an inverted check in `GuardedStore`.
- **Not evaluated:** concurrent access; adversarial inputs chosen to force false positives against a *known* filter; multi-megabyte filters and cache-locality effects; non-ASCII-ish key distributions.

## Key Learnings

- **I asserted a mechanism I hadn't tested — and it was probably wrong.** My first write-up said double hashing's excess false positives came from "only ~m² distinct probe patterns, so many items share one". For 12 items in m=128 that can't be the story. Testing a hypothesis I *could* check — that the cause is the probes forming an arithmetic progression — by adding one cubic term removed most of the excess (m=128: 0.0122 → 0.0095, formula 0.0097). The measured effect was right the first time; the explanation attached to it was not, and it only got corrected because I stopped to test it before writing it down. It is now backed by shipped, tested code rather than a scratch script.
- **A measured comparison exposed a design flaw.** I computed all `k` probe positions before checking a single bit, so independent hashing paid for `k` digests on lookups that almost always fail on probe one or two. The throughput table showed it; lazy probing fixed it (2.66 → 1.24 µs at k=7) and changed the double-vs-independent conclusion for the dominant workload. Without that table I'd have written that double hashing is "k× faster" — it isn't, and for absent keys it barely matters.
- **"Equivalent asymptotically" hides the size at which it stops being true.** Double hashing is often presented as a free replacement for independent hashes. At m=128 it inflated the false-positive rate by 26%; the gap decays to ~1% by m=65,536. The formula isn't wrong; the independence assumption behind the equivalence is.
- **A claim I read off a table can be wrong in a small way.** I wrote that at 3× overfill "a majority of absent keys pass"; the measured rate is 43.7%, and it isn't a majority until about 5× (83%). Re-reading each sentence against the output caught it.
- **Test tolerances need a reason.** I first asserted fill ≈ 0.5 ± 0.03 at every target; at p=10%, `k` rounds down and the fill is 0.466 — the assertion was wrong, not the filter. An exact-equality assertion on a Wilson bound failed by one floating-point ulp. Neither was a code bug; both are the kind of failure that gets "fixed" by loosening a bound without understanding it.
- **Statistical tests can be deterministic *and* safe.** Fixed keys remove run-to-run randomness; 99.9% intervals make it very unlikely a correct implementation was unlucky on the keys chosen; and the mutation check showed the tests are still sensitive enough to catch a dropped exponent or a wrong formula.
- **A property that can be checked exactly should be.** "Union equals inserting both sets" is a bit-for-bit equality, not "roughly the same false-positive rate" — a much stronger test at no extra cost.
- **State a limitation next to the number it limits.** The 35× memory advantage excludes the key strings a real set also holds, and a Bloom filter can't enumerate, delete, or store values; a bare "35× smaller" would have been true and misleading.
