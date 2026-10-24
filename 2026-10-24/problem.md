# Problem

**Category:** `genai-agentic`

## Statement

Implement and measure three context-window truncation/retention strategies for a long conversation, against a question whose answer depends on a single fact buried somewhere earlier in the history.

The actual question this exercise answers: **when a fixed token budget forces history to be cut, which strategy keeps a fact retrievable the longest as it gets buried deeper — and where does each one's own retention trick break?** Naive truncate-oldest, a budget-constrained extractive-summary fallback, and an importance-weighted retention policy are all built from scratch (no real LLM, no embeddings) so each strategy's failure mode is attributable to the strategy itself, not to "a smarter model would have handled this."

**Requirements:**

- `count_tokens(text) -> int` — whitespace-split word count, the stand-in for a tokenizer.
- `answer(context: List[str]) -> Optional[str]` — a deterministic extractor: searches the given context turns, in order, for the pattern `my account id is (\d+)` (case-insensitive) and returns the first captured value found, or `None` if no turn in the given context matches. This *is* the simulated "LLM" — it can only answer from what's actually in the context it's given, and it can only recognize the one phrasing its regex knows.
- Three retention strategies, same interface — `(turns: List[str], budget: int) -> List[str]`, called on the history *before* the current question, returning which turns survive, in chronological order:
  - `truncate_oldest(turns, budget)`: keep the most recent turns that fit within `budget`; drop everything older.
  - `summarize_oldest(turns, budget)`: same recent-turn window as `truncate_oldest`, but the turns it would otherwise drop entirely are reduced to a single prepended summary turn — built by extracting only the fact-pattern clauses from those dropped turns (via the same pattern `answer()` uses), most-recently-dropped first, filling whatever budget is left over after the recent window. If a fact clause doesn't fit in what's left, it (and every clause older than it) is left out of the summary.
  - `importance_weighted(turns, budget)`: every turn is scored — matches the fact pattern (important) or doesn't (unimportant) — and turns are retained importance-first, recency second, within budget, trying every candidate turn in that priority order (not stopping at the first one that doesn't fit) so a later, cheaper, lower-priority turn can still claim leftover room.
- A ground-truth fact value is always known independently (the test embeds it), so every answer is checked against the real value, not just "did it return something."

## Constraints

- Standard library only (`re`) — no tokenizer, no embeddings, no trained model.
- Deterministic: fixed filler-turn generator, no randomness, so every measurement is reproducible.
- Measured across a **depth sweep** (how many turns separate the fact from the question) for each strategy — not demonstrated with one cherry-picked conversation.
- At least one adversarial case: a fact phrased outside the regex's recognized pattern, to separate "was it retained" from "could it have been read even if retained."

## Approach

1. **One shared pattern is the single source of truth for "is this fact-shaped."** `answer()`, `importance_weighted`'s scorer, and `summarize_oldest`'s extractor all key off the same `my account id is (\d+)` pattern. This is deliberate: a phrasing the pattern can't recognize is invisible to every strategy equally, so a failure on that phrasing measures the extractor's limit, not any one retention policy's limit.
2. **`truncate_oldest` has a hard recency cliff.** Correctness is 100% while the fact's turn is inside the kept window and drops to 0% the instant it falls outside — a step function, measured by sweeping depth at a fixed budget, not assumed.
3. **`summarize_oldest` extends that cliff but doesn't remove it.** The summary itself is budget-constrained and filled most-recently-dropped-fact-first, so it reliably rescues a buried fact only until *other*, more-recently-dropped fact-shaped turns (plausible in a real conversation — "my X is Y" statements aren't unique to the one fact that matters) crowd it out of the summary's own leftover budget first.
4. **`importance_weighted` doesn't decay with recency at all** — a correctly-recognized fact survives arbitrarily deep, at the cost of displacing unimportant recent turns that `truncate_oldest` would have kept instead.
5. **The adversarial phrasing case is the ceiling on all three.** With the budget effectively unbounded (nothing truncated under any strategy — full retention), a fact phrased outside the regex's pattern is still unanswerable. That proves retention policy and extraction capability are separate failure modes: no retention strategy can compensate for an extractor that can't parse the phrasing it's given.

## Stretch

- A real tokenizer (`tiktoken`) instead of whitespace count.
- A second, differently-phrased fact pattern, to test whether `importance_weighted`'s single-pattern scorer generalizes or needs one rule per fact shape.
- Hybrid strategy: importance-weighted among recent turns, summary fallback for the rest.
- Token-cost accounting: compare total tokens spent per strategy at matched recall, not just recall at matched budget.
