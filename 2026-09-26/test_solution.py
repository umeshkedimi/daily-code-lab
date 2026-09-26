import math
import os
import sys

import pytest

import solution
from eval_set import ANSWERABLE, QUESTIONS, UNANSWERABLE, Question
from solution import (
    BM25Index,
    CachedEmbedder,
    Chunk,
    DenseIndex,
    Document,
    HashedBagEmbedder,
    Hit,
    HybridRetriever,
    Judge,
    NgramIndex,
    approx_tokens,
    auroc,
    build_prompt,
    chunk_corpus,
    chunk_paragraphs,
    chunk_words,
    crude_stem,
    dedupe_hits,
    evaluate,
    false_accept_at_recall,
    load_corpus,
    mean,
    norm_ws,
    paired_bootstrap,
    per_question,
    question_overlap,
    rrf_fuse,
    summarize,
    tokenize,
)


def chunks_of(*texts, doc="d"):
    return [Chunk(doc, t, i) for i, t in enumerate(texts)]


# --- chunking ----------------------------------------------------------------------------


def test_chunk_words_windows_overlap_and_reach_the_end():
    doc = Document("d", " ".join(f"w{i}" for i in range(25)))
    cs = chunk_words(doc, size=10, overlap=4)  # stride 6: starts 0, 6, 12, 18 -> last window reaches word 24
    assert [c.text.split()[0] for c in cs] == ["w0", "w6", "w12", "w18"]
    assert cs[-1].text.split()[-1] == "w24"
    for a, b in zip(cs, cs[1:]):
        assert a.text.split()[-4:] == b.text.split()[:4]  # exactly `overlap` shared words
    assert [c.index for c in cs] == [0, 1, 2, 3]


def test_chunk_words_no_redundant_tail_and_full_coverage():
    for n in (1, 9, 10, 11, 19, 20, 21, 100):
        doc = Document("d", " ".join(f"w{i}" for i in range(n)))
        cs = chunk_words(doc, 10, 0)
        assert " ".join(c.text for c in cs).split() == doc.text.split()  # nothing lost, nothing repeated
        assert all(len(c.text.split()) <= 10 for c in cs)


def test_chunk_words_edge_cases_and_validation():
    assert chunk_words(Document("d", ""), 10) == []
    assert len(chunk_words(Document("d", "a b c"), 10, 5)) == 1  # shorter than one window
    for size, overlap in ((0, 0), (5, 5), (5, -1), (5, 9)):
        with pytest.raises(ValueError):
            chunk_words(Document("d", "a b c"), size, overlap)


def test_chunk_paragraphs_packs_whole_paragraphs_and_respects_the_limit():
    text = "one two three\n\nfour five\n\nsix seven eight nine\n\nten"
    cs = chunk_paragraphs(Document("d", text), max_words=6)
    assert [c.text for c in cs] == ["one two three four five", "six seven eight nine ten"]
    assert all(len(c.text.split()) <= 6 for c in cs)


def test_chunk_paragraphs_splits_an_oversized_paragraph_and_keeps_order():
    text = "a b\n\n" + " ".join(f"x{i}" for i in range(10)) + "\n\nc d"
    cs = chunk_paragraphs(Document("d", text), max_words=4)
    assert all(len(c.text.split()) <= 4 for c in cs)
    assert " ".join(c.text for c in cs).split() == text.split()
    with pytest.raises(ValueError):
        chunk_paragraphs(Document("d", text), 0)


# --- tokenizing -----------------------------------------------------------------------------


def test_tokenize_drops_stopwords_keeps_dunder_names_and_lowercases():
    assert tokenize("What is the __getattr__ hook, and when is it called?") == ["__getattr__", "hook", "called"]
    assert tokenize("The Quick", stop=False) == ["the", "quick"]


