import random

import pytest

from solution import (
    NegativeCycleError,
    bellman_ford,
    dijkstra,
    reconstruct_path,
    shortest_path,
)

CLASSIC = {
    "A": [("B", 7), ("C", 9), ("F", 14)],
    "B": [("C", 10), ("D", 15)],
    "C": [("D", 11), ("F", 2)],
    "D": [("E", 6)],
    "F": [("E", 9)],
}


def path_cost(graph, path):
    total = 0
    for u, v in zip(path, path[1:]):
        total += min(w for x, w in graph.get(u, []) if x == v)
    return total


def random_graph(rng, n, m, lo, hi):
    g = {i: [] for i in range(n)}
    for _ in range(m):
        g[rng.randrange(n)].append((rng.randrange(n), rng.randint(lo, hi)))
    return g


def test_classic_graph_distances():
    dist, _ = dijkstra(CLASSIC, "A")
    assert dist == {"A": 0, "B": 7, "C": 9, "F": 11, "D": 20, "E": 20}


def test_shortest_path_reconstruction():
    assert shortest_path(CLASSIC, "A", "E") == (20, ["A", "C", "F", "E"])


def test_source_equals_target():
    assert shortest_path(CLASSIC, "A", "A") == (0, ["A"])


def test_unreachable_target_returns_none():
    assert shortest_path(CLASSIC, "E", "A") is None


def test_unreachable_nodes_absent_from_dist():
    dist, parent = dijkstra({"A": [("B", 1)], "X": [("Y", 1)]}, "A")
    assert set(dist) == {"A", "B"} and set(parent) == {"A", "B"}


def test_target_only_nodes_need_no_key():
    dist, _ = dijkstra({"A": [("B", 3)]}, "A")
    assert dist["B"] == 3


def test_zero_weight_edges_and_self_loops():
    g = {"A": [("A", 0), ("B", 0)], "B": [("C", 0)]}
    dist, _ = dijkstra(g, "A")
    assert dist == {"A": 0, "B": 0, "C": 0}


def test_parallel_edges_take_the_cheapest():
    assert shortest_path({"A": [("B", 5), ("B", 2)]}, "A", "B") == (2, ["A", "B"])


def test_reconstruct_path_unreachable_is_none():
    _, parent = dijkstra(CLASSIC, "A")
    assert reconstruct_path(parent, "nope") is None


def test_dijkstra_rejects_negative_edges():
    with pytest.raises(ValueError):
        dijkstra({"A": [("B", -1)]}, "A")


def test_bellman_ford_handles_negative_edges():
    # Greedy Dijkstra would finalize B at 2 before seeing the A->C->B route.
    g = {"A": [("B", 2), ("C", 5)], "C": [("B", -4)]}
    dist, _ = bellman_ford(g, "A")
    assert dist["B"] == 1


def test_bellman_ford_detects_negative_cycle():
    g = {"A": [("B", 1)], "B": [("C", -3)], "C": [("B", 1)]}
    with pytest.raises(NegativeCycleError):
        bellman_ford(g, "A")


def test_bellman_ford_ignores_unreachable_negative_cycle():
    g = {"A": [("B", 1)], "X": [("Y", -1)], "Y": [("X", -1)]}
    dist, _ = bellman_ford(g, "A")
    assert dist == {"A": 0, "B": 1}


@pytest.mark.parametrize("seed", range(25))
def test_dijkstra_matches_bellman_ford_on_random_graphs(seed):
    rng = random.Random(seed)
    g = random_graph(rng, n=rng.randint(2, 30), m=rng.randint(0, 120), lo=0, hi=20)
    d_dist, d_parent = dijkstra(g, 0)
    b_dist, _ = bellman_ford(g, 0)
    assert d_dist == b_dist
    for node in d_dist:
        path = reconstruct_path(d_parent, node)
        assert path[0] == 0 and path[-1] == node
        assert path_cost(g, path) == d_dist[node]
