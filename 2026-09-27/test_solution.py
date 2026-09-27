import math
import random
from itertools import combinations

import pytest

from solution import (
    DisjointSet,
    Edge,
    NaiveGroups,
    brute_force_mst_weight,
    chain_union_order,
    doubling_union_order,
    kruskal_mst,
    mst_forest_weight_via_prim,
    prim_mst_weight,
    random_union_order,
)


def built(order, n, path_compression, union_by_rank):
    d = DisjointSet(range(n), path_compression=path_compression, union_by_rank=union_by_rank)
    for a, b in order:
        d.union(a, b)
    return d


# --- basic correctness -------------------------------------------------------------


def test_new_elements_start_as_singletons():
    d = DisjointSet(range(5))
    assert d.num_components == 5
    assert all(not d.connected(i, j) for i in range(5) for j in range(5) if i != j)
    assert all(d.connected(i, i) for i in range(5))


def test_union_merges_and_is_idempotent():
    d = DisjointSet(range(4))
    assert d.union(0, 1) is True
    assert d.num_components == 3
    assert d.connected(0, 1)
    assert d.union(0, 1) is False  # already together
    assert d.num_components == 3


def test_union_is_transitive():
    d = DisjointSet(range(5))
    d.union(0, 1)
    d.union(1, 2)
    assert d.connected(0, 2)
    assert not d.connected(0, 3)
    d.union(3, 4)
    assert not d.connected(0, 3)
    d.union(2, 3)
    assert d.connected(0, 4)
    assert d.num_components == 1


def test_component_size_and_groups():
    d = DisjointSet(range(6))
    d.union(0, 1)
    d.union(1, 2)
    d.union(4, 5)
    assert d.component_size(0) == d.component_size(1) == d.component_size(2) == 3
    assert d.component_size(3) == 1
    groups = d.groups()
    sizes = sorted(len(members) for members in groups.values())
    assert sizes == [1, 2, 3]
    assert sum(len(members) for members in groups.values()) == 6


def test_add_is_idempotent_and_new_elements_are_isolated():
    d = DisjointSet([1, 2])
    d.union(1, 2)
    d.add(1)  # already present -- must not reset its group
    assert d.connected(1, 2)
    d.add(3)
    assert d.num_components == 2
    assert not d.connected(1, 3)


def test_works_over_arbitrary_hashable_elements_not_just_ints():
    d = DisjointSet(["alice", "bob", "carol"])
    d.union("alice", "bob")
    assert d.connected("alice", "bob")
    assert not d.connected("alice", "carol")


# --- the actual asymptotic claims -----------------------------------------------------


@pytest.mark.parametrize("n", [10, 50, 200, 1000])
def test_chain_order_without_rank_forces_a_true_linear_chain(n):
    d = built(chain_union_order(n), n, path_compression=False, union_by_rank=False)
    assert d.depth(0) == n - 1  # the exact worst case, not just "large"
    assert d.max_depth() == n - 1


def test_chain_order_pairs_must_be_in_this_direction_to_build_a_chain():
    """The reversed pairing looks superficially similar but produces a flat
    star instead -- documented as the first bug caught building this
    exercise (see notes.md). Locking the correct direction in with a test."""
    n = 50
    correct = built(chain_union_order(n), n, path_compression=False, union_by_rank=False)
    assert correct.max_depth() == n - 1
    reversed_pairs = [(b, a) for a, b in chain_union_order(n)]
    flat = built(reversed_pairs, n, path_compression=False, union_by_rank=False)
    assert flat.max_depth() == 1


@pytest.mark.parametrize("n", [10, 100, 1000, 10_000, 100_000])
def test_union_by_rank_bounds_depth_by_floor_log2_n_on_the_chain_order(n):
    """The bound holds regardless of how loose it is for a particular order
    (chain order lands well under it -- see notes.md); the guarantee being
    tested is the upper bound itself, on adversarial input."""
    d = built(chain_union_order(n), n, path_compression=False, union_by_rank=True)
    assert d.max_depth() <= math.floor(math.log2(n))


@pytest.mark.parametrize("k", [1, 4, 7, 10, 13])
def test_union_by_rank_bound_is_exactly_tight_on_the_doubling_order(k):
    n = 2 ** k
    d = built(doubling_union_order(n), n, path_compression=False, union_by_rank=True)
    assert d.max_depth() == math.floor(math.log2(n))  # equality, not just <=


