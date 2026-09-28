"""Bloom filter: approximate set membership in a fraction of a real set's memory.

Answers "have I seen x?" with no false negatives, and false positives at a
rate you choose by sizing. Its whole value is quantitative -- bits per
element, false-positive rate, and an estimate of how many distinct items it
holds all have closed-form predictions -- so this module exposes each of
them and the demo measures every one against its formula.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import Iterable, List, Optional, Tuple, Union

Item = Union[str, bytes]

_POPCOUNT = bytes(bin(i).count("1") for i in range(256))
_MAGIC = b"BLM1"
_HASHINGS = ("double", "independent", "enhanced")


# --- sizing and prediction formulas -------------------------------------------------


def optimal_params(capacity: int, fp_rate: float) -> Tuple[int, int]:
    """(m bits, k hashes) minimizing memory for `capacity` items at `fp_rate`.

    m = -n ln p / (ln 2)^2 and k = (m/n) ln 2. At the optimum exactly half of
    the bits are set, which is what makes the filter as informative as
    possible per bit.
    """
    if capacity < 1:
        raise ValueError("capacity must be >= 1")
    if not 0.0 < fp_rate < 1.0:
        raise ValueError("fp_rate must be strictly between 0 and 1")
    m = math.ceil(-capacity * math.log(fp_rate) / (math.log(2) ** 2))
    k = max(1, int(math.floor(m / capacity * math.log(2) + 0.5)))
    return m, k


def expected_fp_rate(m: int, k: int, n: int) -> float:
    """False-positive probability after n insertions, assuming each of the k
    probes is an independent uniform bit: (1 - (1 - 1/m)^(k n))^k."""
    return (1.0 - (1.0 - 1.0 / m) ** (k * n)) ** k


def estimate_cardinality(m: int, k: int, set_bits: int) -> float:
    """Swamidass & Baldi: how many distinct items produced `set_bits` set
    bits. n = -(m/k) ln(1 - X/m). Diverges as the filter fills."""
    if set_bits >= m:
        return math.inf
    return -(m / k) * math.log(1.0 - set_bits / m)


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion. Unlike the naive
    normal interval it stays sensible near 0 -- where false-positive rates live."""
    if trials == 0:
        return 0.0, 1.0
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


# --- the filter -----------------------------------------------------------------------


def _to_bytes(item: Item) -> bytes:
    return item if isinstance(item, bytes) else item.encode("utf-8")


