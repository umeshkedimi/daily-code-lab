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
    raise NotImplementedError


def reconstruct_path(parent: Parent, target: Node) -> Optional[List[Node]]:
    """Walk `parent` back from `target` to the source; None if unreachable."""
    raise NotImplementedError


def shortest_path(
    graph: Graph, source: Node, target: Node
) -> Optional[Tuple[float, List[Node]]]:
    """(cost, path) from source to target, or None if unreachable."""
    raise NotImplementedError


def bellman_ford(graph: Graph, source: Node) -> Tuple[Dist, Parent]:
    """Reference implementation. Allows negative edges.

    Raises NegativeCycleError if a negative cycle is reachable from source.
    """
    raise NotImplementedError
