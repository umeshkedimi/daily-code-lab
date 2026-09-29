# Problem

**Category:** `dsa`

**Note:** second of two small DSA problems for 2026-09-29, each in its own commit — an explicit exception to the repo's "one problem per day" rule, logged here rather than silently breaking it.

## Statement

Implement a **trie (prefix tree)**: `insert`, exact `contains`, `starts_with` (does any stored word have this prefix), `words_with_prefix` (autocomplete), `count_with_prefix`, and `delete` — with deletion actually pruning dead branches, not just clearing a flag.

**Requirements:**
- Query cost should depend on the *query's length*, not on how many words are stored — the opposite trade-off from scanning a word list.
- A word that is a prefix of another stored word (`"car"` / `"carpet"`) must be handled correctly in both directions: inserting, looking up, and deleting either one independently.
- Deleting a word must not resurrect it as a false "still present" prefix match, and must not delete a branch another stored word still needs.
- `words_with_prefix` needs a `limit` that stops the search early, not one that builds everything and truncates.

## Constraints

- No use of a real dictionary/wordlist library or a suffix-array-style shortcut — the tree itself must do the matching.
- The two axes that affect cost — *how many words are stored* and *how long is the query* — must be measured separately, since conflating them would hide which one the trie actually helps with.
- Every claim (sortedness of results, count-matches-length-of-list, delete leaves the rest of the tree correct) must be checked against an independent reference, not eyeballed.

## Approach

1. **One node per character position on a path, shared wherever words share a prefix.** A node has children keyed by the next character and an `is_word` flag — a node being reachable does not mean it's a stored word (`"ca"` can be reachable inside `"car"` without `"ca"` itself ever having been inserted). This is the entire mechanism: lookup and prefix-check are the same walk, differing only in whether the final node's `is_word` flag is checked.
2. **An incremental `word_count` per node**, updated on every insert/delete along the path, so "how many words share this prefix" and autocomplete's "is this branch worth walking" don't need a subtree scan at query time — a small amount of bookkeeping on the rare operation (insert/delete) paid to make the common operation (query) cheaper.
3. **`words_with_prefix` returns results in sorted order for free**, by visiting a node's children in sorted key order during the DFS — no separate sort step, and `limit` stops the recursion itself rather than building the full result and slicing it.
4. **Deletion in two passes**: clear the target's `is_word` flag and walk the path decrementing `word_count`, then prune in a *second*, reverse (leaf-to-root) pass — a node can only be safely removed once whatever's below it on that same word's path has already been removed, so root-to-leaf pruning in one pass is the wrong order and was caught, not assumed correct, while building this (see notes.md).
5. **Two axes measured separately, deliberately.** Fixing word length while varying the stored-word count isolates "does cost grow with how much is stored" (it shouldn't); fixing the stored-word count while varying query length isolates "does cost grow with the query" (it should, linearly) — reporting only one of the two would tell an incomplete, easily-misread story about what a trie actually buys.
