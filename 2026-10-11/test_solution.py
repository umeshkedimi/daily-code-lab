import random

import pytest

from solution import (
    CANONICAL_QA,
    CONFUSER_PAIRS,
    HEAVY_PARAPHRASES,
    LIGHT_PARAPHRASES,
    UNRELATED_SAME_SLOT_PAIRS,
    CharTrigramEmbedder,
    CountingLLM,
    SemanticCache,
    UnknownPromptError,
    WordBagOfWordsEmbedder,
    cosine_similarity,
    measure_tradeoff,
    true_answer,
)

EMBEDDERS = [CharTrigramEmbedder(), WordBagOfWordsEmbedder()]


# --- cosine_similarity, on hand-built vectors (no embedder involved) --------------


def test_cosine_similarity_of_identical_vectors_is_one():
    v = [0.6, 0.8]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_of_opposite_vectors_is_negative_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


# --- embedders: basic invariants --------------------------------------------------


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_embedding_is_deterministic(embedder):
    assert embedder.embed("hello world") == embedder.embed("hello world")


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_self_similarity_is_one(embedder):
    text = "What is the capital of France?"
    assert cosine_similarity(embedder.embed(text), embedder.embed(text)) == pytest.approx(1.0)


def test_word_bow_embedding_of_empty_string_is_the_zero_vector():
    emb = WordBagOfWordsEmbedder().embed("")
    assert all(x == 0.0 for x in emb)  # no tokens -> no features -> no divide-by-zero garbage


def test_char_trigram_embedding_of_empty_string_is_not_the_zero_vector():
    # A real, non-obvious difference between the two embedders, not a bug:
    # _char_trigrams pads even "" to "    " and extracts two identical
    # whitespace-only trigrams from the padding, so an empty string still
    # produces a well-defined (degenerate, self-similarity 1.0) vector --
    # word tokenization has no such padding step and correctly yields the
    # zero vector instead.
    emb = CharTrigramEmbedder().embed("")
    assert any(x != 0.0 for x in emb)
    assert cosine_similarity(emb, emb) == pytest.approx(1.0)


# --- ground truth: every corpus prompt must actually have a defined answer -------


@pytest.mark.parametrize(
    "prompt",
    [q for q, _ in CANONICAL_QA.values()]
    + list(LIGHT_PARAPHRASES.values())
    + list(HEAVY_PARAPHRASES.values())
    + [p for pair in CONFUSER_PAIRS for p in pair]
    + [p for pair in UNRELATED_SAME_SLOT_PAIRS for p in pair],
)
def test_every_corpus_prompt_has_a_defined_ground_truth(prompt):
    # Regression test for a real bug hit while building this exercise: the
    # heavy paraphrases deliberately avoid the canonical question's own
    # words (that's the point), which meant the keyword matcher used to
    # recognize them ALSO failed to recognize them, and true_answer()
    # raised UnknownPromptError on its own corpus. Every prompt the rest
    # of this suite exercises must resolve to a real answer.
    true_answer(prompt)  # must not raise


def test_true_answer_handles_arithmetic():
    assert true_answer("What is 12 + 7?") == "19"
    assert true_answer("What is -3 + 5?") == "2"


def test_true_answer_matches_each_canonical_question_to_the_right_answer():
    for canonical_q, answer in CANONICAL_QA.values():
        assert true_answer(canonical_q) == answer


def test_true_answer_raises_on_a_prompt_with_no_defined_ground_truth():
    with pytest.raises(UnknownPromptError):
        true_answer("What's your favorite color?")


# --- CountingLLM -------------------------------------------------------------------


def test_counting_llm_counts_calls_and_is_always_correct():
    llm = CountingLLM()
    assert llm.calls == 0
    for canonical_q, answer in CANONICAL_QA.values():
        assert llm(canonical_q) == answer
    assert llm.calls == len(CANONICAL_QA)


# --- SemanticCache: basic behavior -------------------------------------------------


def test_rejects_invalid_threshold():
    with pytest.raises(ValueError):
        SemanticCache(CountingLLM(), CharTrigramEmbedder(), threshold=-0.1)
    with pytest.raises(ValueError):
        SemanticCache(CountingLLM(), CharTrigramEmbedder(), threshold=1.1)


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_first_query_is_always_a_miss_and_calls_the_llm(embedder):
    llm = CountingLLM()
    cache = SemanticCache(llm, embedder, threshold=0.9)
    response, hit = cache.query("What is the capital of France?")
    assert hit is False
    assert response == "Paris"
    assert llm.calls == 1


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_identical_repeated_query_is_a_hit_and_skips_the_llm(embedder):
    llm = CountingLLM()
    cache = SemanticCache(llm, embedder, threshold=0.9)
    cache.query("What is the capital of France?")
    response, hit = cache.query("What is the capital of France?")
    assert hit is True
    assert response == "Paris"
    assert llm.calls == 1  # the second call did NOT reach the LLM


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_threshold_boundary_is_inclusive(embedder):
    # best_sim >= threshold, not >. Floating-point cosine similarity from
    # this hashing scheme essentially never lands on a round number (even
    # a text compared against itself twice over comes out as
    # 0.9999999999999998/9, not exactly 1.0 -- see notes.md), so the only
    # way to actually exercise this boundary is to force the threshold to
    # the EXACT value `query` will compute, bit for bit.
    llm = CountingLLM()
    cache = SemanticCache(llm, embedder, threshold=0.9)
    prompt = "What is the capital of France?"
    cache.query(prompt)
    cache.threshold = cosine_similarity(embedder.embed(prompt), embedder.embed(prompt))
    _, hit = cache.query(prompt)
    assert hit is True