def test_stemming_unifies_inflections_only_when_enabled():
    for group in (["raise", "raised", "raises"], ["evaluate", "evaluated", "evaluates"], ["default", "defaults"]):
        assert len({crude_stem(w) for w in group}) == 1, group
    assert tokenize("raised raises", stem=False) == ["raised", "raises"]
    assert tokenize("raised raises", stem=True) == ["rais", "rais"]
    assert crude_stem("class") == "class"  # 'ss' endings are not plurals


# --- BM25 ---------------------------------------------------------------------------------------


def test_bm25_matches_the_formula_by_hand():
    cs = chunks_of("alpha beta", "beta beta gamma")  # N=2; 'alpha' df=1
    idx = BM25Index(cs, k1=1.2, b=0.75)
    avgdl = (2 + 3) / 2
    idf = math.log(1 + (2 - 1 + 0.5) / (1 + 0.5))
    expected = idf * 1 * 2.2 / (1 + 1.2 * (1 - 0.75 + 0.75 * 2 / avgdl))
    hits = idx.search("alpha", 5)
    assert [h.index for h in hits] == [0]
    assert hits[0].score == pytest.approx(expected)


def test_bm25_rare_terms_outweigh_common_ones_and_length_is_normalized():
    cs = chunks_of("common common common", "common rare", "filler " * 30 + "rare")
    idx = BM25Index(cs)
    assert idx.search("common rare", 3)[0].index == 1  # has both; 'rare' is worth more than another 'common'
    short_vs_long = [h.index for h in idx.search("rare", 3)]
    assert short_vs_long.index(1) < short_vs_long.index(2)  # same tf, shorter chunk scores higher


def test_bm25_edge_cases():
    idx = BM25Index(chunks_of("alpha beta", "gamma delta", "alpha alpha"))
    assert idx.search("zzz", 3) == []  # no matching term
    assert idx.search("the of and", 3) == []  # only stopwords
    assert len(idx.search("alpha", 1)) == 1  # k respected
    tied = BM25Index(chunks_of("same words", "same words", "same words")).search("same", 3)
    assert [h.index for h in tied] == [0, 1, 2]  # exact ties broken by lower index, deterministically


# --- n-gram index -----------------------------------------------------------------------------------


def test_ngram_index_tolerates_typos_and_morphology_that_bm25_misses():
    cs = chunks_of("the function evaluates default arguments", "nothing relevant here at all")
    assert BM25Index(cs).search("evaluating", 2) == []  # different token: exact-match retrieval sees nothing
    assert NgramIndex(cs).search("evaluating", 2)[0].index == 0  # shared subwords still match


def test_ngram_identical_text_has_cosine_one():
    cs = chunks_of("alpha beta gamma delta", "epsilon zeta eta theta")
    top = NgramIndex(cs).search("alpha beta gamma delta", 2)[0]
    assert top.index == 0 and top.score == pytest.approx(1.0, abs=1e-6)


# --- dense retrieval mechanics (toy embedder: no model needed) -------------------------------------------

np = pytest.importorskip("numpy")


def test_dense_index_scores_are_true_cosine_similarities_computed_independently():
    cs = chunks_of("alpha beta gamma", "delta epsilon zeta", "alpha alpha delta", "omega omega omega")
    emb = HashedBagEmbedder(dim=128)
    idx = DenseIndex(cs, emb)
    hits = idx.search("alpha beta", 4)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True) and hits[0].index == 0
    raw_docs, raw_q = emb.embed_documents([c.text for c in cs]), emb.embed_query("alpha beta")
    for h in hits:  # cosine recomputed from the RAW vectors, not from the index's own stored matrix
        cosine = float(np.dot(raw_docs[h.index], raw_q) / (np.linalg.norm(raw_docs[h.index]) * np.linalg.norm(raw_q)))
        assert h.score == pytest.approx(cosine, abs=1e-5)
        assert -1.0 <= h.score <= 1.0
    assert np.allclose(np.linalg.norm(idx.matrix, axis=1), 1.0, atol=1e-5)
    assert len(idx.search("alpha", 99)) == 4  # k larger than the index


