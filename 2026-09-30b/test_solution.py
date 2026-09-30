import random

import pytest

from solution import (
    CycleError,
    has_cycle_naive,
    is_valid_topological_order,
    topological_sort_dfs,
    topological_sort_kahn,
)

IMPLS = [topological_sort_kahn, topological_sort_dfs]


# --- basic behaviour -------------------------------------------------------------


def test_empty_graph_returns_empty_order():
    assert topological_sort_kahn({}) == []
    assert topological_sort_dfs({}) == []


def test_single_node_no_edges():
    assert topological_sort_kahn({"a": []}) == ["a"]
    assert topological_sort_dfs({"a": []}) == ["a"]


def test_linear_chain():
    graph = {"a": ["b"], "b": ["c"], "c": []}
    for impl in IMPLS:
        order = impl(graph)
        assert order == ["a", "b", "c"]  # only one valid order exists


def test_diamond_dependency():
    # a -> b, a -> c, b -> d, c -> d : two valid orders, a first and d last either way.
    graph = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
    for impl in IMPLS:
        order = impl(graph)
        assert order[0] == "a"
        assert order[-1] == "d"
        assert is_valid_topological_order(graph, order)


def test_isolated_node_with_no_edges_is_still_included():
    graph = {"a": ["b"], "b": [], "isolated": []}
    for impl in IMPLS:
        order = impl(graph)
        assert set(order) == {"a", "b", "isolated"}
        assert is_valid_topological_order(graph, order)


def test_node_appearing_only_as_a_target_is_still_included():
    # "b" is never a key, only ever a target -- must still appear in the order.
    graph = {"a": ["b"]}
    for impl in IMPLS:
        order = impl(graph)
        assert set(order) == {"a", "b"}
        assert order.index("a") < order.index("b")


def test_disconnected_components_both_appear():
    graph = {"a": ["b"], "x": ["y"]}
    for impl in IMPLS:
        order = impl(graph)
        assert set(order) == {"a", "b", "x", "y"}
        assert is_valid_topological_order(graph, order)


# --- cycle detection ---------------------------------------------------------------


def test_self_loop_is_a_cycle():
    graph = {"a": ["a"]}
    for impl in IMPLS:
        with pytest.raises(CycleError):
            impl(graph)
    assert has_cycle_naive(graph) is True


def test_two_node_cycle():
    graph = {"a": ["b"], "b": ["a"]}
    for impl in IMPLS:
        with pytest.raises(CycleError):
            impl(graph)
    assert has_cycle_naive(graph) is True


def test_cycle_hidden_behind_an_acyclic_prefix():
    # a -> b -> c -> d -> b : the cycle is b -> c -> d -> b, reached only after
    # descending past "a", so a detector that stops too early would miss it.
    graph = {"a": ["b"], "b": ["c"], "c": ["d"], "d": ["b"]}
    for impl in IMPLS:
        with pytest.raises(CycleError):
            impl(graph)
    assert has_cycle_naive(graph) is True


def test_acyclic_graph_is_not_flagged_as_a_cycle():
    graph = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}
    assert has_cycle_naive(graph) is False
    for impl in IMPLS:
        impl(graph)  # must not raise


def test_dag_with_a_convergent_but_still_acyclic_shape_is_not_a_cycle():
    # Multiple paths reconverging on the same node (b) is NOT a cycle --
    # only a repeated node on one directed walk is. A detector that flags
    # "node visited more than once" instead of "node on the current
    # recursion stack" would wrongly fail this.
    graph = {"a": ["b"], "c": ["b"], "b": ["d"]}
    assert has_cycle_naive(graph) is False
    for impl in IMPLS:
        order = impl(graph)
        assert is_valid_topological_order(graph, order)


# --- differential / property-based testing -----------------------------------------


def _random_dag(rng: random.Random, n: int, edge_prob: float):
    """A random DAG: shuffle n node labels, then only allow edges from an
    earlier position in the shuffled order to a later one -- this makes the
    graph acyclic by construction, independent of how the algorithms under
    test decide ordering."""
    labels = list(range(n))
    rng.shuffle(labels)
    graph = {label: [] for label in labels}
    for i in range(n):
        for j in range(i + 1, n):
            if rng.random() < edge_prob:
                graph[labels[i]].append(labels[j])
    return graph


@pytest.mark.parametrize("seed", range(30))
def test_random_dags_produce_valid_orders_under_both_algorithms(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 40)
    edge_prob = rng.choice([0.05, 0.2, 0.5])
    graph = _random_dag(rng, n, edge_prob)

    assert has_cycle_naive(graph) is False
    for impl in IMPLS:
        order = impl(graph)
        assert is_valid_topological_order(graph, order)
        assert len(order) == n


@pytest.mark.parametrize("seed", range(20))
def test_random_dag_plus_a_direct_2cycle_is_always_a_cycle(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 30)
    graph = _random_dag(rng, n, edge_prob=0.3)
    nodes = list(graph.keys())

    # Pick two distinct nodes and wire a direct 2-cycle between them -- a
    # guaranteed cycle regardless of whatever forward edges the random DAG
    # happened to generate.
    u, v = rng.sample(nodes, 2)
    if v not in graph[u]:
        graph[u].append(v)
    if u not in graph[v]:
        graph[v].append(u)

    assert has_cycle_naive(graph) is True
    for impl in IMPLS:
        with pytest.raises(CycleError):
            impl(graph)


# --- cross-checking the two algorithms against each other --------------------------


@pytest.mark.parametrize("seed", range(20))
def test_kahn_and_dfs_agree_on_cycle_presence(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 25)
    graph = _random_dag(rng, n, edge_prob=rng.choice([0.1, 0.4]))
    # Randomly corrupt into a cycle about half the time.
    if rng.random() < 0.5 and n >= 2:
        nodes = list(graph.keys())
        u, v = rng.sample(nodes, 2)
        graph.setdefault(u, []).append(v)
        graph.setdefault(v, []).append(u)

    kahn_raises = dfs_raises = False
    try:
        topological_sort_kahn(graph)
    except CycleError:
        kahn_raises = True
    try:
        topological_sort_dfs(graph)
    except CycleError:
        dfs_raises = True

    assert kahn_raises == dfs_raises == has_cycle_naive(graph)
