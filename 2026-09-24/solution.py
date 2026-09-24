"""Consistent hashing ring with virtual nodes, plus the two baselines it is
measured against (modulo sharding and rendezvous hashing).

Problem it solves: sharding keys across N nodes so that when N changes, only
the keys that *must* move (about 1/N of them) do. `hash(key) % N` reshuffles
nearly everything; on a cache cluster that is a cold-cache stampede.

The ring: hash each node to many points ("virtual nodes") on a circle of
2**64 positions. A key belongs to the first virtual node at or clockwise
of its own hash. Adding a node only steals the arcs its new points land
in; removing one only hands its arcs to their clockwise neighbours.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from typing import Callable, Dict, Iterable, List, Sequence, Tuple, Union

Key = Union[str, bytes]


def _to_bytes(key: Key) -> bytes:
    return key if isinstance(key, bytes) else key.encode("utf-8")


def stable_hash64(data: bytes) -> int:
    """A process- and machine-independent 64-bit hash. NOT Python's hash():
    str hashing there is salted per process (PYTHONHASHSEED), so two servers
    would silently build different rings and disagree about key ownership."""
    return int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big")


class EmptyRingError(LookupError):
    """A lookup was made on a ring with no nodes."""


class HashRing:
    """Consistent hash ring with virtual nodes and per-node weights.

    Ring state is an immutable snapshot -- parallel sorted (positions, owners)
    lists plus a node count, replaced wholesale on every change. Readers grab
    the current snapshot once and never observe a half-updated ring, so
    lookups need no lock even while nodes are being added or removed.

    Entries are ordered by (position, node name), so the ring depends only
    on the *set* of nodes (and weights), never on the order they were added
    in -- including when two virtual nodes collide on a position.
    """

    def __init__(
        self,
        vnodes: int = 128,
        hash_fn: Callable[[bytes], int] = stable_hash64,
        bits: int = 64,
    ):
        if vnodes < 1:
            raise ValueError("vnodes must be >= 1")
        self._vnodes = vnodes
        self._hash_fn = hash_fn
        self._ring_size = 1 << bits
        self._weights: Dict[str, int] = {}
        # (positions, owners, distinct node count) -- the node count lives IN the snapshot so
        # a reader never pairs one version of the ring with another version's count.
        self._ring: Tuple[List[int], List[str], int] = ([], [], 0)

    # --- membership ---------------------------------------------------------

    def _position(self, data: bytes) -> int:
        return self._hash_fn(data) % self._ring_size

    def add_node(self, node: str, weight: int = 1) -> None:
        """`weight` scales the node's virtual-node count, so a node with
        weight 3 takes roughly 3x the keys of a weight-1 node."""
        self.add_nodes([(node, weight)])

    def add_nodes(self, nodes: Iterable[Union[str, Tuple[str, int]]]) -> None:
        """Add several nodes (names, or (name, weight) pairs) with a single
        sort. Calling add_node in a loop re-sorts the whole ring every time --
        O(N^2 * V log(N*V)) to build a ring -- which measured ~3 s for 1,000
        nodes x 100 vnodes. All-or-nothing: if any entry is invalid, the ring
        is left untouched."""
        new: Dict[str, int] = {}
        for item in nodes:
            name, weight = (item, 1) if isinstance(item, str) else item
            if weight < 1:
                raise ValueError("weight must be >= 1")
            if name in self._weights or name in new:
                raise ValueError(f"node {name!r} is already on the ring")
            new[name] = weight
        positions, owners, _ = self._ring
        entries = list(zip(positions, owners))
        for name, weight in new.items():
            entries.extend((self._position(f"{name}#{i}".encode()), name) for i in range(self._vnodes * weight))
        entries.sort()
        self._weights.update(new)
        self._ring = ([p for p, _ in entries], [o for _, o in entries], len(self._weights))

    def remove_node(self, node: str) -> None:
        if node not in self._weights:
            raise KeyError(node)
        positions, owners, _ = self._ring
        kept = [(p, o) for p, o in zip(positions, owners) if o != node]
        del self._weights[node]
        self._ring = ([p for p, _ in kept], [o for _, o in kept], len(self._weights))

    @property
    def nodes(self) -> List[str]:
        return sorted(self._weights)

    def __len__(self) -> int:
        return len(self._weights)

    # --- lookup -------------------------------------------------------------

    def _start_index(self, positions: Sequence[int], key: Key) -> int:
        # First virtual node at or after the key's hash; past the last one,
        # wrap to the first. A key hashing exactly to a virtual node's
        # position belongs to that node, i.e. a node owns the arc (prev, pos].
        i = bisect_left(positions, self._position(_to_bytes(key)))
        return i if i < len(positions) else 0

    def get_node(self, key: Key) -> str:
        positions, owners, _ = self._ring  # one consistent snapshot
        if not positions:
            raise EmptyRingError("no nodes on the ring")
        return owners[self._start_index(positions, key)]

    def get_nodes(self, key: Key, n: int) -> List[str]:
        """The first `n` *distinct physical* nodes clockwise from the key --
        a replication preference list. The first entry equals get_node(key).
        Returns fewer than n if the ring has fewer nodes."""
        positions, owners, distinct = self._ring  # everything below reads this one snapshot
        if not positions:
            raise EmptyRingError("no nodes on the ring")
        wanted = min(n, distinct)
        if wanted <= 0:  # the loop below only checks "enough?" after appending, so 0/negative must exit here
            return []
        found: List[str] = []
        i = self._start_index(positions, key)
        for step in range(len(positions)):
            owner = owners[(i + step) % len(positions)]
            if owner not in found:
                found.append(owner)
                if len(found) == wanted:
                    break
        return found

    # --- introspection ------------------------------------------------------

    def arcs(self) -> Dict[str, int]:
        """Exact number of ring positions each node owns; sums to the ring size."""
        positions, owners, _ = self._ring
        owned: Dict[str, int] = {n: 0 for n in set(owners)}
        for i, owner in enumerate(owners):
            prev = positions[i - 1] if i > 0 else positions[-1] - self._ring_size
            owned[owner] += positions[i] - prev
        return owned

    def ownership(self) -> Dict[str, float]:
        """Fraction of the key space each node owns (exact, not sampled)."""
        return {n: a / self._ring_size for n, a in self.arcs().items()}

    def check_invariants(self) -> None:
        positions, owners, distinct = self._ring
        assert distinct == len(self._weights) == len(set(owners))
        assert len(positions) == len(owners)
        assert list(zip(positions, owners)) == sorted(zip(positions, owners)), "ring not sorted"
        assert all(0 <= p < self._ring_size for p in positions)
        for node, weight in self._weights.items():
            assert owners.count(node) == self._vnodes * weight, f"wrong vnode count for {node}"
        assert set(owners) == set(self._weights)
        if positions:
            assert sum(self.arcs().values()) == self._ring_size


# --- Baselines -------------------------------------------------------------------


class ModuloSharder:
    """hash(key) % N. Perfectly balanced and O(1) -- and it reassigns almost
    every key whenever N changes."""

    def __init__(self, nodes: Sequence[str], hash_fn: Callable[[bytes], int] = stable_hash64):
        self._nodes = list(nodes)
        self._hash_fn = hash_fn

    def get_node(self, key: Key) -> str:
        return self._nodes[self._hash_fn(_to_bytes(key)) % len(self._nodes)]


class RendezvousHasher:
    """Highest-random-weight hashing: a key goes to the node with the largest
    hash(key, node). No ring state, near-perfect balance and minimal
    disruption -- but every lookup costs O(N) hash evaluations."""

    def __init__(self, nodes: Sequence[str], hash_fn: Callable[[bytes], int] = stable_hash64):
        self._nodes = list(nodes)
        self._hash_fn = hash_fn

    def get_node(self, key: Key) -> str:
        k = _to_bytes(key)
        # ties (probability ~ N/2**64) broken by name so the result is deterministic
        return max(self._nodes, key=lambda n: (self._hash_fn(k + b"|" + n.encode()), n))


# --- Demo: measure, don't assert -----------------------------------------------------


def _moved(before: Callable[[str], str], after: Callable[[str], str], keys: Sequence[str]):
    """(fraction of keys that changed owner, {old owners of moved keys}, {new owners of moved keys})"""
    sources, dests, n = set(), set(), 0
    for k in keys:
        a, b = before(k), after(k)
        if a != b:
            n += 1
            sources.add(a)
            dests.add(b)
    return n / len(keys), sources, dests


def _ring(nodes: Sequence[str], vnodes: int) -> HashRing:
    r = HashRing(vnodes=vnodes)
    r.add_nodes(nodes)
    return r


def demo() -> None:
    import statistics
    import time

    keys = [f"user:{i}" for i in range(100_000)]
    nodes = [f"node-{i}" for i in range(10)]

    print("1) Adding an 11th node to 10 (100,000 keys). Ideal: 1/11 = 9.1% moved, all onto the new node.")
    print(f"   {'strategy':22} {'keys moved':>11}   moved keys went to")
    rows = [("modulo (hash % N)", ModuloSharder(nodes).get_node, ModuloSharder(nodes + ["node-10"]).get_node)]
    for v in (1, 16, 128, 512):
        rows.append((f"ring, {v} vnodes/node", _ring(nodes, v).get_node, _ring(nodes + ["node-10"], v).get_node))
    rows.append(("rendezvous", RendezvousHasher(nodes).get_node, RendezvousHasher(nodes + ["node-10"]).get_node))
    for name, before, after in rows:
        frac, _, dests = _moved(before, after, keys)
        where = "only the new node" if dests == {"node-10"} else f"{len(dests)} different nodes (reshuffled)"
        print(f"   {name:22} {frac:>10.1%}   {where}")

    print("\n2) Removing node-3 from 10. Ideal: ~10% moved, and ONLY node-3's keys.")
    print(f"   {'strategy':22} {'keys moved':>11}   moved keys came from")
    survivors = [n for n in nodes if n != "node-3"]
    rows = [("modulo (hash % N)", ModuloSharder(nodes).get_node, ModuloSharder(survivors).get_node),
            ("ring, 128 vnodes/node", _ring(nodes, 128).get_node, _ring(survivors, 128).get_node),
            ("rendezvous", RendezvousHasher(nodes).get_node, RendezvousHasher(survivors).get_node)]
    for name, before, after in rows:
        frac, sources, _ = _moved(before, after, keys)
        where = "only the removed node" if sources == {"node-3"} else f"{len(sources)} different nodes"
        print(f"   {name:22} {frac:>10.1%}   {where}")

    print("\n3) Load balance vs virtual nodes: 10 nodes, exact ring ownership, mean of 20 independent rings.")
    print("   CV = std/mean of per-node share (0 = perfectly even). Theory: sqrt((N-1)/(N*V+1)).")
    print(f"   {'vnodes/node':>11} {'CV measured':>12} {'CV theory':>10} {'busiest/average':>16} {'ring entries':>13}")
    n_nodes = 10
    for v in (1, 10, 50, 100, 200, 500, 1000):
        cvs, peaks = [], []
        for trial in range(20):
            r = _ring([f"t{trial}-node-{i}" for i in range(n_nodes)], v)
            shares = list(r.ownership().values())
            cvs.append(statistics.pstdev(shares) / statistics.mean(shares))
            peaks.append(max(shares) * n_nodes)
        theory = ((n_nodes - 1) / (n_nodes * v + 1)) ** 0.5
        print(f"   {v:>11} {statistics.mean(cvs):>12.3f} {theory:>10.3f} {statistics.mean(peaks):>15.2f}x {n_nodes * v:>13,}")

    print("\n4) Cost at scale, 100 vnodes per node. Lookup: ring is O(log(N*V)), rendezvous is O(N).")
    print("   Membership: build a whole ring by add_node in a loop vs one bulk add_nodes call.")
    print(f"   {'nodes':>6} {'ring lookup':>12} {'rendezvous':>11}   {'build: loop':>11} {'build: bulk':>11} {'add 1 node':>11}")
    sample = keys[:2_000]
    for n in (10, 100, 1000):
        names = [f"node-{i}" for i in range(n)]
        t = time.perf_counter()
        looped = HashRing(vnodes=100)
        for name in names:
            looped.add_node(name)
        loop_ms = (time.perf_counter() - t) * 1e3
        t = time.perf_counter()
        ring = _ring(names, 100)
        bulk_ms = (time.perf_counter() - t) * 1e3
        t = time.perf_counter()
        ring.add_node("extra-node")
        add_ms = (time.perf_counter() - t) * 1e3
        rv = RendezvousHasher(names)
        t = time.perf_counter()
        for k in sample:
            ring.get_node(k)
        ring_ns = (time.perf_counter() - t) / len(sample) * 1e9
        few = sample[:200 if n >= 1000 else 2_000]
        t = time.perf_counter()
        for k in few:
            rv.get_node(k)
        rv_ns = (time.perf_counter() - t) / len(few) * 1e9
        print(f"   {n:>6} {ring_ns:>9,.0f} ns {rv_ns:>8,.0f} ns   {loop_ms:>8,.0f} ms {bulk_ms:>8,.0f} ms {add_ms:>8,.1f} ms")

if __name__ == "__main__":
    demo()