class CountingEmbedder:
    def __init__(self):
        self.calls = []

    def embed_documents(self, texts):
        self.calls.append(list(texts))
        return np.array([[float(len(t)), float(sum(map(ord, t)) % 97)] for t in texts], dtype=np.float32)

    def embed_query(self, text):
        return np.array([1.0, 1.0], dtype=np.float32)


def test_cached_embedder_embeds_each_distinct_text_once_and_survives_restarts(tmp_path):
    path = str(tmp_path / "cache.npz")
    inner = CountingEmbedder()
    emb = CachedEmbedder(inner, path, "ns")
    first = emb.embed_documents(["aa", "bbb", "aa"])  # a duplicate within one call
    assert inner.calls == [["aa", "bbb"]]
    assert np.array_equal(first[0], first[2]) and not np.array_equal(first[0], first[1])
    emb.embed_documents(["bbb", "cccc", "aa"])  # partially cached; order must follow the request
    assert inner.calls[-1] == ["cccc"]

    reloaded_inner = CountingEmbedder()
    reloaded = CachedEmbedder(reloaded_inner, path, "ns")  # a new process would do this
    again = reloaded.embed_documents(["cccc", "aa"])
    assert reloaded_inner.calls == []  # served entirely from disk
    assert np.array_equal(again[1], first[0])

    other_ns = CachedEmbedder(CountingEmbedder(), path, "different-model")
    other_ns.embed_documents(["aa"])
    assert other_ns.inner.calls == [["aa"]]  # a different model never reads another model's vectors


def test_cache_writes_are_atomic_and_concurrent_writers_do_not_erase_each_other(tmp_path):
    path = str(tmp_path / "shared.npz")
    a, b = CachedEmbedder(CountingEmbedder(), path, "ns"), CachedEmbedder(CountingEmbedder(), path, "ns")
    a.embed_documents(["only in a"])  # both were opened before either wrote
    b.embed_documents(["only in b"])
    merged = CachedEmbedder(CountingEmbedder(), path, "ns")
    assert len(merged._cache) == 2  # b's save did not discard a's entry
    assert [p.name for p in tmp_path.iterdir()] == ["shared.npz"]  # no temp files left behind


# --- fusion ---------------------------------------------------------------------------------------------


def test_rrf_arithmetic_and_preference_for_agreement():
    fused = rrf_fuse([[1, 2, 3], [3, 2, 1]], k=60)
    by = {h.index: h.score for h in fused}
    assert by[2] == pytest.approx(2 / 62)
    assert by[1] == pytest.approx(1 / 61 + 1 / 63) and by[3] == pytest.approx(by[1])
    assert [h.index for h in fused] == [1, 3, 2]  # 1 and 3 tie (broken by index); both edge the middle one
    assert rrf_fuse([[7, 8], [9, 7]])[0].index == 7  # in both lists beats #1 in only one
    assert {h.index for h in rrf_fuse([[1], [2]])} == {1, 2}  # union: absent from one list is fine


def test_hybrid_retriever_fuses_component_rankings():
    class Stub:
        def __init__(self, order):
            self.order = order

        def search(self, query, k):
            return [Hit(i, 1.0) for i in self.order[:k]]

    h = HybridRetriever([Stub([4, 5, 6]), Stub([6, 4, 9])], depth=3)
    assert [x.index for x in h.search("q", 2)] == [4, 6]


# --- de-duplication ---------------------------------------------------------------------------------------


