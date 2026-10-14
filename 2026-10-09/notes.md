## Implementation

- `dijkstra`: lazy-deletion heap of `(dist, tie, node)`; a `done` set marks finalized nodes, and popped entries for a node already in `done` are discarded. `dist` is only written on a strict improvement (`nd < dist[v]`). Negative weights are rejected up front with `ValueError`.
- `reconstruct_path` / `shortest_path`: walk `parent` back from the target and reverse; `None` when the target never entered `dist`.
- `bellman_ford`: edge list built once, `V - 1` relaxation passes with early exit, then a detection pass. Only nodes reachable from the source ever enter `dist`, so a negative cycle in a different component is correctly ignored (`test_bellman_ford_ignores_unreachable_negative_cycle`).
- Not built: decrease-key (indexed heap), A* (listed as a stretch), all-pairs (Floyd-Warshall / Johnson), bidirectional search.

## Complexity

| Algorithm | Time | Memory |
|---|---|---|
| Dijkstra (binary heap, lazy deletion) | O((V + E) log V) -- at most one push per edge relaxation, so the heap holds at most E entries and log E = O(log V) | O(V + E) |
| Bellman-Ford | O(V * E) worst case (early exit helps on easy inputs) | O(V) plus the edge list |

- **Measured: stale heap entries** (random graphs, 1000 nodes, weights 1-100, seed 0):
  - 5,000 edges: 1,444 pushes, 447 stale pops (about 31% of pushes), 997/1000 nodes reachable.
  - 50,000 edges: 3,316 pushes, 2,316 stale pops (about 70% of pushes), 1000/1000 reachable.
  - Pushes grew only ~2.3x while edges grew 10x, so the heap stays near O(V) in practice on dense graphs, but the share of wasted pops rises with density.
- **Measured: negative edge** `A->B (2)`, `A->C (5)`, `C->B (-4)`: an unchecked greedy run (finalized nodes never revisited) returns `B = 2`; Bellman-Ford returns the true `B = 1` via `A->C->B`. The greedy run finalizes B before it has seen the cheaper route through C.

## Follow-up Questions

**Why?**
Shortest path with non-negative weights underlies routing, dependency/latency analysis, and is the base case for A*. Pairing it with Bellman-Ford makes the *reason* for the non-negativity requirement concrete, not just a rule to memorize.

**How exactly?**
Repeatedly finalize the unfinalized node with the smallest tentative distance and relax its outgoing edges. The heap supplies "smallest tentative distance" in O(log n); lazy deletion replaces decrease-key.

**Which algorithm?**
Dijkstra (1959) with a binary heap; Bellman-Ford as the slower, negative-edge-tolerant reference.

**Which library?**
`heapq` only. The tie-breaker integer in each heap tuple avoids comparing node objects when two distances are equal.

**What happens internally?**
A node can be pushed several times, once per strict improvement, so the heap contains superseded entries. The first pop of a node is always its final distance (the invariant); every later pop of the same node is stale and skipped via `done`.

**What if this fails?**
- **Negative edge:** rejected with `ValueError` before any traversal (`test_dijkstra_rejects_negative_edges`); see the measured greedy-vs-Bellman-Ford example above for what would happen otherwise.
- **Negative cycle:** Dijkstra never sees one (it rejects negative edges); Bellman-Ford raises `NegativeCycleError` only if the cycle is reachable from the source.
- **Zero-weight self-loop / zero-weight edges:** handled because relaxation requires a *strict* improvement; see mutation M3 below for what happens if it doesn't.
- **Unreachable target:** `shortest_path` returns `None`, and `dist` simply omits the node.

**What trade-offs did you consider?**
- **Lazy deletion vs. an indexed heap with decrease-key:** lazy deletion is simpler and uses only `heapq`, at the cost of extra pushes and stale pops (measured above: up to ~70% of pushes on the denser graph). An indexed heap avoids that but needs position tracking.
- **Raise on negative edges vs. silently fall back to Bellman-Ford:** raising keeps the contract honest and the complexity predictable; a silent fallback would hide a 10-100x slowdown.
- **Dist dict with absent keys vs. `inf`:** absent keys mean "unreachable" is `node not in dist`, and `inf` never leaks into arithmetic.

**How do you debug it?**
Differential testing against Bellman-Ford is the tool of record: any disagreement on `dist` localizes the bug to the heap/relaxation logic, since Bellman-Ford has no heap and no finalization invariant to doubt. For path bugs, the path-cost check (sum of edge weights == reported distance) catches a wrong `parent` even when `dist` is right.

**How do you evaluate it?**
- **38 tests pass**, including 25 seeded random graphs cross-checked against Bellman-Ford (a single clean run; not repeated 5x as in earlier days).
- **Mutation check, 5 mutations, each applied in isolation and restored** (`__pycache__` cleared before every run):
  - Drop the stale-entry skip: **38/38 still pass** (see Key Learnings).
  - Drop the negative-weight check: 1/38 failed.
  - Relax with `<=` instead of `<`: **hangs** (killed at a 20s timeout), presumably on the zero-weight self-loop case, where an equal distance keeps re-pushing forever.
  - Drop the `parent` update: 24/38 failed.
  - Bellman-Ford skips the negative-cycle detection pass: 1/38 failed.

## Key Learnings

- **Zero failures again meant a gap, not a harmless mutation.** Removing the stale-entry skip changes no result -- a re-expanded finalized node never produces a strict improvement -- so every test still passes, but the algorithm silently does extra work (up to ~70% of pops were stale in the dense run). The suite checks correctness only; nothing asserts the amount of work done. Closing that would need an instrumented counter or a pop-count assertion, which wasn't added.
- **`<` vs `<=` is a termination bug, not a tie-break detail.** With zero-weight edges, relaxing on equality can re-push the same node forever, so the mutation didn't fail a test -- it hung one. A timeout was needed to even observe it (and macOS has no `timeout` command; a Python `subprocess` timeout was used).
- **Verify odd mutation results by running them alone.** One batched run reported 24 failures for the missing-cycle-check mutation; run in isolation it was 1. The batched number was an artifact and was discarded; only the isolated rerun is recorded above.
