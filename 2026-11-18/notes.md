## Implementation

- `_take_prefix` is the one piece of logic shared by `fixed_proportion_allocate` and `priority_ordered_allocate`: scan a pool's own list front-to-back, keep an item only if it fits in whatever sub-budget remains, **stop** (don't skip) at the first one that doesn't. Both strategies differ only in how they compute that sub-budget before calling it -- a fixed, pre-divided share for one, whatever's left of the running total for the other -- never in how they fill a single pool.
- `greedy_marginal_allocate` is structurally different, not just parametrically: it flattens every pool into one ranked list and **skips** (not stops at) any item that doesn't fit, so it keeps spending leftover budget on the next-best candidate instead of giving up the moment one candidate is too expensive. This is exactly the behavior `test_greedy_marginal_skips_a_non_fitting_item_instead_of_stopping` exists to pin down -- and the one place this implementation diverged from a plausible-looking alternative that still passed every scenario test (see Key Learnings).
- The global sort key `(-score, pool_idx, item_idx)` makes greedy's tie-breaking fully deterministic: two items with the same score always resolve the same way, by which pool and position they came from, rather than by dict/set iteration order that Python doesn't guarantee across equal keys in a plain sort without an explicit tiebreaker.
- `fixed_proportion_allocate`'s `int(budget * proportions[name])` truncates rather than rounds -- deliberately, since the per-pool sub-budgets aren't required to sum back to exactly `budget` (and in general won't, unless the proportions and budget happen to divide evenly) -- the three-scenario result doesn't depend on that gap, only on whether a sub-budget happens to be large enough to reach a given position.
- Not built: score-per-token weighting for greedy, within-pool re-ranking by score, combining this with 2026-10-24's single-sequence strategies as a fourth policy -- all listed as stretch in `problem.md`.

## Complexity

| Operation | Time | Memory |
|---|---|---|
| `fixed_proportion_allocate` | O(total items across pools) | O(selected items) |
| `priority_ordered_allocate` | O(total items across pools) | O(selected items) |
| `greedy_marginal_allocate` | O(n log n), n = total items across pools | O(n) |
| `assemble` | O(selected items) | O(selected items) |

## Measured Results

- **The headline three-scenario claim holds exactly as designed**: across Scenarios A, B, and C (each placing the one fact differently relative to pool priority, order, and score), each of the three strategies fails to recover it in exactly one scenario and succeeds in the other two -- confirmed by running the real implementation against all three before locking in the test assertions, not assumed from the design.
  - A (fact buried at position 4 of the top-priority, biggest-fixed-share pool): `fixed_proportion_allocate` fails (12-token cap cuts off at position 3); `priority_ordered_allocate` and `greedy_marginal_allocate` both succeed.
  - B (fact first in the lowest-priority pool, scored to beat most decoys): `priority_ordered_allocate` fails (docs + the first history item alone consume 36 of the 40-token budget before scratch is reached); `fixed_proportion_allocate` and `greedy_marginal_allocate` both succeed.
  - C (every pool's decoys scored above the fact, which still sits early in its own pool): `greedy_marginal_allocate` fails (the inflated decoys outrank the fact globally); `fixed_proportion_allocate` and `priority_ordered_allocate` both succeed, because neither looks at `score` at all.
- **Sanity bounds confirmed, not just the headline**: at a generous 100-token budget, all three recover the fact in Scenario A's layout (ruling out the "exactly one failure" pattern being baked into the functions rather than forced by a tight budget); at a 4-token budget (below the fact's own 5-token cost), all three fail in every scenario.
- **16 tests, 3/3 consecutive clean runs.**

## Follow-up Questions

**Why?**
2026-10-24 measured what happens to one growing sequence under a budget. Real agent context assembly is usually several separate, differently-sourced pools competing for the same budget -- and "how much of the budget should pool X get" is a genuinely different decision from "which turns of this one conversation do I keep," with its own distinct failure modes.

**How exactly?**
`fixed_proportion_allocate` and `priority_ordered_allocate` never look at an item's `score` -- they only care about a pool's own front-to-back order and how big a sub-budget that pool is handed. `greedy_marginal_allocate` is the only one that treats the whole collection as one ranked list, which is both its advantage (it can rescue a highly-relevant item buried in a rigidly-capped pool, Scenario A) and its exposure (it can be fooled by a decoy with an inflated score, Scenario C) -- the same mechanism produces both outcomes.

**What happens internally?**
`greedy_marginal_allocate`'s skip-not-stop fill is the detail that makes it a genuine greedy-by-value algorithm rather than "sorted, then truncated": skipping an item that doesn't fit and continuing to the next-ranked one is what lets it use every spare token on a cheaper, lower-ranked candidate instead of giving up at the first expensive one -- demonstrated directly by `test_greedy_marginal_skips_a_non_fitting_item_instead_of_stopping`, independent of any of the three fact-recovery scenarios.

**What if this fails?**
- **A caller assumes `fixed_proportion_allocate`'s unused sub-budget helps another pool**: it doesn't, by construction -- each pool's sub-budget is computed independently from `budget` and `proportions`, with no mechanism to redistribute slack (`test_fixed_proportion_leftover_subbudget_never_reaches_another_pool`).
- **A caller assumes `priority_ordered_allocate` treats every pool fairly if given enough total budget**: only true if that total budget comfortably covers every earlier pool's *entire* content, not merely "a reasonable amount" -- Scenario B's 40-token budget would look generous in isolation but still starves the third pool entirely, because the first two pools alone need 36.
- **A caller assumes `greedy_marginal_allocate`'s score is a reliable relevance signal**: Scenario C is the concrete case where it isn't -- the function has no way to distinguish a correctly-scored fact from an incorrectly-scored decoy, because both distinctions come from the same single number.

**What trade-offs did you consider?**
- **A shared `_take_prefix` helper for the two order-based strategies (chosen) vs. inlining the fill logic in each**: both strategies' only real difference is how the sub-budget is computed, not how a pool is filled once it has one -- sharing the fill logic means that difference stays the only place the two functions can diverge, instead of two independently-written loops that could silently drift.
- **Index-based selection tracking in `greedy_marginal_allocate` (chosen) vs. filtering the original pool list by value equality on `Candidate`**: an early version filtered by `candidate in selected_list`, which works only because no two candidates in the test scenarios happen to share both `text` and `score` -- switching to tracking selected positions by index removes that fragile, accidental correctness and makes the function correct for pools with duplicate-valued candidates too, not just the ones this exercise happens to construct.

**How do you evaluate it?**
- **16 tests, 3/3 consecutive clean runs.**
- **Mutation check, 3 targeted mutations, each applied and reverted in isolation with `PYTHONDONTWRITEBYTECODE=1` set for every invocation:**
  - `greedy_marginal_allocate` changed from skip (`continue`) to stop (`break`) on a non-fitting item: 0/16 failed on first attempt -- the three fact-recovery scenarios didn't exercise this distinction at all, which is why a dedicated test (`test_greedy_marginal_skips_a_non_fitting_item_instead_of_stopping`) was added afterward specifically to isolate it; re-run with that test present: 1/16 failed, exactly the new one.
  - `priority_ordered_allocate` stops tracking `remaining` (every pool gets the full `budget` instead of whatever's left): 1/16 failed (`test_scenario_b_priority_ordered_fails_scratch_starved_by_order` -- inverted from fail-as-expected to pass-when-it-shouldn't, caught because that test asserts `None`, not `"ZX9Q7"`, so the mutation flips the assertion's direction).
  - `fixed_proportion_allocate`'s `int()` truncation changed to `round()`: 0/16 failed -- a genuine zero-impact mutation for these specific budget/proportion values (`int(40*0.3)` and `round(40*0.3)` both equal 12), not investigated further, consistent with 2026-10-14/2026-10-15's pattern of zero-failure mutations sometimes needing no further digging and sometimes needing a new test; here, the two functions are only observably different for proportions landing exactly on a `.5` fraction, which none of this exercise's scenarios do, so left as a known, narrow blind spot rather than a bug.

## Key Learnings

- **A mutation surviving all tests on the first attempt is a prompt to ask "what *property* does this behavior have that none of my current scenarios exercise?", not just "add more scenarios like the ones I have."** The three fact-recovery scenarios were all designed to answer "does the fact survive," and none of them happened to depend on *whether* greedy gives up early versus keeps searching after a miss -- because in every one of them, nothing cheap and valuable was sitting just past an expensive item. The fix wasn't a fourth fact-recovery scenario; it was a smaller, more targeted test aimed at the mechanism itself.
- **"Each strategy fails exactly once across three scenarios" was verified by actually running the three strategies against three hand-constructed scenarios in a throwaway script before any test was written** -- the same discipline as the circuit-breaker day's outage test and the timing-attack day's case analysis, applied here to a claim about three competing algorithms' comparative behavior rather than one algorithm's internal correctness. Two of the three scenarios needed a numeric adjustment (a score value tweaked) after the first attempt produced the wrong strategy failing -- caught by the verification script, before it ever became a test.
- Consistent with 2026-10-24: a strategy's tie-break or stopping/skipping rule isn't incidental -- greedy's skip-not-stop fill is the entire reason it can recover a cheap, valuable item sitting just past an expensive one, and that specific mechanism needed its own test because none of the scenario-level tests happened to depend on it.