def test_dedupe_drops_copies_and_heavy_overlap_but_keeps_distinct_and_tiny_chunks():
    base = "the finally clause always executes and if it returns the saved exception is discarded here"
    shifted = " ".join(base.split()[2:] + ["extra"])  # 10 of its 11 four-word shingles already appeared
    cs = [
        Chunk("try", base, 0),
        Chunk("compound", base, 0),  # the same passage duplicated in another section
        Chunk("try", shifted, 1),  # a sliding-window neighbour that mostly repeats the first
        Chunk("with", "context managers define enter and exit methods for resource handling blocks", 0),
        Chunk("x", "two words", 0),  # shorter than one n-gram: nothing to compare, so it is kept
    ]
    hits = [Hit(i, 1.0 - i / 10) for i in range(5)]
    assert [h.index for h in dedupe_hits(hits, cs)] == [0, 3, 4]  # rank order preserved
    assert [h.index for h in dedupe_hits(hits, cs, threshold=1.01)] == [0, 1, 2, 3, 4]  # nothing can reach 101%


def test_dedupe_threshold_is_the_fraction_of_a_chunks_own_shingles_already_seen():
    base = "the finally clause always executes and if it returns the saved exception is discarded here"
    extended = base + " plus a few extra trailing words"  # 12 of its 18 shingles (67%) are repeats; 6 are new
    cs = [Chunk("a", base, 0), Chunk("b", extended, 0)]
    hits = [Hit(0, 1.0), Hit(1, 0.9)]
    assert len(dedupe_hits(hits, cs, threshold=0.8)) == 2  # 33% new content: not a duplicate
    assert len(dedupe_hits(hits, cs, threshold=0.6)) == 1  # a stricter notion of "same" drops it


def test_the_real_corpus_duplicates_passages_across_sections():
    docs = load_corpus()
    ev = norm_ws(next(q for q in QUESTIONS if q.id == "L01").evidence)
    assert {d.id for d in docs if ev in norm_ws(d.text)} == {"try", "compound"}  # the same passage lives in two sections


def test_copies_with_shifted_chunk_boundaries_sit_below_the_apriori_threshold_and_above_adjacent_windows():
    """The measurement behind dedupe's default: same passage in two sections shares 0.75-0.79 of its
    word 4-grams; adjacent sliding windows share ~0.25. A threshold of 0.8 misses the copies."""
    docs = load_corpus()
    chunks = chunk_corpus(docs, lambda d: chunk_words(d, 100, 25))
    hits = BM25Index(chunks).search("saved exception is discarded finally clause return", 5)
    by_doc = {}
    for h in hits:
        by_doc.setdefault(chunks[h.index].doc_id, []).append(h)
    assert len(dedupe_hits(hits, chunks, threshold=0.8)) == len(hits)  # a-priori 0.8: copies survive
    assert len(dedupe_hits(hits, chunks)) < len(hits)  # default 0.6: they are caught
    for doc in by_doc.values():  # adjacent windows of one document are never mistaken for copies
        ids = sorted(h.index for h in doc)
        for a, b in zip(ids, ids[1:]):
            if b == a + 1:
                sa, sb = solution._shingles(chunks[a].text), solution._shingles(chunks[b].text)
                assert len(sa & sb) / len(sa) < 0.4


def test_across_the_eval_set_copies_crowd_the_top_five_and_dedupe_removes_them():
    docs = load_corpus()
    chunks = chunk_corpus(docs, lambda d: chunk_words(d, 100, 25))
    bm25 = BM25Index(chunks)
    redundant = sum(len(h := bm25.search(q.text, 5)) - len(dedupe_hits(h, chunks)) for q in ANSWERABLE)
    assert redundant >= 15  # measured 27 of 180 slots at threshold 0.65 (bm25); this guards against the effect vanishing


# --- prompt assembly ------------------------------------------------------------------------------------------


