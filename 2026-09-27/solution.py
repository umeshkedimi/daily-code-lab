"""Disjoint Set Union (Union-Find) with path compression and union by rank,
built up to Kruskal's Minimum Spanning Tree algorithm.

DSU answers one question cheaply -- "are x and y in the same group?" -- for
a structure that only ever merges groups, never splits them (network
connectivity, image segmentation, Kruskal's MST). The entire point of the
two optimizations below is an asymptotic claim (amortized ~O(alpha(n)) per
operation, alpha the inverse Ackermann function, effectively <= 4 for any
n that fits in the universe). A claim like that is worth measuring, not
just asserting -- see demo() and notes.md.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, Generic, Hashable, Iterable, List, Optional, Sequence, Set, Tuple, TypeVar

T = TypeVar("T", bound=Hashable)


class DisjointSet(Generic[T]):
    """Union-Find over arbitrary hashable elements.

    `path_compression` and `union_by_rank` are independently switchable so
    the demo can isolate what each one actually buys -- they fix different
    failure modes (see notes.md), not the same one twice.
    """

    def __init__(self, elements: Iterable[T] = (), *, path_compression: bool = True, union_by_rank: bool = True):
        self._parent: Dict[T, T] = {}
        self._rank: Dict[T, int] = {}
        self._size: Dict[T, int] = {}
        self._count = 0
        self.path_compression = path_compression
        self.union_by_rank = union_by_rank
        self.find_hops = 0  # cumulative pointer hops across every find() call -- the amortized-cost instrument
        for e in elements:
            self.add(e)

    def add(self, x: T) -> None:
        if x in self._parent:
            return
        self._parent[x] = x
        self._rank[x] = 0
        self._size[x] = 1
        self._count += 1

    def find(self, x: T) -> T:
        root = x
        hops = 0
        while self._parent[root] != root:
            root = self._parent[root]
            hops += 1
        self.find_hops += hops
        if self.path_compression:
            # Full compression: every node visited on the way up is repointed
            # directly at the root, so the *next* find through any of them is O(1).
            #
            # NOTE: `node, self._parent[node] = self._parent[node], root` looks
            # equivalent and is wrong -- Python assigns tuple targets left to
            # right, so `node` is already updated to the *next* node by the time
            # the `self._parent[node]` subscript on the left is evaluated, and
            # the wrong (already-correct) entry gets overwritten while the
            # intended one is silently left untouched. Caught by a test
            # asserting the exact post-compression depth, not just "faster".
            node = x
            while node != root:
                nxt = self._parent[node]
                self._parent[node] = root
                node = nxt
        return root

    def union(self, x: T, y: T) -> bool:
        """Naive convention when union_by_rank is off: root(x) is always
        attached under root(y). Fixed and asymmetric on purpose -- it's what
        makes an adversarial union order able to force a linear chain."""
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        if self.union_by_rank:
            if self._rank[rx] < self._rank[ry]:
                rx, ry = ry, rx
            if self._rank[rx] == self._rank[ry]:
                self._rank[rx] += 1
        self._parent[ry] = rx
        self._size[rx] += self._size[ry]
        self._count -= 1
        return True

    def connected(self, x: T, y: T) -> bool:
        return self.find(x) == self.find(y)

    def component_size(self, x: T) -> int:
        return self._size[self.find(x)]

    @property
    def num_components(self) -> int:
        return self._count

    def groups(self) -> Dict[T, List[T]]:
        out: Dict[T, List[T]] = {}
        for x in self._parent:
            out.setdefault(self.find(x), []).append(x)
        return out

    def depth(self, x: T) -> int:
        """Steps to the root, WITHOUT applying path compression -- a pure
        read of the current tree shape, for measuring structure independent
        of the find_hops work counter."""
        d = 0
        while self._parent[x] != x:
            x = self._parent[x]
            d += 1
        return d

    def max_depth(self) -> int:
        return max((self.depth(x) for x in self._parent), default=0)


# --- Construction orders used to demonstrate (and to attack) the DSU -------


def chain_union_order(n: int) -> List[Tuple[int, int]]:
    """union(i, i-1) for i in 1..n-1. `union(a, b)` attaches root(b) under
    root(a) (see union() below), so calling it as (i, i-1) each time makes
    the *new* element i swallow the *old* root i-1 as a child, one link at a
    time: parent[0]=1, parent[1]=2, ..., parent[n-2]=n-1. Under the naive
    attachment rule (no union_by_rank), this is the textbook worst case: a
    linear chain of depth n-1. (Calling it as (i-1, i) instead produces a
    flat star, not a chain -- the direction of the pair matters, and getting
    it backwards was the first bug caught while building this exercise; see
    notes.md.)"""
    return [(i, i - 1) for i in range(1, n)]


def doubling_union_order(n: int) -> List[Tuple[int, int]]:
    """Repeatedly merge adjacent equal-size groups: (0,1),(2,3),... then
    (0,2),(4,6),... then (0,4),.... Each merge combines two trees of the
    SAME rank -- exactly the condition under which union-by-rank increments
    rank -- so, unlike chain_union_order, this order reaches the
    floor(log2 n) depth bound tightly (exactly, for n a power of two) rather
    than falling well short of it."""
    order = []
    step = 1
    while step < n:
        for i in range(0, n - step, 2 * step):
            order.append((i, i + step))
        step *= 2
    return order


def random_union_order(n: int, rng: random.Random) -> List[Tuple[int, int]]:
    order = list(range(n))
    rng.shuffle(order)
    return [(order[i], order[i + 1]) for i in range(n - 1)]


# --- A trustworthy but slow oracle, for differential testing ----------------


class NaiveGroups:
    """Reference model: a plain list of sets, no tree at all. find/union are
    O(n) but the logic is trivial to get right -- exactly the point of an
    oracle. Used to cross-check DisjointSet under randomized operations."""

    def __init__(self, elements: Iterable[T] = ()):
        self._groups: List[Set[T]] = [{e} for e in elements]

    def connected(self, x: T, y: T) -> bool:
        return any(x in g and y in g for g in self._groups)

    def union(self, x: T, y: T) -> bool:
        gx = next(g for g in self._groups if x in g)
        gy = next(g for g in self._groups if y in g)
        if gx is gy:
            return False
        gx |= gy
        self._groups.remove(gy)
        return True

    @property
    def num_components(self) -> int:
        return len(self._groups)


# --- Kruskal's MST -----------------------------------------------------------


@dataclass(frozen=True)
class Edge:
    u: int
    v: int
    weight: float


def kruskal_mst(n: int, edges: Sequence[Edge]) -> Tuple[List[Edge], float, int]:
    """Returns (forest_edges, total_weight, num_components). If the graph is
    disconnected, this is a minimum spanning FOREST -- one tree per
    component -- not an error; num_components > 1 tells the caller.
    """
    dsu: DisjointSet[int] = DisjointSet(range(n))
    chosen: List[Edge] = []
    for e in sorted(edges, key=lambda e: e.weight):
        if dsu.union(e.u, e.v):  # union() itself is the cycle check: True iff u, v were in different components
            chosen.append(e)
            if len(chosen) == n - 1:
                break
    return chosen, sum(e.weight for e in chosen), dsu.num_components


def prim_mst_weight(n: int, edges: Sequence[Edge]) -> Tuple[float, int]:
    """Independent O(V^2) reference for cross-checking Kruskal: grow one
    tree from vertex 0, always adding the cheapest edge that leaves the
    current tree. On a disconnected graph, exhausts vertex 0's reachable
    component; the caller reruns per remaining component."""
    adj: Dict[int, Dict[int, float]] = {i: {} for i in range(n)}
    for e in edges:
        adj[e.u][e.v] = min(adj[e.u].get(e.v, math.inf), e.weight)
        adj[e.v][e.u] = min(adj[e.v].get(e.u, math.inf), e.weight)

    unvisited = set(range(n))
    start = next(iter(unvisited))
    unvisited.remove(start)
    best_cost = {v: adj[start].get(v, math.inf) for v in unvisited}
    total = 0.0
    reached = 1
    while unvisited:
        v = min(unvisited, key=lambda x: best_cost[x])
        if best_cost[v] == math.inf:
            break  # nothing left in this vertex's component
        total += best_cost[v]
        reached += 1
        unvisited.remove(v)
        for w in unvisited:
            if adj[v].get(w, math.inf) < best_cost[w]:
                best_cost[w] = adj[v][w]
    return total, reached


def mst_forest_weight_via_prim(n: int, edges: Sequence[Edge]) -> Tuple[float, int]:
    """Prim's algorithm run once per connected component, for comparison
    against Kruskal on graphs that may be disconnected."""
    dsu: DisjointSet[int] = DisjointSet(range(n))
    for e in edges:
        dsu.union(e.u, e.v)
    total = 0.0
    for root, members in dsu.groups().items():
        if len(members) == 1:
            continue
        relabel = {v: i for i, v in enumerate(members)}
        sub_edges = [Edge(relabel[e.u], relabel[e.v], e.weight) for e in edges if e.u in relabel and e.v in relabel]
        w, _ = prim_mst_weight(len(members), sub_edges)
        total += w
    return total, dsu.num_components


def brute_force_mst_weight(n: int, edges: Sequence[Edge]) -> Optional[float]:
    """Exhaustive oracle for tiny graphs only: try every (n-1)-subset of
    edges, keep the minimum-weight one that is a valid spanning tree (via a
    fresh DisjointSet used purely as a spanning-tree checker). Returns None
    if the graph has fewer than n-1 edges (can't possibly span)."""
    from itertools import combinations

    if len(edges) < n - 1:
        return None
    best = None
    for combo in combinations(edges, n - 1):
        dsu: DisjointSet[int] = DisjointSet(range(n))
        if all(dsu.union(e.u, e.v) for e in combo) and dsu.num_components == 1:
            w = sum(e.weight for e in combo)
            if best is None or w < best:
                best = w
    return best


# --- Demo: measure, don't assert ------------------------------------------------


def _avg_hops_per_query(dsu: DisjointSet, n: int, n_queries: int, rng: random.Random) -> float:
    start = dsu.find_hops
    for _ in range(n_queries):
        dsu.find(rng.randrange(n))
    return (dsu.find_hops - start) / n_queries


def _built(order: List[Tuple[int, int]], n: int, path_compression: bool, union_by_rank: bool) -> DisjointSet:
    d: DisjointSet = DisjointSet(range(n), path_compression=path_compression, union_by_rank=union_by_rank)
    for a, b in order:
        d.union(a, b)
    return d


def demo() -> None:
    import math
    import time

    rng = random.Random(0)
    configs = [("neither", False, False), ("compress-only", True, False), ("rank-only", False, True), ("both", True, True)]

    print("1) A specific adversarial union ORDER (chain_union_order) forces a real O(n) chain without rank")
    print(f"   {'n':>8} {'neither: depth(0)':>18} {'expected n-1':>13} {'rank-only: max_depth':>21} {'floor(log2 n)':>14}")
    for n in (10, 100, 1_000, 10_000):
        no_opt = _built(chain_union_order(n), n, False, False)
        rank_only = _built(chain_union_order(n), n, False, True)
        print(f"   {n:>8} {no_opt.depth(0):>18} {n - 1:>13} {rank_only.max_depth():>21} {math.floor(math.log2(n)):>14}")

    print("\n2) A DIFFERENT order (pairwise-doubling merges) shows rank's floor(log2 n) bound is exactly tight")
    print(f"   {'n':>8} {'rank-only: max_depth':>21} {'floor(log2 n)':>14} {'exactly equal?':>15}")
    for k in (7, 10, 13, 16):
        n = 2 ** k
        d = _built(doubling_union_order(n), n, False, True)
        bound = math.floor(math.log2(n))
        print(f"   {n:>8} {d.max_depth():>21} {bound:>14} {str(d.max_depth() == bound):>15}")
    print("   (this order pairs only equal-rank trees, so union_by_rank's tie-break never actually fires --")
    print("    'rank-only' and 'neither' build the IDENTICAL tree here, which is exactly why it reaches the bound)")

    print("\n3) Path compression ALONE amortizes over MANY queries -- it does not bound any single query")
    print("   (same pathological chain both rows; only the query budget relative to n differs)")
    print(f"   {'n':>8} {'avg hops/query at 20000 fixed queries':>38} {'avg hops/query at 5n queries':>29}")
    for n in (128, 1_024, 8_192, 65_536):
        fixed = _avg_hops_per_query(_built(chain_union_order(n), n, True, False), n, 20_000, random.Random(1))
        scaled = _avg_hops_per_query(_built(chain_union_order(n), n, True, False), n, 5 * n, random.Random(1))
        print(f"   {n:>8} {fixed:>38.2f} {scaled:>29.2f}")
    print("   -- with a FIXED query budget, most of a large chain's nodes are never individually touched, so an")
    print("      un-compressed tail persists; union_by_rank has no such warm-up cost, because it bounds the WORST")
    print("      case directly. With both together, there's rarely a deep path to begin with (see below).")

    print("\n4) Realistic mixed workload (n-1 random unions + n random find queries, interleaved, NOT adversarial):")
    print(f"   {'n':>9} {'neither':>10} {'compress-only':>14} {'rank-only':>10} {'both':>8}")
    for n in (1_000, 10_000, 100_000, 1_000_000):
        row = []
        for _, pc, ur in configs:
            d: DisjointSet = DisjointSet(range(n), path_compression=pc, union_by_rank=ur)
            ops = [("u", a, b) for a, b in random_union_order(n, random.Random(2))]
            ops += [("f", rng.randrange(n), None) for _ in range(n)]
            random.Random(3).shuffle(ops)
            for kind, a, b in ops:
                d.union(a, b) if kind == "u" else d.find(a)
            row.append(d.find_hops / len(ops))
        print(f"   {n:>9,} " + " ".join(f"{v:>10.3f}   " if i == 0 else f"{v:>14.3f}" if i == 1 else f"{v:>10.3f}" if i == 2 else f"{v:>8.3f}" for i, v in enumerate(row)))
    print("   (compare this to (1): the SAME 'neither'/'compress-only' code, on a merely RANDOM access pattern")
    print("    instead of the adversarial chain order, is already close to flat -- the pathology needs a specific")
    print("    adversarial order to appear, which is exactly why an attacker-controlled union order is the risk.)")

    print("\n5) Kruskal's MST on a small weighted graph")
    edges = [
        Edge(0, 1, 4), Edge(0, 2, 1), Edge(1, 2, 2), Edge(1, 3, 5),
        Edge(2, 3, 8), Edge(2, 4, 10), Edge(3, 4, 2), Edge(3, 5, 6), Edge(4, 5, 3),
    ]
    mst, weight, components = kruskal_mst(6, edges)
    brute = brute_force_mst_weight(6, edges)
    print(f"   edges chosen: {[(e.u, e.v, e.weight) for e in mst]}")
    print(f"   total weight: {weight} | components: {components} | brute-force optimum: {brute} | match: {weight == brute}")

    print("\n6) Kruskal (DSU) vs Prim (independent O(V^2) reference), random graphs, connected and disconnected")
    rng2 = random.Random(42)
    mismatches = 0
    for trial in range(200):
        n = rng2.randint(2, 30)
        density = rng2.choice([0.1, 0.3, 0.6, 0.9])  # low density -> likely disconnected, tests the forest case
        pairs = [(u, v) for u in range(n) for v in range(u + 1, n)]
        rng2.shuffle(pairs)
        m = max(0, int(len(pairs) * density))
        random_edges = [Edge(u, v, rng2.randint(1, 100)) for u, v in pairs[:m]]
        _, kw, kc = kruskal_mst(n, random_edges)
        pw, _ = mst_forest_weight_via_prim(n, random_edges)
        if kw != pw:
            mismatches += 1
    print(f"   {200} random graphs (n<=30, densities 0.1-0.9, disconnected included): {mismatches} mismatches between Kruskal and Prim")

    print("\n7) Timing at scale: n=1,000,000 elements, 2,000,000 unions + finds")
    n = 1_000_000
    d = DisjointSet(range(n))
    ops = [("u", a, b) for a, b in random_union_order(n, random.Random(4))]
    ops += [("f", random.Random(5).randrange(n), None) for _ in range(n)]
    start = time.perf_counter()
    for kind, a, b in ops:
        d.union(a, b) if kind == "u" else d.find(a)
    elapsed = time.perf_counter() - start
    print(f"   {len(ops):,} operations in {elapsed:.2f}s ({elapsed / len(ops) * 1e6:.3f} us/op), "
          f"total hops = {d.find_hops:,} ({d.find_hops / len(ops):.3f} hops/op)")


if __name__ == "__main__":
    demo()
