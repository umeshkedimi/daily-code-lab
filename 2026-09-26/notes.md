## Implementation

- **Corpus / labels.** `load_corpus()` returns Python's language reference (`pydoc_data.topics`): 79 sections, 60,030 words. `eval_set.py` holds 46 questions: 18 *lexical*, 18 *paraphrase*, 10 *unanswerable* (3 unrelated to Python, 7 about library modules the language reference doesn't cover; their telltale terms — `json`, `asyncio`, `csv`… — are asserted absent from the corpus). Relevance is by **evidence span**: a chunk is relevant iff it contains a gold span (whitespace-normalized), so the same labels work under any chunking; a span cut in two by every chunk boundary is reported as `split`.
- **Chunkers.** `chunk_words(size, overlap)` (sliding word window; the last window always reaches the end, with no redundant tail) and `chunk_paragraphs(max_words)` (pack whole paragraphs, split oversized ones).
- **Retrievers** (all `search(query, k) -> [Hit(index, score)]`, ties broken by lower chunk index):
  - `BM25Index` — inverted index, Okapi BM25 (k1=1.2, b=0.75), optional crude suffix stemmer.
  - `NgramIndex` — TF-IDF cosine over character 3–5-grams of word tokens (a *subword lexical* matcher, not semantic).
  - `DenseIndex` + `Embedder` protocol — exact cosine over embeddings. Real model: `FastEmbedEmbedder` (`BAAI/bge-small-en-v1.5`, 384-d, ONNX, CPU, query-side encoding for queries). `CachedEmbedder` caches by sha1(model + text) with atomic, merge-on-save writes. `HashedBagEmbedder` is a toy embedder used only to test the plumbing without a model.
  - `HybridRetriever` — reciprocal rank fusion (`1/(60 + rank)`), rank-only so BM25 and cosine scales need no calibration.
- **Post-retrieval.** `dedupe_hits` drops a hit when ≥ threshold of its word 4-gram shingles already appear in higher-ranked kept hits. `build_prompt` packs hits in rank order into a token budget (approximated as 1.3 tokens/word), numbers citations `[1]…`, skips hits that don't fit (later smaller ones may still fit), and always includes a "use ONLY these sources / say you don't know" instruction.
- **Measurement.** `Judge`, `evaluate` (first-relevant rank, doc-level rank, nDCG, per-question outcomes), `summarize`, `paired_bootstrap` (95% percentile CI of the mean per-question difference), `auroc`, `false_accept_at_recall`, `question_overlap`.
- **Not built:** any generation step (no LLM/API key here), a reranker/cross-encoder, approximate-nearest-neighbour search, query rewriting, multi-hop retrieval.

## Complexity

- BM25 index: O(total tokens); query: O(postings of the query terms). N-gram index: O(total n-grams), several times larger. Dense: index O(chunks × model cost), query O(N·d) brute force — trivial at 815×384 but the reason real deployments use ANN (HNSW/IVF) beyond ~10⁵–10⁶ chunks.
- RRF: O(rankers × depth log depth). Dedupe: O(k × shingles per chunk).
- Dense index memory: chunks × 384 × 4 bytes (≈1.2 MB at 815 chunks; the on-disk cache holding every chunk of the whole sweep is ~16 MB).
- Measured: the embedding model ran at ~112 chunks/s for ~120-word chunks on this CPU; embedding the 815 default chunks took ~22 s cold, 0.3 s from cache.

## Follow-up Questions

**Why?**
An LLM only knows its training data and has a limited context window. RAG retrieves the few relevant passages at question time and puts them in the prompt, so answers can use private/current documents and cite sources. Retrieval is the part that fails silently: if the right passage isn't in the prompt, no model can answer, and the failure looks like a hallucination. So retrieval quality has to be measured on its own — the harness here is the deliverable as much as the pipeline.

**How exactly?**
Chunk → index → for a question, retrieve top-k chunks (lexical, dense, or fused) → drop near-duplicates → pack into the token budget with citations and an abstention instruction → (generation, not built here).

**Which algorithm?**
BM25 (probabilistic term weighting with length normalization); TF-IDF cosine over character n-grams; dense bi-encoder retrieval (cosine over embeddings); reciprocal rank fusion for hybrid; shingle overlap for dedupe; paired bootstrap for significance; AUROC for abstention.

