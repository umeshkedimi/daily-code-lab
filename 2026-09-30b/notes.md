## Implementation

- `_ordered_nodes(graph)`: collects every node (dict keys and every list target) in first-seen order, via a dict used purely for its insertion-order-preserving `setdefault`. Deliberately not `set(...)` — set iteration order for strings/arbitrary objects depends on hash randomization, which would make test output non-deterministic across runs for no reason.
- `topological_sort_kahn`: standard in-degree BFS. `in_degree[v]` is decremented as each of `v`'s prerequisites is processed; `v` enters the queue the instant its in-degree reaches exactly 0, which can only happen once, so no node is ever queued twice by this path. If the graph has a cycle, every node on that cycle keeps a permanent unprocessed prerequisite (another node on the same cycle), so it never reaches in-degree 0 and never enters the order — caught by comparing final `len(order)` to the total node count.
- `topological_sort_dfs`: three-color DFS (`WHITE`/`GRAY`/`BLACK`), building the order in **postorder** (a node is appended only after the `for v in graph.get(u, [])` loop fully returns), then reversing once at the end. `GRAY` means "on the current root-to-here recursion path" — a back-edge into a gray node is a genuine cycle. `BLACK` means "fully explored via some other path already" — an edge into a black node is a reconverging diamond, not a cycle, and is silently skipped (not re-visited, not flagged).
- `is_valid_topological_order`: the one function every other test leans on. Two checks: `order` is an exact permutation of the graph's nodes (same set, same length — catches both missing and duplicated nodes), and for every edge `u -> v`, `position[u] < position[v]`.
- `has_cycle_naive`: deliberately not a third coloring-based DFS (which would just be the same technique copy-pasted, likely to share the same blind spot if there were one). Instead: for every node, BFS strictly forward along edges and check whether that starting node is ever re-reached — a cycle exists iff some node can walk back to itself.
- Not built: cycle *extraction* (returning the actual cycle's node sequence for diagnostics, rather than just detecting one exists), weighted edges (irrelevant to topological order), or an iterative (non-recursive) DFS — the recursive version is simpler and the test graphs here are small; a genuinely deep graph would need an explicit stack to avoid Python's recursion limit (see Follow-up Questions).

## Complexity

- **Kahn's:** O(V + E) — each node enters/leaves the queue once, and each edge is inspected exactly once (when decrementing its target's in-degree).
- **DFS:** O(V + E) — same bound, standard DFS traversal cost; each node visited once, each edge inspected once.
- **`has_cycle_naive`:** O(V · (V + E)) — a full forward BFS from every node.
- **Naive "scan for any node with no remaining prerequisite, remove it, repeat" reference** (used only for the timing comparison, not for correctness — see Implementation): O(V) work to find a removable node, times O(V) removals, each also re-scanning up to O(E) remaining edges to check "does anything in `remaining` still point to me" — O(V²) plus O(V·E) in this implementation, which is asymptotically worse than Kahn's/DFS's O(V+E) and was structured differently on purpose so it wouldn't just be "Kahn's algorithm with different variable names."
- Measured, sparse random DAGs (`edge_prob=0.01`) at V = 100 / 500 / 2,000: Kahn's/DFS stayed in single-digit milliseconds throughout (0.035ms → 1.974ms, roughly tracking the growth in V+E), while the naive reference went 1.5ms → 346ms → 17.8s — a 44x gap at V=100 widening to over 9,000x at V=2,000, consistent with O(V+E) against roughly O(V²).

## Follow-up Questions

**Why?**
Topological sort is the backbone of build systems (compile B before A if A depends on B), package/dependency resolution, course-prerequisite scheduling, and task orchestration DAGs (Airflow, Makefiles) — anywhere "these things have a must-happen-before relationship, give me a valid execution order" comes up. Cycle detection is the other half of the same problem: a cyclic dependency is exactly the case where no valid build/install/schedule order exists.

**How exactly?**
Kahn's peels off nodes from the "no remaining prerequisite" end, working forward. DFS finishes nodes from the "nothing left to explore below me" end, working backward, then reverses. They're mirror-image ways of reaching the same guarantee: every node appears only after everything it structurally depends on already has.

**Which algorithm?**
Kahn's algorithm (in-degree BFS, 1962) and DFS-based topological sort (the classic CLRS formulation, using finish-time ordering) — two independently well-known algorithms for the same problem, chosen specifically so their agreement is informative (see Key Learnings).

**Which library?**
Standard library only — `collections.deque` for Kahn's queue (O(1) `popleft`, same reasoning as the deque used in 2026-09-30's sliding-window-maximum problem).

