## Implementation

- **Hash:** `stable_hash64` = BLAKE2b truncated to 64 bits. Positions live on a ring of `2**bits` (default 64). `hash_fn` and `bits` are injectable, which is what makes tiny hand-checkable rings and forced collisions testable.
- **Ring state:** `(positions, owners, node_count)` — two parallel sorted lists plus the number of distinct nodes — replaced wholesale on every membership change. Entries sort by `(position, node name)`, so the ring depends only on the node *set* (verified: shuffled insertion order gives an identical ring).
- **`get_node`:** takes one snapshot, `bisect_left` for the first position ≥ `hash(key)`, wrapping to index 0 past the end. Convention: a node owns the arc `(prev, pos]`, so a key hashing *exactly* to a virtual node belongs to it.
- **`get_nodes(key, n)`:** walks clockwise from that index collecting distinct *physical* nodes (skipping later vnodes of nodes already chosen); returns fewer than `n` if the ring has fewer nodes; `n <= 0` returns `[]`. The size cap comes from the snapshot's own node count.
- **`add_nodes` / `add_node`:** validates the whole batch first (all-or-nothing), extends the entry list, sorts once, swaps the snapshot in. `add_node` is the one-element case. `weight` multiplies a node's vnode count.
- **`remove_node`:** filters that node's entries out and swaps a new snapshot in.
- **`arcs()` / `ownership()`:** exact integer arc lengths (`position - previous position`, with the wrapped arc for index 0), summing to exactly `2**bits`. Duplicated positions give the later entries zero-length arcs, matching `bisect_left`'s tie behavior.
- **Baselines:** `ModuloSharder` (`hash % N`) and `RendezvousHasher` (`argmax_n hash(key|n)`, ties broken by name).
- **Not built:** bounded-load ("consistent hashing with bounded loads"), zone/rack-aware replica placement, data migration/rebalancing, distributed agreement on membership, jump consistent hash.

## Complexity

- `get_node`: **O(log(N·V))** (one bisect). `get_nodes(key, n)`: O(log(N·V) + steps walked) — normally about `n` steps; worst case O(N·V) if `n` exceeds the node count and the loop must lap the ring.
- `add_nodes` of K nodes onto M existing entries: O((M + K·V) log(M + K·V)) — one sort. `remove_node`: O(M). Both are copy-on-write.
- Space: O(N·V) — two parallel lists, e.g. 100,000 entries for 1,000 nodes × 100 vnodes.

Measured (100 vnodes/node, this machine):

| nodes | ring lookup | rendezvous lookup | build: `add_node` loop | build: `add_nodes` | add 1 node |
|---:|---:|---:|---:|---:|---:|
| 10 | 527 ns | 3,360 ns | 1 ms | 0 ms | 0.1 ms |
| 100 | 615 ns | 31,967 ns | 34 ms | 6 ms | 0.7 ms |
| 1,000 | 776 ns | 314,315 ns | 3,097 ms | 65 ms | 7.4 ms |

Ring lookup barely moves across a 100× increase in nodes (logarithmic); rendezvous grows ~93× (linear). Bulk build is ~48× faster than the loop at 1,000 nodes.

## Follow-up Questions

**Why?**
`hash(key) % N` is perfectly balanced but re-homes almost every key when N changes — measured **90.9%** moved when going from 10 to 11 nodes, exactly the theoretical N/(N+1). On a cache tier that is a simultaneous cold-start of the whole fleet; on a sharded database it is a mass migration. Consistent hashing bounds movement to roughly the new node's fair share.

**How exactly?**
Nodes and keys are hashed onto the same circle. A key is served by the next node clockwise. A new node takes over only the arcs its points land in; a removed node's arcs pass to their clockwise neighbours. Virtual nodes give each node many arcs so the shares average out.

**Which algorithm?**
Consistent hashing (Karger et al.) with virtual nodes and a sorted-array-plus-binary-search ring. Compared against rendezvous (highest-random-weight) hashing and modulo sharding.

**Which library?**
Standard library only: `hashlib` (BLAKE2b), `bisect`. No third-party hashing.

**What happens internally?**
A lookup hashes the key to a 64-bit integer, binary-searches the sorted position array, wraps if past the end, and indexes the parallel owner array. Membership changes rebuild both arrays and publish them as a new immutable tuple; readers that already hold the old tuple keep a consistent (if momentarily stale) view.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- *Node failure:* removal is the failure model. With **one** point per node, removing the busiest node (37% of the keys) sent **100%** of them to a single successor — which then holds well over double its load and may itself fall over (a cascade). With 16 vnodes they spread over 7 of 9 survivors (largest recipient 32%); with 128, over all 9 (largest 20%).
- *Hash collisions:* ~M²/2⁶⁵ chance of any collision among M entries (≈ 3×10⁻¹⁰ at 100,000), and handled deterministically anyway (tie broken by node name; tested by forcing *every* position to collide).
- *Inconsistent membership views:* two clients holding different node sets will disagree about ownership. Consistent hashing gives no agreement on membership — that needs a coordination layer (config service, gossip, epochs). Not addressed here.
- *Hot keys:* one very popular key still lands on one node; the ring balances *keys*, not *traffic per key*.
- *Concurrent membership change:* readers never see partial state (tested with 4 reader threads against continuous add/remove), but they can briefly see the previous ring.