def test_prompt_respects_budget_orders_citations_and_reports_what_it_dropped():
    cs = chunks_of(*(" ".join(f"w{j}" for j in range(n)) for n in (40, 500, 30, 25)), doc="ref")
    hits = [Hit(i, 1.0) for i in range(4)]
    for budget in (150, 300, 1000):
        p = build_prompt("why?", hits, cs, budget)
        assert approx_tokens(p.text) <= budget
        assert sorted(p.used + p.dropped) == [0, 1, 2, 3]
    p = build_prompt("why?", hits, cs, 300)
    assert 1 in p.dropped and p.used == [0, 2, 3]  # the 500-word chunk did not fit; later small ones still did
    assert "[1] (source: ref)" in p.text and "[3] (source: ref)" in p.text and "[4]" not in p.text
    assert p.text.index("[1]") < p.text.index("[2]") < p.text.index("[3]")


def test_prompt_always_carries_the_grounding_and_abstention_instruction():
    p = build_prompt("why?", [], chunks_of("x"), token_budget=0)
    assert p.used == [] and "ONLY the numbered sources" in p.text and "I don't know" in p.text
    assert p.text.rstrip().endswith("Answer:")


# --- evaluation harness ------------------------------------------------------------------------------------------


class StubRetriever:
    def __init__(self, order_by_question):
        self.order = order_by_question

    def search(self, query, k):
        return [Hit(i, 10.0 - r) for r, i in enumerate(self.order[query][:k])]


def _mini():
    docs = [Document("a", "alpha beta gamma delta epsilon"), Document("b", "the answer is forty two exactly")]
    cs = [Chunk("a", "alpha beta gamma", 0), Chunk("a", "delta epsilon", 1), Chunk("b", "the answer is forty", 0),
          Chunk("b", "two exactly", 1), Chunk("b", "the answer is forty two exactly", 2)]
    return docs, cs


def test_judge_relevance_depends_on_the_span_not_on_chunk_ids():
    docs, cs = _mini()
    q = Question("q", "lexical", "q?", "b", "forty two")
    j = Judge(cs, docs, [q])
    assert j.relevant["q"] == {4}  # only the chunk holding the whole span; 2 and 3 each hold half
    assert j.gold_docs["q"] == {"b"}
    j2 = Judge(cs[:4], docs, [q])  # a chunking where every boundary cuts the span
    assert j2.relevant["q"] == set() and j2.n_split_evidence() == 1


def test_evaluate_computes_ranks_ndcg_and_honors_depth_and_post_steps():
    docs, cs = _mini()
    q = Question("q", "lexical", "Q", "b", "forty two")
    j = Judge(cs, docs, [q])
    (r,) = evaluate(StubRetriever({"Q": [0, 2, 4]}), j, [q], depth=3)
    assert r.first_rank == 3 and r.doc_rank == 2 and r.n_relevant == 1  # chunk 2 is from doc b, chunk 4 has the span
    assert r.ndcg == pytest.approx(1 / math.log2(4))  # one relevant chunk at rank 3; ideal DCG is 1.0
    (r2,) = evaluate(StubRetriever({"Q": [0, 2, 4]}), j, [q], depth=2)
    assert r2.first_rank is None  # depth cut it off
    (r3,) = evaluate(StubRetriever({"Q": [0, 2, 4]}), j, [q], depth=1, fetch=3, post=lambda hs: hs[2:])
    assert r3.first_rank == 1  # the post step (e.g. dedupe) chose what fills the final slots


def test_ndcg_known_value_and_zero_when_nothing_is_relevant():
    assert solution._ndcg([1, 0, 1], total_relevant=2, depth=3) == pytest.approx(1.5 / (1 + 1 / math.log2(3)))
    assert solution._ndcg([0, 0, 0], total_relevant=0, depth=3) == 0.0


def test_summary_metrics_and_per_question_vectors():
    docs, cs = _mini()
    qs = [Question("a", "lexical", "A", "b", "forty two"), Question("b", "lexical", "B", "a", "alpha beta")]
    j = Judge(cs, docs, qs)
    res = evaluate(StubRetriever({"A": [4, 0], "B": [1, 2, 0]}), j, qs, depth=3)
    assert per_question(res, "hit@1") == [1.0, 0.0]
    assert per_question(res, "hit@3") == [1.0, 1.0]
    assert per_question(res, "rr") == [1.0, 1 / 3]
    s = summarize(res)
    assert s["recall@1"] == 0.5 and s["recall@3"] == 1.0 and s["mrr"] == pytest.approx((1 + 1 / 3) / 2)


