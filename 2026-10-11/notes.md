## Implementation

- `_hashed_embedding`: the hashing trick -- each token hashed via SHA-256 to one of `dim` buckets with a pseudo-random sign (`(h // dim) % 2`), summed, then L2-normalized. Deterministic (SHA-256, not Python's randomized `hash()`), so every similarity value in this write-up is bit-for-bit reproducible on any machine.
- `CharTrigramEmbedder` operates on padded character trigrams (`"  text  "` sliced into overlapping 3-grams); `WordBagOfWordsEmbedder` on whitespace/punctuation-split words with a short, generic stopword list applied first. Trigrams reward *any* shared substring, including a shared digit; words require whole-token overlap, which is coarser but still has zero notion of synonymy.
- `true_answer` is the ground truth, built independently of the cache: an arithmetic regex checked first, then a "concept groups" matcher for the fixed canonical questions -- each canonical maps to a tuple of keyword groups, and a prompt matches iff at least one synonym from *every* group is present. This OR-within-group, AND-across-groups structure is what lets `true_answer` recognize the heavy paraphrases' own wording (which deliberately avoids "capital", using "seat of government" instead) -- see Key Learnings for the bug this exact design point was fixed in response to.
- `SemanticCache.query` does a full linear scan over cached entries every call (O(n) in cache size) -- there's no indexing structure, which is a deliberate scope cut: this exercise is about whether similarity-based lookup is *safe*, not about making it fast at scale (a real system would pair this with an approximate nearest-neighbor index).
- `measure_tradeoff` tracks hit rate and wrong-answer rate *separately* for every category (light, heavy, confuser) -- the first version of this function only tracked hit rate, which silently miscounted a wrong-answer collision between two heavy paraphrases as a cache "success" (see Key Learnings).
- Not built: cache eviction/sizing (already covered, differently, by 2026-09-20's LRU cache), a real trained embedding model (the entire point is measuring what a from-scratch lexical one can and can't do), approximate nearest-neighbor indexing for scale.

## Complexity

- `embed`: O(tokens in the text) to hash and accumulate, O(dim) to normalize.
- `SemanticCache.query`: O(n · dim) per call, `n` = cache size -- a full scan, re-stated from Implementation because it's the reason this exercise doesn't test cache behavior at large `n`.
- **The measured trade-off table** (the actual point of this exercise), full threshold sweep, both embedders:

  **char-trigram:**

  | threshold | light hit% | heavy hit% | heavy wrong% | confuser wrong% | llm calls |
  |---|---|---|---|---|---|
  | 0.00 | 100% | 100% | 67% | 100% | 1 |
  | 0.30 | 100% | 67% | 33% | 100% | 2 |
  | 0.50 | 100% | 33% | 33% | 100% | 5 |
  | 0.70 | 67% | 0% | 0% | 100% | 8 |
  | 0.80 | 100% | 0% | 0% | 100% | 9 |
  | 0.85 | 100% | 0% | 0% | 0% | 12 |
  | 0.95 | 0% | 0% | 0% | 0% | 15 |

  **word-bag-of-words:**

  | threshold | light hit% | heavy hit% | heavy wrong% | confuser wrong% | llm calls |
  |---|---|---|---|---|---|
  | 0.00 | 100% | 100% | 67% | 100% | 1 |
  | 0.30 | 100% | 33% | 33% | 100% | 7 |
  | 0.50 | 100% | 33% | 33% | 0% | 11 |
  | 0.70 | 100% | 33% | 33% | 0% | 11 |
  | 0.75 | 100% | 0% | 0% | 0% | 12 |
  | 1.00 | 33% | 0% | 0% | 0% | 14 |

  Reading char-trigram's table: `confuser wrong%` doesn't reach 0 until threshold 0.85, but `heavy hit%` is already 0 by threshold 0.70 -- there's a dead zone (0.70-0.80) with **zero paraphrase benefit and 100% confuser risk simultaneously**, and no threshold anywhere achieves nonzero heavy-hit benefit with zero risk.
- **The single pairwise comparison that implies the whole table**: `cosine(canonical, heavy_paraphrase)` = 0.372 (trigram) / 0.000 (word-bow); `cosine(confuser_a, confuser_b)` = 0.824 (trigram) / 0.500 (word-bow). Since the heavy-paraphrase similarity is *lower* than the confuser similarity for **both** embedders, no threshold can accept the former while rejecting the latter -- accepting the lower score necessarily also accepts the higher one.
- **Randomized robustness** (200 digit-swap confuser pairs, seed 0, char-trigram): mean similarity 0.831, min 0.746 -- the risk is systematic, not an artifact of the two hand-picked examples above.

## Follow-up Questions

**Why?**
Caching repeated LLM calls is one of the most common cost/latency optimizations in production LLM systems, and "cache by meaning, not exact text" is the obvious next step once exact-match caching's low hit rate becomes a problem -- but "by meaning" requires an embedding that actually captures meaning, which this exercise shows a cheap lexical one does not reliably do.

**How exactly?**
Embed the incoming prompt, compare against every cached prompt's embedding by cosine similarity, and treat the best match as "the same question" if its similarity clears a threshold -- the entire design rests on that threshold being a reliable proxy for "would get the same correct answer," which is exactly the claim measured and found false here.

**Which algorithm?**
The hashing trick (feature hashing, Weinberger et al. 2009) for the embeddings; cosine similarity with a fixed threshold for the cache lookup -- both standard, well-known techniques, deliberately not a novel one, so the failure mode measured is about the *class* of approach (lexical/hashed, untrained), not an implementation quirk.

**Which library?**
Standard library only: `hashlib` for SHA-256 hashing, `re` for tokenization and the arithmetic ground truth, `math` for the L2 norm.

**What happens internally?**
A hashed bag-of-words vector is, structurally, just "which buckets did any of my tokens land in, and with what sign" -- cosine similarity between two such vectors is dominated entirely by token *overlap*, with no mechanism anywhere to represent that "capital" and "seat of government" refer to the same concept, or that "7" and "8" are both just numbers rather than load-bearing identity tokens. The failure isn't a bug in the hashing; it's that lexical overlap and semantic equivalence are different things, and this embedding can only ever measure the former.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- **A heavy paraphrase using completely different vocabulary than the canonical question:** measured directly above -- similarity collapses to 0.372/0.000, below the confuser pairs' similarity for both embedders. There is no fix within this architecture; it would require an embedding that actually models synonymy (i.e., a trained one).
- **Two different canonical questions' heavy paraphrases sharing enough generic wording to collide with each other:** found while building this corpus, not designed in advance -- `"...serves as France's seat of government"` and `"...serves as the seat of Japan's government"` share "city", "serves", "seat", "government", enough that at a moderate word-bow threshold the second one queried matches the *first's* cached entry and returns the wrong country's capital. Verified directly (`test_heavy_paraphrases_of_different_questions_can_collide_with_each_other`) -- this is the same risk as the hand-built confuser pairs, but emerging naturally from the paraphrase corpus itself, which is a stronger demonstration than a deliberately adversarial pair.
- **An exact tie at the threshold boundary:** `best_sim >= threshold`, inclusive -- verified by forcing the threshold to the *exact* bit-for-bit value `query` computes for a self-comparison (floating-point similarity essentially never lands on a round number otherwise; see Key Learnings).
- **A prompt that is simultaneously an arithmetic expression and a coincidental keyword match** (e.g. mentions "capital" and "France" while also containing "12 + 7"): resolved by checking the arithmetic regex first -- an explicit, tested priority decision (`test_an_explicit_arithmetic_expression_takes_priority_over_a_coincidental_keyword_match`), not an accident of code order nothing would notice if reversed.

**What trade-offs did you consider?**
- **Two independently-built lexical embeddings, both from scratch (chosen) vs. one embedding plus a claim about "real" models:** building two different lexical approaches and showing *both* fail the same way is much stronger evidence that the problem is structural (lexical overlap ≠ semantic equivalence) rather than a fixable quirk of one specific hashing scheme.
- **Hand-picked confuser pairs, a corpus-wide threshold sweep, AND a 200-trial randomized sweep (chosen) vs. any one of these alone:** the hand-picked pairs make the failure concrete and debuggable; the threshold sweep makes "no safe threshold" a checked property instead of a read-off-the-table impression; the randomized sweep rules out "you just got unlucky with two examples."
- **Separately tracking hit rate and wrong-answer rate per category (chosen, after finding the gap) vs. a single aggregate "accuracy" number:** an aggregate would have hidden exactly the risk this exercise exists to surface -- see Key Learnings for the concrete case this distinction caught.

**How do you debug it?**
- When a cache hit looks suspicious, `true_answer(the_actual_prompt_queried)` vs. the cached response immediately tells you whether it's a real bug or a correct hit that merely looks surprising.
- For a "why did/didn't this match" question, print `cosine_similarity` against *every* cached entry, not just the winning one -- the heavy-paraphrase-vs-heavy-paraphrase collision (Key Learnings) was found exactly this way, by noticing the "winning" match wasn't the canonical entry at all.
- `measure_tradeoff`'s per-category breakdown is the debugging tool of record for "is this threshold safe": a single aggregate pass/fail number would never have surfaced the 0.70-0.80 char-trigram dead zone (zero benefit, full risk) documented in Complexity.

**How do you evaluate it?**
- **49 tests, 5/5 consecutive clean runs.**
- **Mutation check, 4 targeted mutations, each applied and reverted in isolation with all bytecode caches cleared before every run** (see Key Learnings for why "all" needed real emphasis this time):
  - `SemanticCache` threshold check `>=` → `>`: 2/49 failed (required a test that forces an *exact* bit-for-bit boundary value, not a naturally-occurring one -- see Key Learnings).
  - Hashing trick drops the signed-hashing step (always `+1`, never `-1`): 1/49 failed.
  - `true_answer` checks canonical concept groups before the arithmetic regex: 1/49 failed (required an explicit, deliberately-constructed ambiguous prompt -- see Key Learnings).
  - `SemanticCache.query` appends the new entry to the cache *before* checking for a hit (so every query would trivially "hit" its own freshly-added entry): 9/49 failed.

## Key Learnings

- **Measuring the actual trade-off, instead of asserting it, found a real design bug before any mutation testing even started.** The heavy paraphrases were deliberately written to avoid the canonical question's own words -- that's the whole point of calling them "heavy." But the *ground truth* function's keyword matcher required those same words, so `true_answer()` raised `UnknownPromptError` on its own corpus the first time `demo()` ran. Fixed by switching from flat required-keyword lists to synonym groups (OR within a group, AND across groups). Added a direct regression test (`test_every_corpus_prompt_has_a_defined_ground_truth`) iterating every prompt in the corpus, so this exact class of self-inconsistency can't silently return.
- **A metric that only tracks "was it a hit" can't tell you a hit was wrong -- and it cost a real, unplanned finding before being caught.** The first version of `measure_tradeoff` reported `heavy_hit_rate` with no correctness check. Running it surfaced a 33% heavy-hit rate that looked like a success -- until checking it directly showed the "hit" was the Japan paraphrase matching the already-cached France paraphrase's entry and returning "Paris" for a question whose true answer is "Tokyo." This wasn't a case built on purpose; it emerged from two heavy paraphrases sharing generic wording ("city", "serves", "seat", "government"). Fixed by tracking hit rate and wrong-answer rate as separate, explicit measurements for every category, and locked in with a dedicated test (`test_heavy_paraphrases_of_different_questions_can_collide_with_each_other`).
- **Floating-point cosine similarity essentially never lands on a round number, which made the `>=` vs. `>` mutation invisible until specifically forced.** Even a text compared against an identical copy of itself came out to `0.9999999999999998`, not exactly `1.0`. This meant no "natural" test in the corpus exercised the exact boundary where `>=` and `>` actually disagree -- the first full mutation run on this file reported zero failures for that mutation, which (correctly, this time) meant a real coverage gap, not a safe mutation. Caught by writing a test that sets `cache.threshold` to the exact value `query()` will compute, bit for bit, rather than hoping a "round" threshold like `0.9` would ever coincide with one.
- **A system-wide, hidden bytecode cache outside the project directory produced false "zero failures" for two mutations, and the fix changes how every future mutation-testing session in this environment needs to clear caches.** `sys.pycache_prefix` on this machine is set to `~/Library/Caches/com.apple.python/...`, mirroring the full project path -- entirely invisible to `find . -name __pycache__` (scoped to the working directory) and to `rm -rf .pytest_cache`, both of which this repo's prior days relied on exclusively. Worse, the standard timestamp-based cache invalidation check (recorded mtime + size in the `.pyc` header) was fooled by this exercise's own mutate → test → `cp`-restore cycle running fast enough that two different file contents landed on the *same whole-second mtime* -- `stat` showed the restored file and the stale cached bytecode agreeing on `mtime=1791735892` down to the second, even though the content differed. `python3 -B` (don't *write* new bytecode) did **not** fix this, because it still *reads* an already-existing, apparently-still-valid cache. The actual fix: `export PYTHONDONTWRITEBYTECODE=1` for the whole session (prevents the condition from ever recurring) plus `rm -rf` on the external cache directory once to clear the already-poisoned entries. Both of this exercise's two "zero failures" mutations (the `>=`/`>` threshold check and the arithmetic-ordering check) were re-run after this fix and reclassified correctly as real gaps, not benign mutations -- the earlier "0 failures" readings for both were artifacts of this cache, not evidence about the code. Every mutation-testing session in this repo going forward should set `PYTHONDONTWRITEBYTECODE=1` before starting, not just clear the local `__pycache__`/`.pytest_cache`.
