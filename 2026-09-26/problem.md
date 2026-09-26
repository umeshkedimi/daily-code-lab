# Problem

**Category:** `genai-agentic`

## Statement

Build a **retrieval-augmented generation (RAG) retrieval pipeline** — chunk documents, index them, retrieve the top-k passages for a question, and assemble a grounded prompt — and, more importantly, **build the harness that measures whether it works**.

**Requirements:**
- Chunking, at least one lexical and one dense (embedding) retriever, and a way to combine them.
- A labeled evaluation set and retrieval metrics (recall@k, MRR, nDCG).
- Comparison of retrievers *and* of chunking strategies, with a statement of how much of any difference is noise.
- Handling of two real-world failure modes: near-duplicate passages crowding out results, and questions the corpus cannot answer.
- Prompt assembly under a token budget, with source citations and an abstention instruction.

## Constraints

- **Labels must survive re-chunking.** Chunk size is one of the things being compared, so relevance cannot be defined by chunk id.
- **No generation model is available** (no LLM API key in this environment), so answer *faithfulness* cannot be evaluated. Retrieval quality and prompt assembly can, and the notes say plainly what is therefore not covered.
- **Small eval set (36 answerable questions):** differences must come with confidence intervals; a 0.03 gap on 36 questions is not a finding.
- **The corpus must be real text I did not write**, because I write the questions and would otherwise be grading my own homework.
- Dense retrieval must be a *real* embedding model, not a stand-in, or "does semantic search help?" goes unanswered.

## Approach

1. **Corpus:** Python's language reference (`pydoc_data.topics`, 79 sections, ~60,000 words), shipped with the interpreter — real technical prose, stable, nothing to copy into the repo.
2. **Labels by evidence span.** Each question carries a short verbatim span from its answer passage; a chunk is relevant iff it contains the span. The same 36 labels stay valid for any chunk size or strategy, and a span cut in two by a chunk boundary is *detected* (no chunk contains it) rather than silently mis-scored.
3. **Three question kinds, with the split measured.** *Lexical* questions reuse the passage's vocabulary; *paraphrase* questions ask for the same fact in different words (the classic keyword-search failure); *unanswerable* questions have no answer in the corpus (3 unrelated, 7 about Python library modules the language reference doesn't cover). Because I wrote the questions after reading the passages, the lexical/paraphrase distinction is *measured* (word overlap with the answer span), not assumed.
4. **Retrievers:** BM25 from scratch (optionally with a crude stemmer); a character-n-gram TF-IDF matcher from scratch (subword lexical, explicitly *not* semantic); dense retrieval with `bge-small-en-v1.5` (a real 384-d model, via `fastembed`/ONNX, on CPU) behind an `Embedder` interface with a disk cache; and **reciprocal rank fusion** to combine rankers without calibrating their incomparable score scales.
5. **Chunking:** sliding word windows (size × overlap) and paragraph packing, swept across sizes to expose the trade-off between splitting answers apart and diluting them.
6. **Post-retrieval:** shingle-based **de-duplication** (overlapping windows *and* the same passage copied across sections), and a **token-budgeted prompt builder** with numbered citations and an explicit "say you don't know" instruction.
7. **Statistics:** per-question outcomes feed a **paired bootstrap** so every comparison reports whether the data can actually tell the systems apart.
8. **Abstention:** AUROC of top-1 score for answerable vs unanswerable questions, and the false-accept rate at a threshold that still answers 90% of answerable ones.
