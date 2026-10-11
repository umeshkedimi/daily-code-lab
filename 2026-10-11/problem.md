# Problem

**Category:** `genai-agentic`

## Statement

Build a semantic cache for LLM calls: instead of caching by exact prompt text, embed the prompt and look up the nearest cached entry by cosine similarity; if it's close enough (above a configurable threshold), return the cached response without calling the (simulated) LLM.

The actual question this exercise answers: **does a cheap, from-scratch lexical embedding separate true paraphrases (same answer) from near-duplicate-but-different prompts (different answer) well enough that some threshold is actually safe to use?** Two embeddings are built from scratch (hashed character-trigrams, and a hashed bag-of-words) specifically so there's no "a smarter model would have handled this" escape hatch — the measurement has to stand on its own.

**Requirements:**
- A simulated LLM (`CountingLLM`) is always correct and counts every call — the "cost" the cache exists to reduce.
- A **ground-truth function** (`true_answer`) computes the right answer for any prompt directly, independent of the cache, so a cache hit's correctness can be checked against what *this specific prompt* should have gotten — not just whatever the cache happened to return.
- The trade-off must be **measured across a threshold sweep** (hit rate, and — critically — wrong-answer rate, for both the intentionally-constructed "confuser" pairs and the paraphrase corpus itself) rather than demonstrated with one cherry-picked example.
- The corpus includes both **light paraphrases** (minor wording change) and **heavy paraphrases** (same meaning, different words entirely) — a lexical embedding's failure mode should show up specifically on the heavy case.

## Constraints

- Standard library only (`hashlib`, `math`, `re`) — no embedding API, no trained model. This is what makes the finding honest rather than a toy-implementation artifact to blame.
- Both embeddings are deterministic (SHA-256-based hashing, not Python's randomized `hash()`), so every similarity value is bit-for-bit reproducible — there is no flaky "sometimes it works" to explain away.
- Checked with hand-picked adversarial pairs (digit-swap arithmetic confusers), a corpus-wide falsifiable property check across a threshold sweep (no threshold is simultaneously safe and beneficial), and a 200-trial randomized sweep confirming the confuser-pair risk isn't cherry-picked.

## Approach

1. **Two embeddings, both the "hashing trick" (Weinberger et al.):** each token (a character trigram, or a stopword-filtered word) is hashed to one of `dim` buckets with a pseudo-random sign, summed, then L2-normalized. No vocabulary or training step — `CharTrigramEmbedder` operates on substrings (surface-level), `WordBagOfWordsEmbedder` on whole words (coarser, but still has zero notion of synonymy).
2. **A ground-truth oracle (`true_answer`) built independently of the cache**, so "was this hit correct" is checkable per-prompt: arithmetic prompts are parsed and computed directly; canonical questions are matched via substring "concept groups" (synonym-tolerant enough to recognize the heavy paraphrases' own deliberately-different wording — see notes.md for a bug this caught in its own corpus).
3. **A confuser corpus**: prompts that differ by a single digit but have different true answers (`"What is 12 + 7?"` vs `"What is 12 + 8?"`) — the realistic risk case for any similarity-based cache, since the edit is tiny but answer-changing.
4. **Measure, don't assert**: `measure_tradeoff` runs a fixed corpus (canonical questions, light paraphrases, heavy paraphrases, confuser pairs) through the cache at a sweep of thresholds, reporting hit rate *and* wrong-answer rate separately for each category — because a "hit" can itself be wrong (see notes.md), and conflating the two would hide exactly the risk this exercise is about.
5. **A single falsifiable claim, checked by code, not by eyeballing a table**: across the full threshold sweep, no threshold is simultaneously "beneficial" (some heavy paraphrase correctly hits) and "safe" (zero wrong answers anywhere) — this is what "there's no good threshold" actually means, made testable.
