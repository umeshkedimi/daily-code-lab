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

1. **Validate first**: scan every edge and raise `ValueError` on a negative weight, before any work. Dijkstra's correctness rests on one invariant -- when a node is popped with the smallest tentative distance, no later path can improve it -- and a negative edge is exactly what falsifies that.
2. **Dijkstra with lazy deletion**: `heapq` has no decrease-key, so every improvement just pushes a new `(dist, tie, node)` entry. When an entry is popped for a node already finalized, it is stale and skipped (`if u in done: continue`). The `tie` counter means the heap never falls back to comparing node objects, which may not be orderable.
3. **`parent` map**: updated on every strict improvement; `reconstruct_path` walks it from the target back to the source (`parent[source] = None`) and reverses. Unreachable nodes are simply absent from `dist`/`parent`, so "reachable?" is `target in dist`.
4. **Bellman-Ford as the independent reference**: relax every edge up to `V - 1` times (early exit when a pass changes nothing), then one more pass -- any edge that still relaxes means a negative cycle reachable from the source. It shares no logic with the heap version, so agreement between the two is meaningful.
5. **Cross-check**: 25 seeded random non-negative graphs; Dijkstra and Bellman-Ford must produce identical `dist`, and every reconstructed path must be a real path whose edge weights sum to the reported distance.

## Stretch

- A* on a grid with Manhattan heuristic; measure nodes expanded vs plain Dijkstra.
