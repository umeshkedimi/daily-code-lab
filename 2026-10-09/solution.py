"""Dijkstra's shortest path (heap + lazy deletion) with path reconstruction,
cross-checked against Bellman-Ford."""

from __future__ import annotations

import heapq
from typing import Dict, Hashable, List, Optional, Tuple

Node = Hashable
Graph = Dict[Node, List[Tuple[Node, float]]]
Dist = Dict[Node, float]
Parent = Dict[Node, Optional[Node]]


class NegativeCycleError(Exception):
    """A negative-weight cycle is reachable from the source."""


def dijkstra(graph: Graph, source: Node) -> Tuple[Dist, Parent]:
    """Shortest distance from `source` to every reachable node.

    Returns (dist, parent); parent[source] is None. Unreachable nodes are
    absent from both. Raises ValueError on a negative edge weight.
    """
    for u, edges in graph.items():
        for v, w in edges:
            if w < 0:
                raise ValueError(f"negative edge weight {w} on {u!r}->{v!r}")

    dist: Dist = {source: 0}
    parent: Parent = {source: None}
    heap: List[Tuple[float, int, Node]] = [(0, 0, source)]
    done = set()
    tie = 1  # tiebreaker so heapq never compares nodes (which may be unorderable)
    while heap:
        d, _, u = heapq.heappop(heap)
        if u in done:  # stale entry: u was already finalized via a shorter path
            continue
        done.add(u)
        for v, w in graph.get(u, ()):
            nd = d + w
            if v not in dist or nd < dist[v]:
                dist[v] = nd
                parent[v] = u
                heapq.heappush(heap, (nd, tie, v))
                tie += 1
    return dist, parent


def reconstruct_path(parent: Parent, target: Node) -> Optional[List[Node]]:
    """Walk `parent` back from `target` to the source; None if unreachable."""
    if target not in parent:
        return None
    path = []
    node: Optional[Node] = target
    while node is not None:
        path.append(node)
        node = parent[node]
    path.reverse()
    return path


def shortest_path(
    graph: Graph, source: Node, target: Node
) -> Optional[Tuple[float, List[Node]]]:
    """(cost, path) from source to target, or None if unreachable."""
    dist, parent = dijkstra(graph, source)
    if target not in dist:
        return None
    return dist[target], reconstruct_path(parent, target)


def bellman_ford(graph: Graph, source: Node) -> Tuple[Dist, Parent]:
    """Reference implementation. Allows negative edges.

    Raises NegativeCycleError if a negative cycle is reachable from source.
    """
    edges = [(u, v, w) for u, adj in graph.items() for v, w in adj]
    nodes = {source} | {u for u, _, _ in edges} | {v for _, v, _ in edges}
    dist: Dist = {source: 0}
    parent: Parent = {source: None}
    for _ in range(len(nodes) - 1):
        changed = False
        for u, v, w in edges:
            if u in dist and (v not in dist or dist[u] + w < dist[v]):
                dist[v] = dist[u] + w
                parent[v] = u
                changed = True
        if not changed:
            return dist, parent
    for u, v, w in edges:  # any further relaxation means a reachable negative cycle
        if u in dist and dist[u] + w < dist[v]:
            raise NegativeCycleError(f"negative cycle reachable via {u!r}->{v!r}")
    return dist, parent