def test_doubling_order_never_triggers_ranks_tie_break_so_rank_only_equals_neither():
    """Every merge in this order combines two equal-rank trees, so
    `rank[rx] < rank[ry]` is never true -- rank only increments a counter
    here, it never changes which root wins. Confirms *why* this order is
    the tight case: it defeats the mechanism, it doesn't exploit it."""
    n = 256
    without_rank = built(doubling_union_order(n), n, path_compression=False, union_by_rank=False)
    with_rank = built(doubling_union_order(n), n, path_compression=False, union_by_rank=True)
    assert without_rank._parent == with_rank._parent  # byte-identical resulting structure


def test_path_compression_collapses_a_path_after_the_first_find():
    n = 500
    d = built(chain_union_order(n), n, path_compression=True, union_by_rank=False)
    assert d.depth(0) == n - 1  # untouched by construction: compression only fires on find()
    hops_before = d.find_hops
    d.find(0)
    assert d.find_hops - hops_before == n - 1  # the one expensive call really does cost n-1
    assert d.depth(0) <= 1  # ...and the path is now (nearly) flat
    hops_before = d.find_hops
    d.find(0)
    assert d.find_hops - hops_before <= 1  # the second call is now cheap


def test_compression_only_amortizes_over_many_queries_not_a_fixed_small_budget():
    """A fixed query budget much smaller than n leaves most of a long chain
    un-compressed; a budget that scales with n lets every node's path get
    paid off once. Same code, same adversarial chain -- only the query
    count differs."""
    n = 20_000
    rng = random.Random(0)
    fixed = built(chain_union_order(n), n, path_compression=True, union_by_rank=False)
    for _ in range(500):  # small, fixed budget
        fixed.find(rng.randrange(n))
    avg_fixed = fixed.find_hops / 500

    rng2 = random.Random(0)
    scaled = built(chain_union_order(n), n, path_compression=True, union_by_rank=False)
    for _ in range(5 * n):  # budget that scales with n
        scaled.find(rng2.randrange(n))
    avg_scaled = scaled.find_hops / (5 * n)

    assert avg_fixed > 10 * avg_scaled  # the same optimization, wildly different amortized outcome
    assert avg_scaled < 5  # and the scaled version really does approach a small constant


def test_both_optimizations_stay_near_constant_even_with_a_small_fixed_query_budget():
    """Unlike compression alone, union-by-rank prevents the bad tree from
    forming at all, so there's no warm-up cost regardless of query count."""
    n = 20_000
    rng = random.Random(0)
    d = built(chain_union_order(n), n, path_compression=True, union_by_rank=True)
    baseline = d.find_hops  # exclude construction's own find() calls -- only the query loop below is being measured
    for _ in range(500):
        d.find(rng.randrange(n))
    assert (d.find_hops - baseline) / 500 < 3


def test_random_union_order_is_not_adversarial_even_without_any_optimization():
    """The pathology in the tests above needs a SPECIFIC adversarial order;
    a random one already behaves reasonably even with neither optimization
    -- the point is that an attacker-controlled order is the actual risk,
    not that naive union-find is unconditionally bad."""
    n = 5000
    d = built(random_union_order(n, random.Random(7)), n, path_compression=False, union_by_rank=False)
    assert d.max_depth() < 10 * math.log2(n)


# --- differential testing against a trivially-correct reference ------------------------------


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("path_compression,union_by_rank", [(False, False), (True, False), (False, True), (True, True)])
def test_matches_naive_reference_under_random_operations(seed, path_compression, union_by_rank):
    n = 60
    rng = random.Random(seed)
    fast = DisjointSet(range(n), path_compression=path_compression, union_by_rank=union_by_rank)
    slow = NaiveGroups(range(n))
    for _ in range(300):
        a, b = rng.randrange(n), rng.randrange(n)
        if rng.random() < 0.7:
            assert fast.union(a, b) == slow.union(a, b), f"union({a},{b}) disagreed at step with seed {seed}"
        else:
            assert fast.connected(a, b) == slow.connected(a, b), f"connected({a},{b}) disagreed"
        assert fast.num_components == slow.num_components