def test_paired_bootstrap_detects_a_real_gap_and_reports_no_gap_for_identical_systems():
    a = [1.0] * 30 + [0.0] * 6
    assert paired_bootstrap(a, a) == (0.0, 0.0, 0.0)
    better = [1.0] * 30 + [0.0] * 6
    worse = [1.0] * 12 + [0.0] * 24
    d, lo, hi = paired_bootstrap(better, worse)
    assert d == pytest.approx(0.5) and lo > 0  # interval excludes zero
    assert paired_bootstrap(better, worse, seed=3) == paired_bootstrap(better, worse, seed=3)  # reproducible
    tiny_d, tlo, thi = paired_bootstrap([1, 0, 1, 0, 1, 1], [1, 1, 0, 0, 1, 1])
    assert tlo < 0 < thi  # +/- one question of six is indistinguishable from noise


def test_auroc_and_false_accept_rate_by_hand():
    assert auroc([3, 4], [1, 2]) == 1.0 and auroc([1, 2], [3, 4]) == 0.0
    assert auroc([1, 1], [1, 1]) == 0.5  # ties count half: a constant score separates nothing
    # pairs (pos, neg): (2,1)=1  (2,3)=0  (3,1)=1  (3,3)=0.5  ->  2.5 / 4
    assert auroc([2, 3], [1, 3]) == 0.625
    pos = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    # 90% of the 10 positives answered -> threshold is the 9th highest score, 2; negatives >= 2 are 9.5 and 3
    assert false_accept_at_recall(pos, [9.5, 3, 0], recall=0.9) == pytest.approx(2 / 3)
    assert false_accept_at_recall(pos, [0.5, 0.4], recall=0.9) == 0.0
    assert false_accept_at_recall(pos, [10, 10], recall=1.0) == 1.0  # threshold 1: everything passes


def test_a_constant_score_separates_nothing():
    assert auroc([0.0328] * 9, [0.0328] * 4) == 0.5


# --- eval-set integrity (real corpus) ---------------------------------------------------------------------------------


def test_every_gold_span_exists_in_its_topic_and_ids_are_unique():
    docs = {d.id: norm_ws(d.text) for d in load_corpus()}
    assert len({q.id for q in QUESTIONS}) == len(QUESTIONS)
    for q in ANSWERABLE:
        assert q.topic in docs, f"{q.id}: unknown topic {q.topic!r}"
        assert norm_ws(q.evidence) in docs[q.topic], (
            f"{q.id}: evidence not found -- if the interpreter version changed, the language reference text "
            f"changed and these labels are stale (running {sys.version.split()[0]})"
        )
    assert {q.kind for q in QUESTIONS} == {"lexical", "paraphrase", "unanswerable"}
    assert (len(ANSWERABLE), len(UNANSWERABLE)) == (36, 10)
    assert all(q.topic is None and q.evidence is None for q in UNANSWERABLE)


def test_unanswerable_questions_really_are_absent_from_the_corpus():
    text = norm_ws(" ".join(d.text for d in load_corpus())).lower()
    for term in ("gunicorn", "postgres", "rust", "json", "asyncio", "threading", "csv", "argparse", "dataclass", "pathlib"):
        assert term not in text, f"{term!r} appears in the corpus, so a question about it may be answerable"


def test_paraphrase_questions_really_share_less_vocabulary_with_their_answer_than_lexical_ones():
    lex = mean([question_overlap(q) for q in QUESTIONS if q.kind == "lexical"])
    par = mean([question_overlap(q) for q in QUESTIONS if q.kind == "paraphrase"])
    assert lex > par + 0.2, (lex, par)  # measured: 0.39 vs 0.14 -- the labels are measured, not merely asserted


