## Implementation

- `_Node`: a `children` dict, an `is_word` flag, and an incrementally-maintained `word_count` (this node's subtree word total, including itself if `is_word`).
- `insert(word)`: walks/creates nodes character by character, collecting the path; if the final node was already `is_word`, returns `False` (no-op); otherwise sets it, increments `_size`, and increments `word_count` on every node in the path — that last step is what makes `count_with_prefix` O(depth) instead of a subtree walk.
- `_find_node(prefix)` is the one shared walk both `__contains__` and `starts_with` build on: `contains` additionally checks `is_word` on the result; `starts_with` only checks that the node exists at all.
- `words_with_prefix` does a DFS from the prefix's node, visiting `sorted(n.children)` at each level — sorted output falls out of the traversal order, no separate sort needed. `limit` is checked inside the walk (with an explicit `limit <= 0 → []` short-circuit) so the recursion itself stops early, rather than collecting everything and slicing.
- `delete(word)`: pass 1 clears `is_word` and walks the path decrementing `word_count`, recording every node visited (`stack`). Pass 2 walks `stack` **in reverse** (leaf toward root), deleting a child from its parent only if that child is now both not-a-word and childless — reverse order matters: a node's own deletion eligibility depends on whether its child was *already* removed in this same pass, which is only true if the pass runs leaf-first.
- `NaiveWordSet`: a de-duplicating list with linear-scan `contains`/`starts_with`/`words_with_prefix` — trivially correct, the reference every other test is checked against.
- Not built: case-insensitive matching, Unicode normalization, a compressed/radix-trie variant (merging chains of single-child nodes), fuzzy/edit-distance search, serialization.

## Complexity

- `insert`, `delete`, `contains`, `starts_with`: O(L) — L is the query/word length, independent of how many words are stored.
- `count_with_prefix`: O(L) (one walk to the prefix's node, then an O(1) field read).
- `words_with_prefix`: O(L + result size) — the walk to the prefix node, plus the DFS over exactly the matching subtree (bounded further by `limit` when given).
- Measured, word count held fixed at each size while probing with words of a fixed length: `contains` on 5,000 / 50,000 / 200,000 stored words took 0.0073s / 0.0104s / 0.0143s for 20,000 lookups (barely growing across a 40× increase in stored words), against a plain list's 0.38s / 3.94s / 9.07s (52×, 379×, 635× slower, tracking the list's linear growth almost exactly).
- Measured, word count held fixed while probing with increasing query length: `starts_with` cost went 0.34 → 2.94 → 28.2 → 54.7 µs/call as query length went 10 → 100 → 1,000 → 2,000 — roughly linear in length, as expected for an O(L) walk.

## Follow-up Questions

**Why?**
Autocomplete, spell-check candidate generation, IP routing table longest-prefix-match, and dictionary/word-game validation all reduce to "find things that share a prefix with this query, fast, regardless of how big the dictionary is." A sorted list plus binary search can find the *range* of matches in O(log n + L), which is competitive — the trie's real edge is when prefixes are being extended interactively (typing one more character should cost O(1) more, not another O(log n) search from scratch) or when the alphabet/structure naturally shares long common prefixes (routing tables, genomic sequences).

**How exactly?**
Each stored word is a path from the root, one node per character, with `is_word` marking which paths correspond to actually-inserted words rather than just prefixes passed through on the way to a longer word. Lookup, prefix-check, and insert are all the same character-by-character walk; only what happens at the end (or when a character is missing) differs.

**Which algorithm?**
The trie / prefix tree (Fredkin, 1960), with the autocomplete/prefix-count extensions (per-node subtree counts) layered on as a standard augmentation, not a different structure.

**Which library?**
Standard library only (`dataclasses` for the node). No trie/dictionary package — the entire exercise is the tree logic itself.

**What happens internally?**
Each step of a walk is one dictionary lookup (`node.children.get(ch)`) — Python dicts are hash tables, so each step is O(1) expected regardless of how many *other* words share or don't share that prefix; the O(L) total cost is simply L such steps chained together. There's no comparison against other stored words at all, which is the structural reason cost doesn't grow with how many words are stored — the naive list approach's cost comes entirely from comparing the query against every stored word.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **A stored word doubling as a prefix of another** (`"car"`/`"carpet"`): handled by construction — `is_word` lives on the node, not on "having no children," so a word can be both a complete stored word and an internal node on the way to a longer one. Verified both for lookup and for independent deletion of either.
- **Deleting a word that's a prefix of another:** must not delete the shared node — verified directly (`test_delete_of_a_word_that_is_a_prefix_of_another_keeps_the_other`).
- **Deleting a word that shares only its early characters with a sibling:** must not touch the sibling's branch — verified (`test_delete_of_a_word_whose_prefix_is_shared_does_not_touch_the_sibling`).
- **`limit=0`:** must return nothing, not "at least one result because the check happens after collecting it" — a real bug this exercise's own tests caught (see Key Learnings), now covered explicitly.
- **A very long, mostly-unshared word:** costs proportionally more to insert/query/delete (O(L)), and its deletion prunes the *entire* unshared tail back to wherever it starts sharing structure with another word — not just the leaf.

**What trade-offs did you consider?**
- **Per-node dict of children (chosen) vs a fixed-size array indexed by character** (e.g. 26 slots for lowercase a–z): a dict handles an arbitrary alphabet (Unicode, mixed case, digits) with no wasted space for sparse branches, at the cost of dict overhead per node versus a flat array's direct indexing. For a small, known alphabet an array trie is faster and denser; this implementation chose generality.
- **Eager per-node `word_count` (chosen) vs computing counts on demand by walking the subtree:** the eager version pays a small O(path length) cost on every insert/delete to make every `count_with_prefix`/autocomplete-ranking query O(1) after the initial walk — the right trade when queries vastly outnumber mutations, which is the common case for a dictionary/autocomplete structure.
- **Sorted traversal order for `words_with_prefix` (chosen) vs collecting unsorted and sorting the result:** visiting `sorted(n.children)` costs a sort of the (usually tiny) branching factor at every node visited, rather than one sort of the whole (possibly much larger) result set at the end — cheaper when a `limit` cuts the walk short, since a limited walk never has to look at the children it didn't visit anyway.
- **Two-pass, explicit-stack deletion (chosen) vs recursive deletion that prunes on the way back up the call stack:** a recursive approach gets leaf-to-root ordering for free from the call stack unwinding; the explicit two-pass version makes that ordering visible and independently testable (`test_delete_prunes_now_dead_branches` reads `_root.children` directly), which is why it was chosen for an exercise about getting this specific ordering right.

**How do you debug it?**
- For a "word is missing" bug, check `starts_with` on progressively shorter prefixes of the word — the first prefix that returns `False` names exactly which character step broke, without needing to inspect internal node structure at all.
- For a deletion bug, `_find_node(word)` after deleting lets you inspect directly whether the node still exists, whether `is_word` was cleared, and whether it still has children (i.e., whether it *should* have survived) — three independent facts a single boolean "delete() returned True" can't distinguish.
- `count_with_prefix("")` should always equal `len(trie)` — a cheap global consistency check after a batch of inserts/deletes.
- The differential test against `NaiveWordSet` is the actual debugging tool of record here: when it fails, the randomized seed reproduces the exact operation sequence that diverged, rather than needing to guess at a minimal repro by hand.

**How do you evaluate it?**
- **34 tests, 15/15 consecutive clean runs.** Hand-checked cases for prefix-of-a-prefix, shared branches, deletion pruning (including that pruning doesn't over-prune a shared branch), and empty/edge inputs.
- **15 seeds × 150 randomized insert/delete/contains/prefix/count operations**, each checked against `NaiveWordSet` step by step (not just at the end), plus a running `len(trie) == len(naive)` check after every operation.
- **Realistic-scale check:** 5,000 words, sparse lookups, and a prefix query cross-checked against the naive reference directly (not just internally consistent).
- **The two complexity claims measured separately** (word-count axis and query-length axis), specifically so neither one is asserted from the other.
- **Mutation check — 10 of 10 deliberate breaks caught**, including the two genuinely-encountered bugs (limit-check ordering, prune direction) reintroduced and confirmed caught, plus ignoring `is_word` on read, unsorted traversal, and forgetting to maintain `word_count` on insert or delete.
- **Not evaluated:** Unicode/case-folding behavior; a compressed (radix) trie's space savings on data with long unshared suffixes; concurrent access; memory overhead per node versus a flat sorted-list-plus-binary-search alternative (asymptotically the wrong comparison at small scale — dict-per-node overhead is real and unmeasured here).

## Key Learnings

- **The tests caught two real bugs, not hypothetical ones, in code I'd already run manually and thought correct.** `words_with_prefix(prefix, limit=0)` returned one result instead of zero, because the length check ran *after* appending the current match rather than before — the first hit always got in regardless of the limit. And `delete()`'s pruning pass walked root-to-leaf, checking each parent's child for "now dead" status before that child's *own* children had been pruned — so a long unshared branch's leaf node would correctly become empty and childless, but its parent (checked earlier in the same forward pass) had already been evaluated while the leaf still existed, and never got revisited. Both were caught by tests written before I'd double-checked the implementation by hand, which is exactly the value of writing the test first and trusting its answer over an assumption.
- **Pruning order is not a minor implementation detail — it's the entire correctness argument for "delete really removes the branch."** A node's eligibility for removal depends on facts about its children that are only settled *after* those children have themselves been resolved; any single-pass pruning walk has to run in the direction where dependencies are resolved before the things that depend on them are checked, i.e. leaf-to-root. Getting the pass direction backwards doesn't crash or error — it just silently leaves dead nodes in the tree, which only shows up if something specifically inspects the tree structure afterward (as `test_delete_prunes_now_dead_branches` does) rather than only checking the externally-visible behavior (`word not in trie`), which was already correct even with the ordering bug.
- **Measuring two different axes separately, on purpose, is what made the "why" of the data structure legible.** A single benchmark mixing "more words stored" with "longer queries" would have produced numbers that were still technically true but impossible to attribute to either cause — deliberately holding one axis fixed while varying the other is what turned "the trie is faster" into "the trie's cost tracks query length, not corpus size," which is the actual, specific, useful claim.
