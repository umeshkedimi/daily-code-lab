## Implementation

- `answer` is a single linear scan with `.search()` per turn, returning on the first match — it has no notion of "the most recent" or "the correct" fact if several turns match; it just finds whatever's first in the context it's handed. That's deliberate: it's what makes the decoy-crowding test (below) demonstrate a *wrong, confident* answer rather than just a missing one.
- `truncate_oldest` walks `turns` in reverse, greedily keeping whole turns while they fit, and stops at the first one that doesn't — a classic recency window, O(n).
- `summarize_oldest` reuses that exact same reverse-greedy loop to build the "recent" window and tracks `cutoff` (the index where the recent window starts) to split `turns` into `old` / `recent`. `_old_fact_summary` extracts fact-pattern clauses from `old`, most-recently-dropped first (`reversed(old_turns)`), and greedily keeps them while they fit the *leftover* budget — stopping at the first clause that doesn't fit, never skipping ahead to a smaller, older one. That stop-on-first-miss rule is exactly what makes the crowding failure mode possible: a newer decoy fact can occupy the only slot and block an older, real fact from ever being considered.
- `importance_weighted` ranks turns by `(not fact_match, -index)` — fact-matching turns sort first (`False < True`), ties broken by recency — then greedily tries each in that priority order using `continue` (not `break`) on a miss, so a later, cheaper, lower-priority turn can still claim room a higher-priority one couldn't use.
- Not built: a real tokenizer, a second fact pattern (listed as stretch in `problem.md`), hybrid importance+summary fallback.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `truncate_oldest` | O(n) | O(n) kept |
| `summarize_oldest` | O(n) | O(n) |
| `importance_weighted` | O(n log n) (the priority sort) | O(n) |
| `answer` | O(n) turns × O(len(turn)) regex scan | O(1) |

## Follow-up Questions

**Why fail fast instead of just scaling up the budget?** You can't always — context budgets are fixed by the model's window and by cost, and a long-running conversation grows without bound. The question isn't "how do we avoid truncating," it's "which truncation loses the least."

**How exactly does `summarize_oldest` decide what survives?** Most-recently-dropped-first, greedy, stop-on-first-miss. It's optimized to extend the *recency* cliff a little further back, not to be "fair" about which fact matters most — which is exactly why it can be crowded out by a newer, irrelevant fact-shaped turn (measured below).

**What happens internally when importance_weighted runs out of budget mid-list?** It doesn't stop — the `continue` means every remaining lower-priority turn still gets a chance, so the final kept set can include a cheap old filler turn that happened to fit, while a slightly more expensive recent one right before it in priority order didn't. It's a knapsack-style greedy, not a two-pointer window.

**What if this fails?** Measured directly: with `before=0, depth=10`, `budget=4×filler_cost`, `truncate_oldest` keeps the 4 most recent filler turns and drops the fact entirely; `importance_weighted` keeps the fact plus the 3 most recent filler turns, displacing exactly one filler turn (`Turn 7`) that the naive window would have kept. That's the whole trade-off in one number: importance-weighting buys depth-independence by spending budget the naive window would have spent on recency.

**What trade-offs did you consider?** `summarize_oldest`'s "most-recently-dropped-first" ordering vs. "oldest-first": most-recently-dropped-first is the realistic choice (a real summarizer processing a growing backlog would compress recent overflow before ancient history), but it means an *old, important* fact loses a tie-break race against a *newer, equally fact-shaped but irrelevant* turn for the same leftover budget — confirmed directly, see the crowding measurement below.

**How do you evaluate it?** Three separate falsifiable measurements, not one anecdote:
- **`truncate_oldest`'s cliff is a true step function.** Swept depth 0–7 at a fixed budget (3 filler turns' worth): correct at depth 0, 1, 2; wrong at every depth ≥ 3, with no exceptions in either direction.
- **`summarize_oldest` has no cliff with respect to depth alone** — swept depth 2 through 20 at a budget with exactly one fact-clause's worth of leftover room: it stayed correct at every depth tested, while `truncate_oldest` failed from depth 3 onward at the same budget. (This is a more precise claim than `problem.md`'s approach section implies — depth alone never breaks it here, because there's only one fact-shaped turn competing for the summary's leftover slot. It's density of *competing* fact-shaped content that breaks it, not distance, which is exactly what the next measurement isolates.)
- **`summarize_oldest`'s actual failure mode is crowding, not distance.** With two decoy "my account id is X" turns placed *after* the real fact (so they're more recently dropped), at the same tight leftover-budget (room for exactly one clause), the summary keeps the most recent decoy and the real, older fact is never even considered — `answer` returns the decoy's wrong value, not `None`. A wrong, confident answer, not a missing one.
- **Adversarial phrasing is a strict ceiling.** With retention budget inflated past the sum of every turn's cost (nothing truncated under any of the three strategies — verified via `kept == history`), a fact phrased as `"FYI: 48213 is the account id you should use."` is still unanswerable by all three, because `answer`'s pattern never matches that phrasing regardless of what's retained.

## Key Learnings

- The hand-traced test constants (11-token filler turns, 5-token fact clauses, derived from the exact fixed strings rather than assumed) held up exactly on the first implementation run — all 11 tests passed without a single constant needing adjustment. Worth noting as the payoff of tracing by hand against fixed, deterministic strings before writing the implementation, rather than against the previous day's (circuit breaker) looser assumptions, which needed two post-hoc fixes.
- Writing `summarize_oldest`'s approach note before measuring made a claim that turned out to be imprecise: "depth eventually crowds out the summary" isn't true in isolation — depth alone never caused a failure in the sweep (2 through 20). The real variable is the *number of competing fact-shaped turns*, which the separate decoy test isolates cleanly. A sweep over the wrong variable would have reported "no cliff found" and missed the actual failure mode entirely — the measurement has to vary the thing the mechanism actually depends on, not just the thing that's easiest to vary.
- Reinforced from the circuit-breaker day: a strategy's internal tie-break rule (here, "most-recently-dropped-first, stop on first miss") isn't an implementation detail — it's the entire reason a specific failure mode exists. Changing it to "biggest clauses first" or "oldest-first" would produce a different, equally real crowding story.