**What trade-offs did you consider?**
- **Vnodes per node** trade balance against memory and rebuild cost. Measured CV of per-node load: 0.94 (1 vnode), 0.29 (10), 0.095 (100), 0.026 (1,000); busiest node at 3.06×, 1.53×, 1.18×, 1.04× the average. Rendezvous, sampled over 100,000 keys, sits at CV 0.011 — essentially the sampling-noise floor (0.0095) — versus 0.102 for the ring at 128 vnodes: **ten times more skewed**, in exchange for O(log) instead of O(N) lookups.
- **Ring vs rendezvous:** rendezvous has no state, better balance, and equally minimal movement (9.0% on add), but its lookup cost grows linearly (314 µs at 1,000 nodes vs 776 ns). It wins for small N; the ring wins as N grows or lookups dominate. Jump consistent hash (O(1)-ish, no state, but nodes must be numbered `0..N-1` and only the last can be removed) is another point in this space — not built or measured here.
- **Copy-on-write snapshots vs a lock:** membership changes cost O(ring) (7.4 ms for one node on a 1,000-node ring) but lookups take no lock. Right when reads vastly outnumber membership changes, which is the normal case.
- **Fewer keys moved is not automatically better:** at 1 vnode, adding a node moved only 0.3% of keys — because the new node received a 0.3% sliver of the ring. Movement has to be read alongside balance.
- **Determinism over convenience:** stable hash, name-based tie-break, no dependence on insertion order.

**How do you debug it?**
- `check_invariants()` (sorted, correct per-node vnode counts, arcs sum to the ring size, node count matches).
- `ownership()` is an exact ground truth to compare against routed-key counts; the two are cross-checked in a test.
- Hand-checkable 8-bit rings with 3 nodes make boundary/wrap/collision behavior inspectable by eye.
- To diagnose "two servers disagree": compare `ring._ring` (or a digest of it) across processes — the cross-process test does exactly this under different `PYTHONHASHSEED`s.

**How do you evaluate it?**
- **35 tests, ~0.6 s, 25/25 consecutive clean runs.** The disruption guarantees are asserted as exact set equalities on 20,000 keys (adding: every moved key lands on the new node; removing: the moved set *equals* the removed node's key set).
- **Theory check:** a node's ring share with V random points is Beta(V, (N−1)V), giving CV = √((N−1)/(N·V+1)). Measured vs theory (mean of 20 rings): 0.937/0.905 (V=1), 0.292/0.299, 0.121/0.134, 0.095/0.095, 0.066/0.067, 0.039/0.042, 0.026/0.030 (V=1000) — within ~13% throughout.
- **Controls:** modulo sharding must move >85% (so the movement metric can discriminate); Python's `hash()` must genuinely differ across the seeds used in the determinism test.
- **Mutation check — 8 of 8 deliberate breaks caught**, each by the test aimed at it: `bisect_right` for `bisect_left`; no wrap-around; insertion-order tie-break; Python `hash()` instead of the stable hash; `get_nodes` sized from writer-side state; `remove_node` leaving vnodes behind; dropping the wrapped arc; ignoring weights.
- **Not evaluated:** real network/cluster behavior, very large rings (>100k nodes), key-popularity skew, weighted-node balance beyond one 1:1:3 case.

## Key Learnings

- **Measure the property, not just the mechanism.** Consistent hashing's whole promise is *which* keys move. Asserting `moved_keys == removed_node's_keys` as a set equality (rather than "about 10% moved") is both a stronger and a simpler test.
- **A metric can flatter a bad configuration.** 1 vnode "moved only 0.3% of keys" on node addition — because the new node got almost nothing. Movement and balance have to be read together.
- **Virtual nodes are about failure behavior as much as balance.** The 100%-to-one-successor result at 1 vnode is the practical argument for them; the CV table is the quantitative one.
- **Measurement found a design bug:** building a ring by repeated `add_node` took 3.1 s at 1,000 nodes (O(N²·V log)); a bulk `add_nodes` that sorts once takes 65 ms.
- **Review found a subtle concurrency bug before any test could:** `get_nodes` originally sized its result from `self._weights`, writer-side state that isn't part of the immutable snapshot, so a reader could pair one version's node count with another's ring. Making the node count part of the snapshot fixes it; a test that empties `_weights` and requires unchanged lookups now guards it, and the mutation check confirms that test fails on the buggy version. (A concurrent stress test alone would likely never have caught it — the window is tiny.)
- **A test caught a plain edge-case bug:** `get_nodes(key, 0)` returned every node, because the "enough?" check ran only after appending. Fixed with an early return; negative `n` now covered too.
- **Mutation testing is a cheap way to audit a test suite:** breaking the code eight ways and watching each get caught says more about the tests than the pass count does.
- **Python's built-in `hash()` on strings is per-process salted** — fine for dicts, silently wrong for anything that must agree across processes or machines.
