# Problem

**Category:** `genai-agentic`

Picked from [GENAI_AGENTIC_ROADMAP.md](../GENAI_AGENTIC_ROADMAP.md), Stage 1 — "Token allocation strategies across competing context needs."

## Statement

An agent assembling context for one LLM call often has several *competing* sources, not one growing conversation: retrieved documents, conversation history, and scratchpad notes, each with its own candidate items and a per-item relevance score — all fighting over one shared token budget. 2026-10-24 measured what happens to a single, growing sequence under a budget (truncation/summarization/importance-weighting). This exercise measures a different question: **given several separate pools instead of one sequence, how should a fixed budget be split across them — and does the splitting policy actually change whether the one fact that matters survives, or is it all equivalent once padding is accounted for?**

**Requirements:**

- `Candidate(text: str, score: float)` — a frozen dataclass: one candidate item from a pool, with a relevance/importance score assigned by whatever upstream system produced it (a retriever, a recency heuristic, an agent's own self-rating).
- `count_tokens(text: str) -> int` — `len(text.split())`, exactly as 2026-10-24.
- `answer(context: List[str]) -> Optional[str]` — searches each line for `the deployment secret is (\w+)` (case-insensitive) and returns the first match's captured group, or `None`.
- `fixed_proportion_allocate(pools: Dict[str, List[Candidate]], budget: int, proportions: Dict[str, float]) -> Dict[str, List[Candidate]]` — splits `budget` into a fixed per-pool sub-budget (`int(budget * proportions[name])`), then fills each pool independently from the front of its own list up to that sub-budget. A sub-budget's leftover never moves to another pool, even if that pool has nothing left to spend it on.
- `priority_ordered_allocate(pools: Dict[str, List[Candidate]], budget: int, order: List[str]) -> Dict[str, List[Candidate]]` — walks `order`; each pool in turn gets however much of the *remaining* budget it can use (filled from the front of its own list), and whatever's left after that carries to the next pool. A pool late in `order` can receive nothing at all.
- `greedy_marginal_allocate(pools: Dict[str, List[Candidate]], budget: int) -> Dict[str, List[Candidate]]` — flattens every pool's items into one list, ranks the whole thing by `score` descending (ties broken by pool position then item position, for determinism), and fills from the top, **skipping** (not stopping at) any item that doesn't fit in whatever budget remains, continuing to the next-best candidate. The one strategy here that can leave some of a pool's later, lower-scored items unfilled while still using every spare token elsewhere.
- `assemble(allocation: Dict[str, List[Candidate]], pool_order: List[str]) -> List[str]` — flattens an allocation into one list of strings, pool by pool in `pool_order`, each pool's own selected items in their original relative order.

## Constraints

- Standard library only.
- Within a single pool, all three strategies (and `fixed_proportion_allocate`/`priority_ordered_allocate` specifically) fill strictly from the front of that pool's own list — they never reorder a pool's items by score themselves. A pool's list is assumed to already be in whatever order its own upstream system considers best (a retriever's ranking, chronological order, whatever) — modeling the realistic case where the allocator's job is deciding *how much* of each pool to keep, not re-ranking within it.
- `greedy_marginal_allocate` is the only strategy that looks at `score` across pools, and it has no way to tell a genuinely important item from a merely high-scored decoy — its score is exactly as trustworthy as whatever assigned it.

## Approach

1. **The measured, three-scenario claim:** the same fixed budget (40 tokens) and the same decoy text (`"filler filler filler filler filler filler"`, 6 tokens) and fact text (`"the deployment secret is ZX9Q7"`, 5 tokens) are reused across three scenarios that each place the fact differently. In every scenario, exactly one strategy fails to recover the fact and the other two succeed — and across the three scenarios, each of the three strategies is the one that fails exactly once. No strategy dominates the other two; each has a distinct, structural failure mode, not a shared one at different thresholds.
   - **Scenario A** — the fact sits deep (4th of 5 items) inside the pool that's first in priority order and gets the largest fixed share. `fixed_proportion_allocate` fails: its rigid 30%-of-40=12-token share for that pool is cut off before reaching position 4, even though the pool is "first" by every other measure. `priority_ordered_allocate` and `greedy_marginal_allocate` both succeed — the first because being first in `order` means no rigid cap applies, the second because the fact's score is high enough to be reached well before the budget runs out.
   - **Scenario B** — the fact sits first in the *lowest*-priority pool, scored high enough that `greedy_marginal_allocate` reaches it, but `priority_ordered_allocate`'s fixed pool order drains the budget on the first two pools before the last pool gets anything. `fixed_proportion_allocate` and `greedy_marginal_allocate` succeed; `priority_ordered_allocate` fails — starvation by rigid order, independent of score or position-within-pool.
   - **Scenario C** — every pool's decoys are given inflated scores higher than the fact's own score, which sits early in its own pool. `greedy_marginal_allocate` fails: the inflated decoys outrank the fact globally and consume the budget first. `fixed_proportion_allocate` and `priority_ordered_allocate` succeed, because neither of them even looks at `score` — a pool's own front-to-back order is all that matters to them, and the fact is near the front of its pool either way.
2. **A generous-budget sanity check:** with the budget raised enough to cover every pool's full content, all three strategies recover the fact in Scenario A's layout — ruling out the possibility that the three-way "each fails exactly once" result is some property of the functions rather than of the tight budget forcing a real trade-off.
3. **A starved-budget sanity check:** with the budget cut to less than the fact's own token cost, all three strategies fail in every scenario — the ceiling isn't "pick the right strategy," it's "have enough budget for the fact to fit anywhere at all," regardless of policy.

## Stretch

- Letting `greedy_marginal_allocate` weight by score-per-token instead of raw score (a cheap item with a merely-good score could beat an expensive item with a great one) — not explored here, since the fixed per-item token cost in every scenario makes the two equivalent for this exercise.
- A fourth policy that re-ranks within a pool (not just across pools) by score before filling — blurring the clean "allocator decides how much, not what" boundary this exercise keeps fixed.
- Combining this with 2026-10-24's `importance_weighted` single-sequence strategy as a fourth pool-aware policy.
