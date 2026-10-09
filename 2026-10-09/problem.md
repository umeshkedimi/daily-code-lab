# Problem

**Category:** `dsa`

## Statement

Implement Dijkstra's single-source shortest path on a weighted directed graph, with path reconstruction, and cross-check it against Bellman-Ford on random graphs.

Graph representation: adjacency list, `Dict[Node, List[Tuple[Node, float]]]` (`graph[u]` = list of `(v, weight)`). A node that appears only as an edge target may be absent from the dict's keys.

**Requirements:**
- `dijkstra(graph, source) -> (dist, parent)` using a min-heap with **lazy deletion** (skip stale heap entries instead of decrease-key).
- `shortest_path(graph, source, target) -> (cost, path) | None` rebuilt from `parent`; `None` if unreachable.
- `dijkstra` raises `ValueError` on any negative edge weight (Dijkstra's greedy invariant breaks there).
- `bellman_ford(graph, source) -> (dist, parent)` as the slower, independently-built reference; raises `NegativeCycleError` if a negative cycle is reachable from `source`.
- Unreachable nodes are absent from `dist` (not `inf`).
- Handle: source == target, zero-weight edges, parallel edges, self-loops, disconnected graphs.

## Constraints

- Standard library only (`heapq`).
- Differential test: Dijkstra vs Bellman-Ford on random non-negative graphs must agree on every distance, and every reconstructed path must actually sum to the reported cost.
- Show (not assert) where Dijkstra's assumption breaks: a small graph with a negative edge where the greedy answer is wrong and Bellman-Ford's is right.

## Approach

1. TODO: fill in once implemented.

## Stretch

- A* on a grid with Manhattan heuristic; measure nodes expanded vs plain Dijkstra.