**What happens internally?**
Kahn's in-degree array is effectively counting, for each node, how many of its prerequisites are still outstanding; decrementing it to zero is a direct encoding of "every dependency is now satisfied." DFS's gray/black coloring is tracking recursion-stack membership without needing to actually inspect the call stack — gray nodes are exactly the nodes between the current call and the root of this DFS tree.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **A node only ever appears as an edge target, never as a dict key** (`{"a": ["b"]}`, "b" has no entry): handled by `_ordered_nodes` collecting targets too, and `graph.get(u, [])` throughout instead of `graph[u]`, since a target-only node has no outgoing-edge list in the dict at all. Verified (`test_node_appearing_only_as_a_target_is_still_included`).
- **A cycle reachable only after an acyclic prefix** (`a->b->c->d->b`): DFS's gray-coloring catches this regardless of depth, since gray tracks "on the current path," not "visited at all" — verified directly with a cycle that only closes after three hops (`test_cycle_hidden_behind_an_acyclic_prefix`).
- **A reconverging diamond misidentified as a cycle:** the actual failure mode this risks is treating "visited before" (which a diamond triggers, legitimately, via its second incoming path) the same as "currently on the recursion stack" (which only a real cycle triggers) — the three-color scheme exists specifically to keep those two cases distinct. Verified (`test_dag_with_a_convergent_but_still_acyclic_shape_is_not_a_cycle`).
- **Self-loop** (`{"a": ["a"]}`): a length-1 cycle: `a` is its own prerequisite, so Kahn's in-degree for `a` never reaches 0, and DFS immediately re-enters `a` while it's still gray. Verified.

**What trade-offs did you consider?**
- **Two independent algorithms cross-checked against each other (chosen) vs one algorithm trusted on its own:** a single implementation, however well-tested, risks a bug that's consistent with itself — every test it passes is a test it wrote the rules for. Two structurally different algorithms (in-degree BFS vs DFS-postorder) agreeing across many randomized cases is much stronger evidence, because the ways each one *could* go wrong are different (Kahn's: in-degree bookkeeping; DFS: recursion-stack coloring) — a bug that fools both at once is a much narrower target.
- **Recursive DFS (chosen) vs an explicit-stack iterative DFS:** recursive is far more readable for the postorder-append-on-the-way-back-up logic, at the cost of Python's default recursion limit (~1000) capping how deep a dependency chain can go before `RecursionError`. Fine for this exercise's graph sizes; a production version handling arbitrarily deep chains would need an iterative rewrite with an explicit stack carrying "have I already pushed this node's children" state.
- **Raising `CycleError` (chosen) vs returning `None`/an empty list on a cycle:** a cycle is a caller error in the sense that "give me an execution order" is simply an unanswerable request for this input — silently returning something (empty list, partial order) invites a caller to treat "no valid order exists" as "here is a valid order, coincidentally short," which is a much worse failure than a loud, typed exception.

**How do you debug it?**
- `has_cycle_naive` first: if it disagrees with either real implementation about whether a cycle exists, the bug is almost certainly in whichever real implementation disagrees with it, not in the naive check itself (it's simple enough to read directly and trust).
- For a Kahn's ordering bug specifically, print `in_degree` right after initialization — every node with in-degree 0 that *isn't* in the initial queue, or every nonzero in-degree that doesn't correspond to a real incoming edge, points straight at the bug.
- For a DFS bug, `color` at the point of failure distinguishes three genuinely different problems: a node stuck `WHITE` after the top-level loop finished means it was never reached at all (a graph-traversal bug, not a topological-sort bug); a node still `GRAY` after `visit()` returns for it means the color wasn't reset to `BLACK` on the way out; nodes correctly `BLACK` but in the wrong relative order means the bug is in the append-then-reverse step, not the traversal itself.

**How do you evaluate it?**
- **82 tests, 5/5 consecutive clean runs** (`pytest.mark.parametrize` with fixed seeds — reruns confirm environment stability; the randomized coverage comes from 30 + 20 + 20 distinct seeds across three property-based tests).
- **Mutation check, 6 targeted mutations, each applied and reverted in isolation with `__pycache__`/`.pytest_cache` cleared before every run** (a lesson carried over from 2026-09-30's first problem today, where a stale bytecode cache across chained edits in one shell session produced one false "all pass" result — isolating each mutation's run avoids that entirely):
  - Kahn's cycle-check disabled (`len(order) != len(nodes)` forced to never fire): 30/82 failed.
  - Kahn's in-degree never incremented (in-degree stays 0 for everything): 56/82 failed.
  - Kahn's `popleft()` → `pop()` (BFS order → stack order among ties): **0 failures — genuinely behavior-preserving, not a bug.** Any removal order that only ever removes nodes whose in-degree has reached 0 produces a valid topological order; which of several equally-valid orders comes out is not a correctness property this problem specifies, so the validator (correctly) accepts it.
  - DFS cycle-check disabled (gray back-edge check forced to never fire): 30/82 failed.
  - DFS `order.reverse()` removed (postorder returned directly, un-reversed): 35/82 failed.
  - DFS re-visiting already-`BLACK` nodes (treating "not gray" as "not yet visited"): 27/82 failed — nodes get appended more than once, breaking the permutation check.

## Key Learnings

- **Cross-checking two independently-implemented algorithms catches a class of bug that testing either one alone can't:** a mistake that's "consistent" — e.g. both algorithms failing to treat a reconverging diamond correctly in the same way — would pass every test written only against one implementation's own idea of correct, but shows up immediately as a disagreement once a structurally different second implementation (or the naive reachability check) is added to the mix.
- **The `popleft()` → `pop()` mutation on Kahn's queue was the most useful "non-bug" of the session:** it's a genuine reminder that "produces *a* valid topological order" and "produces *the same* order as before" are different specifications, and a test suite asserting the latter when the problem only requires the former would be testing an implementation detail, not the actual contract.