class BloomFilter:
    """Fixed-size bit array plus k hash functions.

    hashing="double" derives all k indexes from one digest (Kirsch &
    Mitzenmacher: index_i = h1 + i*h2 mod m), one hash computation per item.
    hashing="independent" salts a separate digest per probe: k times the
    hashing work, and the textbook model the false-positive formula assumes.
    hashing="enhanced" is double hashing plus a cubic term (Dillinger &
    Manolios), which stops the k probes being an arithmetic progression mod m;
    same one-digest cost, and it removes most of double hashing's excess
    false positives at small m (measured in the demo).

    Hashes are BLAKE2b, never Python's hash(): str hashing there is salted
    per process, so a filter built in one process (or shipped to another
    machine) would silently answer differently in the next.
    """

    def __init__(self, m: int, k: int, hashing: str = "double"):
        if m < 1 or k < 1:
            raise ValueError("m and k must be >= 1")
        if hashing not in _HASHINGS:
            raise ValueError(f"hashing must be one of {_HASHINGS}")
        self.m, self.k, self.hashing = m, k, hashing
        self._bits = bytearray((m + 7) // 8)

    @classmethod
    def from_capacity(cls, capacity: int, fp_rate: float, hashing: str = "double") -> "BloomFilter":
        m, k = optimal_params(capacity, fp_rate)
        return cls(m, k, hashing)

    # --- hashing ---

    def _iter_indexes(self, data: bytes):
        """Yield the k probe positions lazily, so a lookup that fails on an
        early probe never pays for the rest. An absent key usually hits an
        unset bit within ~2 probes (at 50% fill), so for independent hashing
        this skips most of its k digests on the dominant kind of lookup."""
        m, k = self.m, self.k
        if self.hashing in ("double", "enhanced"):
            digest = hashlib.blake2b(data, digest_size=16).digest()
            h1 = int.from_bytes(digest[:8], "big")
            h2 = int.from_bytes(digest[8:], "big") | 1  # odd: coprime with m whenever m is a power of two
            enhanced = self.hashing == "enhanced"
            for i in range(k):
                # (i^3 - i) / 6 is always an integer; it breaks the linear structure of h1 + i*h2
                yield (h1 + i * h2 + ((i**3 - i) // 6 if enhanced else 0)) % m
        else:
            for i in range(k):
                yield int.from_bytes(hashlib.blake2b(data, digest_size=8, salt=i.to_bytes(16, "big")).digest(), "big") % m

    def _indexes(self, data: bytes) -> List[int]:
        return list(self._iter_indexes(data))

    # --- core operations ---

    def add(self, item: Item) -> None:
        bits = self._bits
        for i in self._iter_indexes(_to_bytes(item)):
            bits[i >> 3] |= 1 << (i & 7)  # OR, not XOR: adding the same item twice must not switch its bits back off

    def update(self, items: Iterable[Item]) -> None:
        for item in items:
            self.add(item)

    def __contains__(self, item: Item) -> bool:
        bits = self._bits
        return all(bits[i >> 3] & (1 << (i & 7)) for i in self._iter_indexes(_to_bytes(item)))  # all() stops at the first unset bit

    # --- introspection ---

    def set_bits(self) -> int:
        return sum(_POPCOUNT[b] for b in self._bits)

    def fill_ratio(self) -> float:
        return self.set_bits() / self.m

    def estimated_count(self) -> float:
        """Distinct items inserted, inferred from how many bits are set."""
        return estimate_cardinality(self.m, self.k, self.set_bits())

    def estimated_fp_rate(self) -> float:
        """False-positive rate implied by the current fill: (set bits / m)^k."""
        return self.fill_ratio() ** self.k

    @property
    def memory_bytes(self) -> int:
        return len(self._bits)

    # --- combining and persistence ---

    def _check_compatible(self, other: "BloomFilter") -> None:
        if (self.m, self.k, self.hashing) != (other.m, other.k, other.hashing):
            raise ValueError("filters must share m, k and hashing scheme to be combined")

    def union(self, other: "BloomFilter") -> "BloomFilter":
        """A filter equal to one that had been given both filters' items."""
        self._check_compatible(other)
        out = BloomFilter(self.m, self.k, self.hashing)
        out._bits = bytearray(a | b for a, b in zip(self._bits, other._bits))
        return out

    def to_bytes(self) -> bytes:
        header = struct.pack(">4sQHB", _MAGIC, self.m, self.k, _HASHINGS.index(self.hashing))
        return header + bytes(self._bits)

    @classmethod
    def from_bytes(cls, blob: bytes) -> "BloomFilter":
        header_size = struct.calcsize(">4sQHB")
        if len(blob) < header_size:
            raise ValueError("truncated Bloom filter header")
        magic, m, k, hashing_id = struct.unpack(">4sQHB", blob[:header_size])
        if magic != _MAGIC:
            raise ValueError("not a Bloom filter blob (bad magic)")
        if hashing_id >= len(_HASHINGS):
            raise ValueError("unknown hashing scheme id")
        if len(blob) - header_size != (m + 7) // 8:
            raise ValueError("Bloom filter payload length does not match its header")
        f = cls(m, k, _HASHINGS[hashing_id])
        f._bits = bytearray(blob[header_size:])
        return f


# --- An application: shielding a slow store from lookups for keys that don't exist ---


class GuardedStore:
    """A key-value 'database' with a Bloom filter in front of it. A key the
    filter has never seen is answered immediately, with no database lookup:
    the filter has no false negatives, so that answer is always right. A key
    it *has* seen goes to the database -- including the small fraction of
    unknown keys that are false positives, which cost one wasted lookup."""

    def __init__(self, records: dict, fp_rate: float = 0.01):
        self._db = dict(records)
        self.db_lookups = 0
        self._filter = BloomFilter.from_capacity(max(1, len(self._db)), fp_rate)
        self._filter.update(self._db)

    def get(self, key: str) -> Optional[str]:
        if key not in self._filter:
            return None
        self.db_lookups += 1
        return self._db.get(key)


# --- Demo: measure every claim against its formula ----------------------------------------


def _observed_fpr(f: BloomFilter, probes: int, tag: str) -> Tuple[float, Tuple[float, float]]:
    fp = sum(1 for j in range(probes) if f"{tag}:{j}" in f)
    return fp / probes, wilson_interval(fp, probes)


def demo() -> None:
    import statistics
    import time
    import tracemalloc

    print("1) Sizing: bits per item and memory for 200,000 items, vs a Python set holding the same keys")
    n = 200_000
    keys = [f"user:{i}" for i in range(n)]
    tracemalloc.start()
    baseline = tracemalloc.get_traced_memory()[0]
    real_set = set(keys)
    set_bytes = tracemalloc.get_traced_memory()[0] - baseline
    tracemalloc.stop()
    del real_set
    print(f"   {'target FPR':>10} {'m (bits)':>10} {'k':>3} {'bits/item':>10} {'x optimum':>10} {'KB':>8} {'set is bigger by':>17}")
    for p in (0.1, 0.01, 0.001, 0.0001):
        m, k = optimal_params(n, p)
        print(f"   {p:>10} {m:>10,} {k:>3} {m / n:>10.2f} {m / n / math.log2(1 / p):>10.2f} {m / 8 / 1024:>8.0f} {set_bytes / (m / 8):>16.0f}x")
    print(f"   (set memory measured with tracemalloc: {set_bytes / 1024 / 1024:.1f} MB for {n:,} keys, excluding the key strings themselves")
    print("    already being held by the caller. 'x optimum' = bits/item over the information-theoretic minimum")
    print("    log2(1/p); it is 1.44 = 1/ln(2) at every target, a fixed price of using a bit array + k hashes.)")

    print("\n2) False-positive rate: observed vs target vs exact formula (50,000 items, 200,000 non-member probes)")
    n = 50_000
    print(f"   {'target':>8} {'observed':>9} {'95% CI (Wilson)':>18} {'exact formula':>14} {'CI contains formula?':>21} {'fill':>6}")
    for p in (0.1, 0.01, 0.001):
        f = BloomFilter.from_capacity(n, p)
        f.update(f"member:{i}" for i in range(n))
        rate, (lo, hi) = _observed_fpr(f, 200_000, "other")
        exact = expected_fp_rate(f.m, f.k, n)
        print(f"   {p:>8} {rate:>9.5f} [{lo:.5f}, {hi:.5f}] {exact:>14.5f} {str(lo <= exact <= hi):>21} {f.fill_ratio():>6.3f}")
    print("   (at the optimum, half the bits are set -- fill ~0.5 -- which is what maximizes information per bit)")

    print("\n3) Overfilling: a Bloom filter has no hard capacity; it just degrades. Sized for 50,000 items at 1%.")
    print(f"   {'items inserted':>15} {'x capacity':>11} {'observed FPR':>13} {'exact formula':>14} {'fill':>6} {'estimated count':>16}")
    f = BloomFilter.from_capacity(50_000, 0.01)
    inserted = 0
    for mult in (0.25, 0.5, 1, 1.5, 2, 3, 5, 8):
        target = int(50_000 * mult)
        f.update(f"member:{i}" for i in range(inserted, target))
        inserted = target
        rate, _ = _observed_fpr(f, 100_000, "other")
        est = f.estimated_count()
        est_txt = f"{est:>16,.0f}" if est != math.inf else f"{'inf (full)':>16}"
        print(f"   {inserted:>15,} {mult:>11} {rate:>13.4f} {expected_fp_rate(f.m, f.k, inserted):>14.4f} {f.fill_ratio():>6.3f} {est_txt}")

    print("\n4) Double hashing (1 digest per item) vs independent hashing (k digests): equal only asymptotically")
    print("   Mean observed FPR over 30 independent filters (+/- standard error), 20,000 probes each:")
    print(f"   {'m (bits)':>9} {'k':>3} {'items':>6} | {'exact formula':>13} | {'independent':>17} {'double':>17} {'enhanced double':>17} | {'double vs formula':>18}")
    for m, k, items in [(128, 4, 12), (256, 5, 25), (1024, 7, 100), (8192, 7, 800), (65536, 7, 6000)]:
        row = {}
        for hashing in ("independent", "double", "enhanced"):
            rates = []
            for t in range(30):
                g = BloomFilter(m, k, hashing)
                g.update(f"m{t}:{i}" for i in range(items))
                rates.append(sum(1 for j in range(20_000) if f"x{t}:{j}" in g) / 20_000)
            row[hashing] = (statistics.mean(rates), statistics.stdev(rates) / math.sqrt(len(rates)))
        th = expected_fp_rate(m, k, items)
        d_mean = row["double"][0]
        print(f"   {m:>9} {k:>3} {items:>6} | {th:>13.4f} | {row['independent'][0]:>8.4f} +/-{row['independent'][1]:.4f} "
              f"{d_mean:>8.4f} +/-{row['double'][1]:.4f} {row['enhanced'][0]:>8.4f} +/-{row['enhanced'][1]:.4f} | {(d_mean / th - 1) * 100:>+16.0f}%")
    print("   (independent hashing stays within ~2 standard errors of the formula at every size. Plain double hashing")
    print("    probes an arithmetic progression, so its k probes are correlated and false positives run high at small m;")
    print("    the enhanced variant adds a cubic term that breaks the progression, and closes most of that gap.)")

    print("\n   Throughput at realistic sizes (100,000 items; lookups exit at the first unset bit, so absent keys are cheap):")
    print(f"   {'k':>3} {'scheme':>12} {'add (us)':>9} {'absent lookup (us)':>19} {'present lookup (us)':>20}")
    for p in (0.01, 1e-6):
        for hashing in ("independent", "double"):
            g = BloomFilter.from_capacity(100_000, p, hashing)
            t = time.perf_counter()
            g.update(f"member:{i}" for i in range(100_000))
            add_us = (time.perf_counter() - t) / 100_000 * 1e6
            t = time.perf_counter()
            for j in range(100_000):
                f"other:{j}" in g
            absent_us = (time.perf_counter() - t) / 100_000 * 1e6
            t = time.perf_counter()
            for j in range(100_000):
                f"member:{j}" in g
            present_us = (time.perf_counter() - t) / 100_000 * 1e6
            print(f"   {g.k:>3} {hashing:>12} {add_us:>9.2f} {absent_us:>19.2f} {present_us:>20.2f}")

    print("\n5) Union: OR-ing two filters is exactly the filter you'd get from inserting both sets")
    n = 20_000
    a, b, both = (BloomFilter.from_capacity(n, 0.01) for _ in range(3))
    a.update(f"member:{i}" for i in range(0, n // 2))
    b.update(f"member:{i}" for i in range(n // 2, n))
    both.update(f"member:{i}" for i in range(n))
    u = a.union(b)
    print(f"   bit arrays identical: {u._bits == both._bits} | false negatives after union: "
          f"{sum(1 for i in range(n) if f'member:{i}' not in u)} | serialize round-trip identical: "
          f"{BloomFilter.from_bytes(u.to_bytes())._bits == u._bits}")

    print("\n6) Application: a Bloom filter in front of a database, when 90% of requested keys don't exist")
    records = {f"user:{i}": f"row{i}" for i in range(100_000)}
    store = GuardedStore(records, fp_rate=0.01)
    requests = 500_000
    misses = 0
    for r in range(requests):
        key = f"user:{r % 100_000}" if r % 10 == 0 else f"ghost:{r}"
        misses += key.startswith("ghost")
        got = store.get(key)
        assert (got is None) == key.startswith("ghost")  # never wrong: no false negatives, and the db is the final word
    expected_lookups = (requests - misses) + misses * 0.01
    print(f"   requests: {requests:,} ({misses:,} for nonexistent keys) | database lookups: {store.db_lookups:,} "
          f"instead of {requests:,} ({(1 - store.db_lookups / requests) * 100:.1f}% avoided)")
    print(f"   expected lookups = real keys + 1% of unknown keys = {expected_lookups:,.0f} | "
          f"wasted on false positives: {store.db_lookups - (requests - misses):,} of {misses:,} unknown-key requests")


if __name__ == "__main__":
    demo()
