"""Topological sort of a DAG, via Kahn's algorithm (BFS on in-degree) and
DFS-with-postorder-reversal -- two structurally independent algorithms for
the same problem, cross-checked against each other so that a bug shared by
both (rather than an accident of one implementation) is what would slip
through.

A graph is represented as an adjacency dict `{u: [v, w, ...]}` meaning
"u must come before v", "u must come before w", etc. (u is a prerequisite
of each of its listed targets). Nodes that appear only as a target, or
that have no edges at all, are still included in the output -- a
topological order covers every node, not just the ones with outgoing edges.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, Generic, Hashable, List, TypeVar

T = TypeVar("T", bound=Hashable)


class CycleError(ValueError):
    """Raised when the graph is not a DAG -- no topological order exists."""


def _ordered_nodes(graph: Dict[T, List[T]]) -> List[T]:
    """All nodes (keys and targets), in first-seen order -- deterministic
    regardless of hash randomization, since it follows the caller's own
    dict/list insertion order rather than set iteration order."""
    seen: Dict[T, None] = {}
    for u, targets in graph.items():
        seen.setdefault(u, None)
        for v in targets:
            seen.setdefault(v, None)
    return list(seen.keys())


def topological_sort_kahn(graph: Dict[T, List[T]]) -> List[T]:
    """BFS-based: repeatedly peel off nodes with no remaining unprocessed
    prerequisite. A node can only be peeled once every prerequisite that
    points to it has itself already been peeled, which is exactly what
    `in_degree[v] == 0` tracks."""
    nodes = _ordered_nodes(graph)
    in_degree = {n: 0 for n in nodes}
    for u in nodes:
        for v in graph.get(u, []):
            in_degree[v] += 1

    queue = deque(n for n in nodes if in_degree[n] == 0)
    order: List[T] = []
    while queue:
        u = queue.popleft()
        order.append(u)
        for v in graph.get(u, []):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    if len(order) != len(nodes):
        stuck = [n for n in nodes if n not in order]
        raise CycleError(f"graph has a cycle -- stuck nodes (never reached in-degree 0): {stuck!r}")
    return order


def topological_sort_dfs(graph: Dict[T, List[T]]) -> List[T]:
    """DFS-based: a node is appended to the order only after every node it
    points to has been fully explored, then the whole sequence is reversed
    -- "finishes last, therefore has no remaining dependents, therefore
    sorts first". Cycle detection uses three-way coloring: a WHITE node
    hasn't been visited; GRAY means it's an ancestor currently on the
    recursion stack (a back-edge into a GRAY node is a cycle); BLACK means
    fully finished and safe to revisit without re-exploring."""
    nodes = _ordered_nodes(graph)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    order: List[T] = []

    def visit(u: T) -> None:
        color[u] = GRAY
        for v in graph.get(u, []):
            if color[v] == WHITE:
                visit(v)
            elif color[v] == GRAY:
                raise CycleError(f"graph has a cycle -- back-edge into in-progress node {v!r}")
            # BLACK: already fully explored via some other path; nothing to do.
        color[u] = BLACK
        order.append(u)

    for n in nodes:
        if color[n] == WHITE:
            visit(n)

    order.reverse()
    return order


def is_valid_topological_order(graph: Dict[T, List[T]], order: List[T]) -> bool:
    """Independent checker: `order` is valid iff it's a permutation of every
    node in `graph` and every edge u->v has u appearing before v."""
    nodes = _ordered_nodes(graph)
    if len(order) != len(nodes) or set(order) != set(nodes):
        return False
    position = {n: i for i, n in enumerate(order)}
    for u in nodes:
        for v in graph.get(u, []):
            if position[u] >= position[v]:
                return False
    return True


def has_cycle_naive(graph: Dict[T, List[T]]) -> bool:
    """O(V*(V+E)) reference, deliberately structured differently from the
    recursion-stack coloring above: for every node, BFS forward along edges
    and check whether that same node is reachable again. A cycle exists iff
    at least one node can reach itself via one or more edges."""
    nodes = _ordered_nodes(graph)
    for start in nodes:
        visited = set()
        queue = deque(graph.get(start, []))
        while queue:
            u = queue.popleft()
            if u == start:
                return True
            if u in visited:
                continue
            visited.add(u)
            queue.extend(graph.get(u, []))
    return False
