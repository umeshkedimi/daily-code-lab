# Problem

**Category:** `dsa`

## Statement

Implement **Disjoint Set Union (Union-Find)** with **path compression** and **union by rank**, and use it to build **Kruskal's Minimum Spanning Tree** algorithm. Then **measure** the two optimizations' claims rather than just implement them: DSU's entire reason to exist is an asymptotic guarantee (amortized ~O(α(n)) per operation), and a claim like that should be demonstrated on purpose-built adversarial input, not just asserted in a comment.

**Requirements:**
- `find`, `union`, `connected`, `component_size` over arbitrary hashable elements.
- Both optimizations independently switchable, so each one's actual contribution can be isolated.
- `union()` doubles as Kruskal's cycle check: it returns whether the two elements were already connected.
- Kruskal's algorithm, correct on connected graphs, disconnected graphs (a minimum spanning *forest*), and graphs with parallel edges or zero-weight edges.

## Constraints

- The claimed depth bound for union-by-rank (`floor(log2 n)`) must be demonstrated as *tight* — reached exactly, not just satisfied loosely — which requires a specific adversarial construction order, not an arbitrary one.
- Path compression's benefit must be shown to be genuinely *amortized*: it does not bound the cost of any single `find()` call, only the total cost over enough calls to pay off the structure it inherited.
- Kruskal's correctness must be checked against an independent computation of the answer, not against itself.

## Approach

1. **Two independent flags, two independent guarantees.** `union_by_rank` bounds worst-case tree depth *unconditionally*, the moment it's enabled — by construction, a lower-rank tree is always attached under a higher-rank one, so an `O(n)` tree is structurally impossible. `path_compression` bounds *total accumulated work across many calls* — a single call into a long-untouched chain still costs the full chain length; what compression buys is that every node on that path never costs more than O(1) again. These are different mechanisms fixing different failure modes, not two attempts at the same fix — conflating them was the risk in designing this exercise's own measurements (see notes.md).

2. **Adversarial input has to match the claim being tested.** A chain-building order (each new element attached to the current tip) is the textbook worst case for *no* optimization — but it turns out to be nearly the *best* case for union-by-rank, since the incoming singleton's rank is always lower than the accumulated tree's, so rank correctly keeps attaching it as a shallow leaf. Reaching the `floor(log2 n)` bound *exactly* requires a different order — repeatedly merging pairs of *equal*-rank trees (as in a binomial-heap merge) — which is precisely the one case where rank's tie-breaking never fires, so the bound and the code's actual behavior can be measured against each other honestly.

3. **Amortization needs a query budget that scales with the structure**, or the "amortized" claim measures something else by accident. Running a small, fixed number of queries against a huge pathological structure mostly measures how much of that structure is still untouched — not the amortized cost per query. The exercise measures both a fixed budget and a budget that scales with `n`, on the identical structure, to show the difference directly rather than picking whichever number looks better.

4. **Kruskal's correctness is checked three ways, at three trust levels**: exhaustive brute force (every `(n-1)`-subset of edges) on tiny graphs, where "trustworthy" matters more than "fast"; an independently implemented O(V²) Prim's algorithm — cross-checked against the same brute-force oracle first, so a shared bug in both wouldn't hide — on larger random graphs including disconnected ones; and a spanning-tree validity check (no cycle, exactly `n − components` edges) on the chosen edges themselves.
