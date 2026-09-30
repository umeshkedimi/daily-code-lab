# Problem

**Category:** `dsa`

**Note:** second of two small DSA problems for 2026-09-30, each in its own commit — same exception to the "one problem per day" rule used on 2026-09-17, 2026-09-29, and earlier today (2026-09-30).

## Statement

Given a directed graph as an adjacency map `{u: [v, w, ...]}` (an edge `u -> v` means "u must come before v"), produce a **topological order**: a linear ordering of every node such that for every edge `u -> v`, `u` appears before `v`. If the graph has a cycle, no such ordering exists — detect that and raise, rather than silently returning a wrong or partial order.

**Requirements:**
- Every node must appear in the output exactly once, including nodes that only ever appear as a target (never a dict key) and nodes with no edges at all.
- Cycle detection must work regardless of where the cycle sits in the graph (immediately, or only reachable after descending through an acyclic prefix).
- A node reachable via two different paths that reconverge (a "diamond": `a->b`, `c->b`, `b->d`) is **not** a cycle and must not be flagged as one.
- Implement it two structurally different ways and cross-check them against each other, not just against hand-picked examples.

## Constraints

- Standard library only (`collections.deque`).
- Multiple valid topological orders can exist for the same graph (any two nodes with no path between them can appear in either relative order) — correctness is checked against a general **validator** (every edge respects position in the order, output is an exact permutation of the input's nodes), not by asserting one specific output sequence, except where the graph's structure forces a unique order.
- Checked with randomized property-based testing (random DAGs by construction, plus random DAGs corrupted with a deliberate cycle), not only fixed hand-written graphs.

## Approach

1. **Kahn's algorithm (BFS on in-degree):** compute each node's in-degree, seed a queue with every node that starts at in-degree 0 (no unprocessed prerequisite), then repeatedly pop a node, append it to the order, and decrement the in-degree of everything it points to — pushing a target onto the queue the moment its in-degree hits 0. If the final order is shorter than the full node set, some nodes never reached in-degree 0, which is only possible if they're stuck in a cycle.
2. **DFS with postorder reversal**, built independently of (1): visit nodes depth-first; a node is appended to the order only once everything it points to has been fully explored (i.e., on the way back up the recursion, not the way down). Reversing that postorder sequence gives a valid topological order — a node that finishes last has nothing left depending on it, so it sorts first. Cycle detection uses three-way coloring (white/gray/black): a back-edge into a node still on the current recursion stack (gray) is a cycle; an edge into an already-fully-finished node (black) is fine — it's a reconverging diamond, not a cycle.
3. **A general validator, not a fixed-output assertion**, since Kahn's and DFS can legitimately produce different (both valid) orders when the graph doesn't fully constrain the order: `is_valid_topological_order` checks the output is an exact permutation of the input's nodes and that every edge's source precedes its target.
4. **An independent, differently-structured cycle check (`has_cycle_naive`)** — brute-force, per-node forward reachability (BFS from each node, checking whether it can reach itself) — as a third, structurally unrelated cross-check against both the in-degree-based and coloring-based cycle detection, specifically so a bug shared by both real implementations doesn't look confirmed just because they agree with each other.
