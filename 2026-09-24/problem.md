# Problem

**Category:** `dsa` / `system-design`

## Statement

Implement a **consistent hashing ring with virtual nodes** for sharding keys across a changing set of nodes (cache servers, DB shards, partitions), and **measure it** against the obvious alternatives.

**Requirements:**
- `get_node(key)` → the node that owns the key.
- `add_node` / `remove_node`, with optional per-node **weights**.
- `get_nodes(key, n)` → an ordered list of `n` *distinct* nodes for replication.
- When membership changes, only the keys that must move should move.
- Load should be reasonably even, and its evenness should be tunable and quantifiable.

## Constraints

- **Minimal, directional disruption.** Adding a node may only move keys *onto* that node; removing a node may only move *that node's* keys. No key may ever shuffle between two nodes that both remain.
- **Determinism across machines.** Every process must build the same ring from the same node set — independent of insertion order, of Python's per-process `str` hash salt, and of hash collisions.
- **Lookup must be sub-linear** in the number of nodes.
- **Lock-free reads.** Lookups must be safe while membership is changing, without readers locking.
- Claims must be **measured** (movement %, load imbalance, lookup cost), and compared to theory where a closed form exists.

## Approach

1. **The ring.** Hash space is a circle of 2⁶⁴ positions. Each node is hashed to many points (*virtual nodes*, `"{node}#{i}"`). A key belongs to the first virtual node at or clockwise of `hash(key)` — found with `bisect` over a sorted array in O(log(N·V)). Ownership of the arc `(prev, pos]` is the convention, so a key landing exactly on a virtual node belongs to it, and lookups past the last position wrap to the first.
2. **Why virtual nodes.** One point per node gives wildly uneven arcs (the busiest node averaged 3× its fair share in the measurements) and, worse, dumps a removed node's entire load on a single successor. Many points per node average the arc lengths out and scatter a removed node's keys across all survivors. `V` is the balance-vs-memory dial; weights simply scale a node's `V`.
3. **Stable hashing.** BLAKE2b (64-bit), never Python's `hash()`, whose `str` output is salted per process — two servers would silently build different rings.
4. **Determinism under collisions.** Entries sort by `(position, node name)`, so the ring is a pure function of the node *set*; two virtual nodes colliding on a position resolve identically no matter the insertion order, and removing one never removes the other.
5. **Lock-free reads via immutable snapshots.** Ring state is a `(positions, owners, node_count)` tuple replaced atomically on each change; a reader takes one reference and sees a consistent ring. Membership changes cost O(ring) (copy-on-write) — acceptable because they are rare.
6. **Replication lists.** Walk clockwise from the key's position collecting distinct *physical* nodes, skipping further virtual nodes of nodes already chosen.
7. **Bulk membership.** `add_nodes` sorts once. (Added after measuring that building by repeated `add_node` was O(N²·V log) — ~3 s at 1,000 nodes.)
8. **Baselines to earn the comparison:** `hash(key) % N` (perfect balance, catastrophic movement) and **rendezvous (HRW) hashing** (no ring state, excellent balance, minimal movement, O(N) lookup).

**Verification plan:** hand-checkable 8-bit rings for exact boundaries/wrap/collisions; the disruption properties as *exact set equalities* on 20,000 real keys; balance compared to the closed-form Beta-distribution prediction; analytic ownership cross-checked against routed keys; cross-process determinism under different `PYTHONHASHSEED`s (with a control proving the seeds really change `hash()`); a concurrency test; and a **mutation check** — break the implementation in eight specific ways and confirm the tests catch each.