def test_an_explicit_arithmetic_expression_takes_priority_over_a_coincidental_keyword_match():
    # true_answer checks the arithmetic regex before the canonical keyword
    # groups. This prompt is deliberately ambiguous -- it contains both an
    # arithmetic expression AND the keywords for capital_france -- to make
    # that priority an explicit, tested decision rather than an untested
    # accident of code order that nothing would notice if reversed.
    ambiguous = "What is the capital of France, written as 12 + 7 in some cipher?"
    assert true_answer(ambiguous) == "19"


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_a_tight_threshold_treats_a_light_paraphrase_as_a_hit(embedder):
    llm = CountingLLM()
    cache = SemanticCache(llm, embedder, threshold=0.8)
    cache.query(CANONICAL_QA["capital_france"][0])
    response, hit = cache.query(LIGHT_PARAPHRASES["capital_france"])
    assert hit is True
    assert response == "Paris"


# --- the central finding: measured, not asserted ----------------------------------


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_a_heavy_paraphrase_scores_lower_similarity_than_a_wrong_answer_confuser(embedder):
    # This is the whole point: if this holds, NO threshold can accept the
    # heavy paraphrase (as a hit) while rejecting the confuser pair (as a
    # miss) -- accepting the lower score necessarily also accepts the
    # higher one.
    canonical = CANONICAL_QA["capital_france"][0]
    heavy = HEAVY_PARAPHRASES["capital_france"]
    a, b = CONFUSER_PAIRS[0]
    sim_heavy = cosine_similarity(embedder.embed(canonical), embedder.embed(heavy))
    sim_confuser = cosine_similarity(embedder.embed(a), embedder.embed(b))
    assert sim_heavy < sim_confuser


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_wrong_answer_collision_is_reproducible_at_a_loose_threshold(embedder):
    # Not just a similarity-score comparison -- actually run the cache and
    # get back a wrong answer, end to end.
    llm = CountingLLM()
    cache = SemanticCache(llm, embedder, threshold=0.1)  # loose enough to accept the confuser pair
    a, b = CONFUSER_PAIRS[0]
    cache.query(a)
    response_b, hit_b = cache.query(b)
    assert hit_b is True
    assert response_b != true_answer(b)


def test_heavy_paraphrases_of_different_questions_can_collide_with_each_other():
    # Found while building this exercise, not designed in advance: the
    # France and Japan heavy paraphrases ("...serves as France's seat of
    # government" / "...serves as the seat of Japan's government") share
    # enough generic wording ("city", "serves", "seat", "government") that
    # at a moderate word-bow threshold, the SECOND one queried matches the
    # FIRST one's cached entry instead of its own canonical question --
    # returning the wrong country's capital.
    embedder = WordBagOfWordsEmbedder()
    llm = CountingLLM()
    cache = SemanticCache(llm, embedder, threshold=0.5)
    for canonical_q, _ in CANONICAL_QA.values():
        cache.query(canonical_q)
    cache.query(HEAVY_PARAPHRASES["capital_france"])  # caches under capital_france's answer, "Paris"
    response, hit = cache.query(HEAVY_PARAPHRASES["capital_japan"])
    assert hit is True
    assert response == "Paris"  # wrong -- the true answer is "Tokyo"
    assert response != true_answer(HEAVY_PARAPHRASES["capital_japan"])


@pytest.mark.parametrize("embedder", EMBEDDERS)
def test_no_threshold_is_both_safe_and_beneficial_for_heavy_paraphrases(embedder):
    # The core claim of this exercise, stated as a single falsifiable
    # check across a threshold sweep: no threshold achieves "some heavy
    # paraphrase correctly hits" (a real benefit) simultaneously with
    # "zero wrong answers anywhere" (actually safe).
    thresholds = [i / 100 for i in range(0, 101, 5)]
    results = measure_tradeoff(embedder, thresholds)
    for row in results:
        is_beneficial = row["heavy_hit_rate"] > 0 and row["heavy_wrong_rate"] == 0.0
        is_safe = row["confuser_wrong_rate"] == 0.0 and row["heavy_wrong_rate"] == 0.0
        assert not (is_beneficial and is_safe), f"found a safe AND beneficial threshold: {row}"


# --- randomized robustness: the confuser-pair risk isn't cherry-picked -----------


def test_random_digit_swap_confuser_pairs_are_consistently_high_similarity_for_trigram():
    rng = random.Random(0)
    embedder = CharTrigramEmbedder()
    sims = []
    for _ in range(200):
        x, y = rng.randint(1, 500), rng.randint(1, 50)
        a, b = f"What is {x} + {y}?", f"What is {x} + {y + 1}?"
        sims.append(cosine_similarity(embedder.embed(a), embedder.embed(b)))
    assert min(sims) > 0.70  # consistently high, not a fluke of the hand-picked examples above
    assert sum(sims) / len(sims) > 0.80


def test_random_digit_swap_confuser_pairs_never_land_below_an_unrelated_question_pair():
    # A weaker but embedder-agnostic sanity check: a digit-swap confuser
    # should never look LESS similar than two genuinely unrelated
    # questions -- if it did, that would mean the embedding is somehow
    # picking up on the one differing digit as meaningful, which neither
    # toy embedder here is sophisticated enough to do.
    rng = random.Random(1)
    for embedder in EMBEDDERS:
        unrelated_sim = cosine_similarity(
            *(embedder.embed(p) for p in UNRELATED_SAME_SLOT_PAIRS[0])
        )
        for _ in range(20):
            x, y = rng.randint(1, 500), rng.randint(1, 50)
            a, b = f"What is {x} + {y}?", f"What is {x} + {y + 1}?"
            confuser_sim = cosine_similarity(embedder.embed(a), embedder.embed(b))
            assert confuser_sim >= unrelated_sim - 1e-9