def test_alternate_evidence_spans_exist_in_the_corpus_and_are_limited_to_the_audited_questions():
    text = norm_ws(" ".join(d.text for d in load_corpus()))
    with_alt = [q for q in QUESTIONS if q.alt_evidence]
    assert {q.id for q in with_alt} == {"P03", "P04", "P18"}  # the questions the pooled audit found second answers for
    for q in with_alt:
        for span in q.alt_evidence:
            assert norm_ws(span) in text, f"{q.id}: alternate span not found"
            assert norm_ws(span) != norm_ws(q.evidence)


def test_judge_alternates_widen_relevance_and_can_be_switched_off():
    docs = [Document("a", "alpha beta gamma"), Document("b", "the answer is forty two"), Document("c", "also six times seven")]
    cs = [Chunk("a", "alpha beta gamma", 0), Chunk("b", "the answer is forty two", 0), Chunk("c", "also six times seven", 0)]
    q = Question("q", "paraphrase", "Q", "b", "forty two", ("six times seven",))
    audited, strict = Judge(cs, docs, [q]), Judge(cs, docs, [q], use_alternates=False)
    assert audited.relevant["q"] == {1, 2} and strict.relevant["q"] == {1}
    assert audited.gold_docs["q"] == {"b", "c"} and strict.gold_docs["q"] == {"b"}


def test_default_chunking_leaves_no_evidence_span_cut_in_half():
    docs = load_corpus()
    chunks = chunk_corpus(docs, lambda d: chunk_words(d, 100, 25))
    assert Judge(chunks, docs, QUESTIONS).n_split_evidence() == 0


# --- the central finding, against a real embedding model (skipped unless the model is already cached) -------------------------------


def _model_cached():
    root = os.path.join(os.path.expanduser("~"), ".cache", "daily-code-lab", "fastembed")
    return os.path.isdir(root) and any(os.scandir(root))


@pytest.mark.skipif(not _model_cached(), reason="embedding model not downloaded (run `python3 solution.py` once)")
def test_real_embeddings_beat_keyword_search_on_paraphrased_questions_but_not_on_lexical_ones():
    pytest.importorskip("fastembed")
    docs = load_corpus()
    chunks = chunk_corpus(docs, lambda d: chunk_words(d, 100, 25))
    judge = Judge(chunks, docs, QUESTIONS)
    emb = CachedEmbedder(solution.FastEmbedEmbedder(), solution._cache_path(), "bge-small-en-v1.5")
    dense = evaluate(DenseIndex(chunks, emb), judge, QUESTIONS)
    bm25 = evaluate(BM25Index(chunks), judge, QUESTIONS)

    def r5(res, kind):
        return mean(per_question([r for r in res if r.question.kind == kind], "hit@5"))

    assert r5(dense, "paraphrase") >= r5(bm25, "paraphrase") + 0.15  # measured: 0.67 vs 0.39
    assert r5(bm25, "lexical") >= 0.9 and r5(dense, "lexical") >= 0.9  # on shared vocabulary, keyword search is already fine


@pytest.mark.skipif(not _model_cached(), reason="embedding model not downloaded")
def test_real_model_puts_the_finally_passage_first_for_a_paraphrase_query():
    pytest.importorskip("fastembed")
    cs = chunks_of(
        "If the finally clause executes a return statement, the saved exception is discarded.",
        "str.partition splits at the first occurrence of the separator and returns a 3-tuple.",
        "The nonlocal statement refers to previously bound variables in the nearest enclosing scope.",
    )
    idx = DenseIndex(cs, solution.FastEmbedEmbedder())
    assert idx.matrix.shape == (3, 384)
    assert np.allclose(np.linalg.norm(idx.matrix, axis=1), 1.0, atol=1e-5)
    assert idx.search("what happens to an error when cleanup code returns a value?", 1)[0].index == 0