**Which library?**
Everything lexical, fusion, dedupe, chunking and evaluation is standard library. `fastembed` (ONNX runtime, no PyTorch) provides the real embedding model; `numpy` for vector math. Both are optional at import time — without them the lexical pipeline and the whole harness still run, and the dense tests skip.

**What happens internally?**
The bi-encoder maps each chunk to one 384-d vector, independently of any question; a question is mapped by the same model and compared by cosine. That is why it can match paraphrases (nearby vectors) and why it forgets detail (one vector summarizes the whole chunk, and the model truncates at 512 tokens). BM25 instead scores exact term matches weighted by rarity (idf) and dampened by term-frequency saturation and chunk length.

**How is it implemented?**
See [Implementation](#implementation).

**What if this fails?**
- *Right passage never retrieved:* measured — at default chunking BM25 misses 10 of 36 questions at R@5 and dense 6. The misses concentrate in paraphrase questions.
- *Answer split across chunks:* at 30-word chunks, 2 (no overlap) or 1 (with overlap) of the 36 answer spans are cut in half by every boundary; at ≥60 words, none.
- *Duplicates crowd the results:* 26 of 180 top-5 slots (hybrid, threshold 0.6) were redundant copies or overlapping windows.
- *Unanswerable question gets a confident answer:* with a score threshold set so 90% of answerable questions are still answered, dense lets 10% of unanswerable ones through, BM25 30%, the n-gram matcher 50% (10 unanswerable questions, so one question = 10 points).
- *Poorly-formed fragments:* word windows cut mid-sentence; the demo's packed prompt starts its first source in the middle of a sentence.
- *Numerical warnings that mean nothing:* numpy 2.0 on macOS raised divide/overflow/invalid floating-point flags from the float32 matmul although every result was finite and matched a float64 recomputation to ~8e-8 with identical top-10 ordering; the flags are silenced and a real finiteness check raises if a non-finite score ever appears.

**What trade-offs did you consider?**
- **Lexical vs dense vs hybrid.** On questions that reuse the passage's vocabulary all systems score R@5 = 0.94 (the 3-way hybrid 1.00). On paraphrases BM25/n-gram get 0.50, dense 0.72. Hybrid (BM25+dense) ties dense at R@5 (0.83) and is better on rank quality (R@1 0.64 vs 0.58, MRR 0.73 vs 0.70, nDCG 0.73 vs 0.69) but worse at R@10 (0.89 vs 0.97). Adding a *second lexical* ranker hurts: BM25+n-gram+dense drops paraphrase R@5 to 0.56 — consistent with two lexical voters outvoting the dense one (not tested directly).
- **Chunk size — at equal context.** R@5 alone rises with chunk size (dense 0.53 → 0.83–0.89), but five 345-word chunks hand the reader ~12× more text than five 30-word ones. At an equal ≈600-word budget the trend mostly disappears: dense 0.83–0.86 (30-word chunks, k=20), 0.72 (60w, k=10), 0.83 (120w, k=5), 0.72–0.75 (240w, k=3), 0.67–0.72 (480w, k=2). Hybrid is steadier (0.69–0.83). Big chunks hurt dense retrieval at a fixed budget, plausibly because one vector must summarize more text — a hypothesis, not tested.
- **Overlap** gave no consistent gain (dense R@5 without vs with overlap — 30w: 0.53 vs 0.58; 60w: 0.67 vs 0.69; 120w: 0.83 vs 0.83; 240w: 0.89 vs 0.83; 480w: 0.83 vs 0.83) while adding 16–30% more chunks to embed and store.
- **Paragraph packing** was not clearly better than fixed windows (R@5 0.72 dense at 89 avg words, doc-level R@5 0.83 — the lowest of all configs).
- **Dedupe threshold.** Adjacent 25%-overlap windows share ~0.25 of their 4-grams; the same passage copied into another section (different chunk boundaries) shares 0.75–0.79 (measured on one query's top-5). My a-priori 0.8 missed exactly those copies, so the default is 0.6.
- **Precision of an approximate token count** (1.3 tokens/word) vs a real tokenizer: adequate for budgeting, not for hard limits.

**How do you debug it?**
- Per-question outcomes (`QResult`: first relevant rank, doc-level rank, top chunk ids) — every aggregate number can be traced to specific questions; the demo's "fixed by dense / broken by dense" line lists question ids.
- Look at what was retrieved *instead*, not just that the gold wasn't. That is how the label gap was found (below).
- `Judge.n_split_evidence()` separates "the pipeline can't surface this" (span cut by chunking) from "the retriever ranked it badly".
- Sanity controls: measured question/answer word overlap (0.39 lexical vs 0.14 paraphrase) verifies the question types; a constant score has AUROC exactly 0.5.

**How do you evaluate it?**
- **Recall@k / MRR / nDCG on 36 answerable questions**, computed from evidence spans; **paired bootstrap** so differences carry an interval; **doc-level recall** to separate "found the right section" from "found the right chunk".
- **Label audit by pooling** (details below), with results reported under strict and audited labels.
- **Abstention** via AUROC and false-accept rate at 90% answerable recall.
- **Test suite:** 39 tests, 20/20 consecutive clean runs, plus a **mutation check** — 14 deliberate breaks (BM25 idf and length-normalization, RRF rank offset, chunk stride, paragraph flush, dedupe accumulation, prompt budget, alternate labels, bootstrap sign, AUROC ties, nDCG normalization, dense normalization, cache merging, stopword removal), **all 14 caught** after fixing the one initially missed.
- **Not evaluated:** answer faithfulness/generation (no LLM key), author-independent questions (I wrote all 46), multi-hop questions, other corpora or embedding models, rerankers, latency at scale, more than one annotator.

## Key Learnings

- **Error analysis found a gap in my own labels — and it changed a conclusion.** For "why does a list used as a default argument keep its contents between calls?", *both* retrievers returned a `calls` passage saying "a list or dictionary used as default value will be shared by all calls" — a correct answer that my single gold span (in the `function` section) didn't recognize. Pooling the top-3 results of all four systems for the 13 questions any system missed, and judging them against the question, found second correct passages for 3 questions (P03, P04, P18). Under strict labels dense beat BM25 on paraphrases *distinguishably* (+0.28, CI [+0.06, +0.56]); under audited labels it is +0.22, CI [+0.00, +0.50] — **cannot tell apart**. Three relabelled questions out of 36 moved the statistical conclusion, and the audit helped BM25 and hybrid more than dense (R@5 0.67→0.72 and 0.78→0.83 vs 0.81→0.83). Caveat: one annotator, who had seen the system outputs — hence both label sets are reported.
- **A confounded comparison looks like a result.** "Bigger chunks are better" was in the first table; it was mostly the reader receiving more text. Controlling for context budget removed most of it and reversed it for dense retrieval. Whenever recall@k is compared across chunk sizes, ask what k costs.
- **I predicted wrong about RRF and abstention.** I expected fused rank scores to carry no confidence signal (AUROC ≈ 0.5). Measured 0.92 (BM25+dense) — agreement between rankers shifts the top score — though still below dense alone (0.97), so gating on a component score is better than on the fused one. My notes almost recorded the prediction as a finding.
- **A test that never ran gave false comfort.** I filtered with `-k "not real_"` to skip the model tests; the name `test_the_real_corpus_…` also matched, so it was silently deselected and its unverified assumption ("copies crowd the top-5") went unchecked until I ran everything. When it ran it failed — for a reason worth knowing: the duplicate passages overlap only ~0.75, just under my 0.8 dedupe threshold. Selecting tests by substring is fragile; use markers or skip conditions.
- **The mutation check exposed a tautological test.** My dense-index test compared scores against the index's *own* stored matrix, so un-normalizing the vectors went unnoticed (13/14 caught). It now recomputes cosine from the raw embeddings.
- **Report the curve, not the pick.** Dedupe gave +0.06 R@5 (two questions, CI [+0.00, +0.14]) at *every* threshold from 0.5 to 0.95 — the threshold changes how many slots count as redundant (28 → 11), not which questions are fixed — so the tuned default isn't what produces the gain. The gain itself is suggestive, not established.
- **n = 36 is small.** One question = 0.028; differences of ≤ 0.06–0.08 in the sweep (no CIs there) are within noise. Only large effects are claimed: very small chunks split answers; lexical search matches dense retrieval on shared vocabulary; and the paraphrase gap points toward dense/hybrid but is not statistically established here.
- **Smaller robustness fixes:** the embedding cache wrote non-atomically and could drop another process's entries (now temp-file + `os.replace`, merge-on-save, with a test); an AUROC test I first wrote contained `or True` and could never fail (replaced with hand-computed values).