# --- Kruskal's MST -----------------------------------------------------------------------------


def test_kruskal_matches_a_hand_computed_mst():
    # classic textbook graph (CLRS-style), known optimum weight 37
    edges = [
        Edge(0, 1, 4), Edge(0, 2, 1), Edge(1, 2, 2), Edge(1, 3, 5),
        Edge(2, 3, 8), Edge(2, 4, 10), Edge(3, 4, 2), Edge(3, 5, 6), Edge(4, 5, 3),
    ]
    mst, weight, components = kruskal_mst(6, edges)
    assert weight == brute_force_mst_weight(6, edges) == 13  # cross-checked against the exhaustive oracle, not memory
    assert components == 1
    assert len(mst) == 5
    d = DisjointSet(range(6))
    assert all(d.union(e.u, e.v) for e in mst)  # a valid spanning tree: no edge in it closes a cycle
    assert d.num_components == 1


def test_kruskal_matches_brute_force_on_tiny_random_graphs():
    rng = random.Random(1)
    for _ in range(60):
        n = rng.randint(2, 6)
        pairs = [(u, v) for u in range(n) for v in range(u + 1, n)]
        rng.shuffle(pairs)
        edges = [Edge(u, v, rng.randint(1, 20)) for u, v in pairs[: rng.randint(0, len(pairs))]]
        _, weight, components = kruskal_mst(n, edges)
        brute = brute_force_mst_weight(n, edges)
        if components == 1:
            assert brute is not None and weight == pytest.approx(brute)
        else:
            assert brute is None  # not enough edges to span -- brute force agrees it's impossible


def test_kruskal_matches_prim_on_larger_random_graphs_including_disconnected():
    rng = random.Random(2)
    mismatches = 0
    for _ in range(150):
        n = rng.randint(2, 25)
        pairs = [(u, v) for u in range(n) for v in range(u + 1, n)]
        rng.shuffle(pairs)
        density = rng.choice([0.05, 0.2, 0.5, 0.8])
        edges = [Edge(u, v, rng.randint(1, 100)) for u, v in pairs[: int(len(pairs) * density)]]
        _, kw, kc = kruskal_mst(n, edges)
        pw, pc = mst_forest_weight_via_prim(n, edges)
        assert kc == pc
        if kw != pw:
            mismatches += 1
    assert mismatches == 0


def test_kruskal_handles_disconnected_graphs_as_a_forest_not_an_error():
    edges = [Edge(0, 1, 1), Edge(2, 3, 2)]  # two separate components, no edge between them
    mst, weight, components = kruskal_mst(5, edges)  # vertex 4 is fully isolated
    assert components == 3
    assert weight == 3
    assert len(mst) == 2  # n-1 would be 4; a forest has (n - components) edges
    assert len(mst) == 5 - components


def test_kruskal_handles_duplicate_parallel_edges_and_zero_weights():
    edges = [Edge(0, 1, 5), Edge(0, 1, 1), Edge(1, 2, 0), Edge(0, 2, 100)]
    mst, weight, components = kruskal_mst(3, edges)
    assert weight == 1  # cheaper parallel edge (0,1,1) chosen over (0,1,5)
    assert components == 1
    assert len(mst) == 2


def test_kruskal_on_a_graph_with_no_edges():
    mst, weight, components = kruskal_mst(4, [])
    assert mst == [] and weight == 0 and components == 4


def test_prim_reference_matches_brute_force_on_small_connected_graphs():
    """Cross-checks the oracle used against Kruskal, against an even more
    trustworthy (if slower) oracle, so a shared bug in both wouldn't hide."""
    rng = random.Random(3)
    for _ in range(40):
        n = rng.randint(2, 6)
        pairs = [(u, v) for u in range(n) for v in range(u + 1, n)]
        edges = [Edge(u, v, rng.randint(1, 20)) for u, v in pairs]  # complete graph: always connected
        pw, reached = prim_mst_weight(n, edges)
        assert reached == n
        assert pw == pytest.approx(brute_force_mst_weight(n, edges))


def test_brute_force_reports_none_when_the_graph_cannot_possibly_span():
    assert brute_force_mst_weight(4, [Edge(0, 1, 1), Edge(1, 2, 1)]) is None  # only 2 edges, need >= 3
