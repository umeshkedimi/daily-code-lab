"""Semantic cache for LLM calls: look up by embedding similarity instead of
exact prompt match, so a paraphrase of an already-answered question can
skip the (simulated) expensive call. Both embeddings here are deliberately
built from scratch -- hashed character-trigrams, and a hashed bag-of-words
-- with no trained model and no API, which is also exactly what makes the
measurement below honest: there is nowhere for a "smarter model would have
handled this" excuse to hide.

The actual engineering question: does a cheap, from-scratch lexical
embedding separate TRUE paraphrases (same answer) from NEAR-DUPLICATE-BUT-
DIFFERENT prompts (different answer) well enough that some threshold is
safe to use? Measured directly below, for both embeddings: no. There are
concrete, reproduced-not-asserted cases where a true paraphrase scores
LOWER similarity than a wrong-answer "confuser" pair -- which means no
single threshold can accept the paraphrase while rejecting the confuser,
since accepting the lower score necessarily accepts the higher one too.
See notes.md for the measured numbers and what this implies about semantic
caching in general, not just this toy implementation.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

DIM = 256

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "what", "what's", "which", "who", "whom",
    "of", "in", "on", "to", "me", "tell", "that", "this", "it", "its", "as", "do", "does",
    "i", "you", "your",
}


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _char_trigrams(text: str) -> List[str]:
    padded = f"  {text.lower().strip()}  "
    return [padded[i : i + 3] for i in range(len(padded) - 2)]


def _word_tokens(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _hashed_embedding(tokens: List[str], dim: int) -> List[float]:
    """The hashing trick (Weinberger et al.): each token is hashed to one of
    `dim` buckets with a pseudo-random sign, and the resulting vector is
    L2-normalized. No vocabulary, no training -- but with few tokens per
    text (this corpus has prompts with as few as 2 content tokens), an
    individual hash collision can noticeably perturb the result; see
    notes.md for how much that actually matters here."""
    vec = [0.0] * dim
    for tok in tokens:
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h // dim) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class Embedder(Protocol):
    def embed(self, text: str) -> List[float]: ...


@dataclass
class CharTrigramEmbedder:
    """Surface-level: rewards any shared substrings, including ones that
    happen to carry the whole meaning of a sentence (a digit)."""

    dim: int = DIM

    def embed(self, text: str) -> List[float]:
        return _hashed_embedding(_char_trigrams(text), self.dim)


@dataclass
class WordBagOfWordsEmbedder:
    """Token-level with simple stopword filtering: coarser than character
    trigrams, but still has no notion of synonymy or word order -- two
    sentences with zero shared content words score exactly 0, no matter
    how closely they mean the same thing."""

    dim: int = DIM

    def embed(self, text: str) -> List[float]:
        return _hashed_embedding(_word_tokens(text), self.dim)


# --- the cache itself ---------------------------------------------------------------


@dataclass
class _CacheEntry:
    prompt: str
    embedding: List[float]
    response: str


class SemanticCache:
    """On `query`, embeds the prompt and compares it against every cached
    entry; if the best match's similarity is at least `threshold`, that
    entry's response is returned without calling `llm`. No eviction policy
    -- cache sizing/eviction is a separate, already-covered problem (see
    2026-09-20's LRU cache); this exercise is specifically about whether
    similarity-based lookup is safe at all, not about bounding memory."""

    def __init__(self, llm: Callable[[str], str], embedder: Embedder, threshold: float):
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        self._llm = llm
        self._embedder = embedder
        self.threshold = threshold
        self._entries: List[_CacheEntry] = []
        self.hits = 0
        self.misses = 0

    def query(self, prompt: str) -> Tuple[str, bool]:
        """Returns (response, was_hit)."""
        emb = self._embedder.embed(prompt)
        best_sim, best_entry = -1.0, None
        for entry in self._entries:
            sim = cosine_similarity(emb, entry.embedding)
            if sim > best_sim:
                best_sim, best_entry = sim, entry

        if best_entry is not None and best_sim >= self.threshold:
            self.hits += 1
            return best_entry.response, True

        self.misses += 1
        response = self._llm(prompt)
        self._entries.append(_CacheEntry(prompt, emb, response))
        return response, False


# --- ground truth + simulated "always-correct, but costly" LLM ---------------------


class UnknownPromptError(ValueError):
    pass


_ARITHMETIC_RE = re.compile(r"(-?\d+)\s*\+\s*(-?\d+)")

# canonical question -> (the question text, the true answer)
CANONICAL_QA: Dict[str, Tuple[str, str]] = {
    "capital_france": ("What is the capital of France?", "Paris"),
    "capital_japan": ("What is the capital of Japan?", "Tokyo"),
    "largest_planet": ("What is the largest planet in the solar system?", "Jupiter"),
}

# Each canonical question maps to a tuple of "concept groups"; a prompt
# matches iff at least one synonym from EVERY group is present. This is
# deliberately still just substring matching, not a real intent classifier
# -- but it has to tolerate the heavy paraphrases' wording (which, on
# purpose, avoids the canonical question's own words -- "seat of
# government" instead of "capital") or the ground truth itself couldn't
# recognize its own heavy-paraphrase corpus. Caught directly by this
# exercise's own first demo() run (see Key Learnings).
_CANONICAL_CONCEPT_GROUPS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "capital_france": (("france",), ("capital", "seat of")),
    "capital_japan": (("japan",), ("capital", "seat of")),
    "largest_planet": (("planet",), ("largest", "biggest", "most mass")),
}


def true_answer(prompt: str) -> str:
    """The ground-truth answer for `prompt`, computed directly and
    independently of any cache -- this is what "the real LLM" always
    answers correctly, used to check whether a cache hit returned the
    right thing for THIS prompt, not just whatever got cached earlier
    under a similar-looking one."""
    m = _ARITHMETIC_RE.search(prompt)
    if m:
        return str(int(m.group(1)) + int(m.group(2)))
    lowered = prompt.lower()
    for key, groups in _CANONICAL_CONCEPT_GROUPS.items():
        if all(any(syn in lowered for syn in group) for group in groups):
            return CANONICAL_QA[key][1]
    raise UnknownPromptError(f"no ground truth defined for prompt: {prompt!r}")


class CountingLLM:
    """Simulates calling a real LLM: always correct (via `true_answer`),
    but every call is counted -- that count is the "cost" the cache exists
    to reduce."""

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        return true_answer(prompt)


# --- the corpus: true paraphrases (light and heavy) vs. wrong-answer confusers -----

LIGHT_PARAPHRASES: Dict[str, str] = {
    "capital_france": "What's the capital of France?",
    "capital_japan": "What's the capital of Japan?",
    "largest_planet": "What's the largest planet in the solar system?",
}

HEAVY_PARAPHRASES: Dict[str, str] = {
    "capital_france": "Name the city that serves as France's seat of government.",
    "capital_japan": "Which city serves as the seat of Japan's government?",
    "largest_planet": "Of all the planets orbiting the sun, which one has the most mass?",
}

# Pairs of prompts that differ by a tiny edit (one digit) but have
# DIFFERENT true answers -- the risk case for any similarity-based cache.
CONFUSER_PAIRS: List[Tuple[str, str]] = [
    ("What is 12 + 7?", "What is 12 + 8?"),
    ("What is 100 + 1?", "What is 100 + 2?"),
    ("What is 45 + 10?", "What is 45 + 11?"),
]

# A different-question baseline: same "slot" (both ask for a capital),
# genuinely different answer, for comparison against the confuser pairs.
UNRELATED_SAME_SLOT_PAIRS: List[Tuple[str, str]] = [
    ("What is the capital of France?", "What is the capital of Japan?"),
]


def _query_and_check(cache: SemanticCache, prompt: str) -> Tuple[bool, bool]:
    """Returns (was_hit, was_correct). A hit is only "safe" if it was ALSO
    correct -- a hit that returns the wrong answer (e.g. one heavy
    paraphrase coincidentally matching a different, already-cached heavy
    paraphrase instead of its own canonical question) is the actual risk
    this exercise measures, and must never be counted as a success just
    because `SemanticCache.query` returned `hit=True`."""
    response, hit = cache.query(prompt)
    return hit, response == true_answer(prompt)


def measure_tradeoff(embedder: Embedder, thresholds: Sequence[float]) -> List[dict]:
    """For each threshold: prime the cache with every canonical question,
    then measure light- and heavy-paraphrase hit rate AND correctness
    (a hit can be wrong -- see Key Learnings in notes.md), and the
    wrong-answer rate on the confuser pairs. One fresh cache per
    threshold."""
    results = []
    for threshold in thresholds:
        llm = CountingLLM()
        cache = SemanticCache(llm, embedder, threshold)

        for canonical_q, _ in CANONICAL_QA.values():
            cache.query(canonical_q)

        light_results = [_query_and_check(cache, p) for p in LIGHT_PARAPHRASES.values()]
        heavy_results = [_query_and_check(cache, p) for p in HEAVY_PARAPHRASES.values()]

        confuser_wrong = 0
        for a, b in CONFUSER_PAIRS:
            cache.query(a)
            _, correct_b = _query_and_check(cache, b)
            if not correct_b:
                confuser_wrong += 1

        results.append(
            {
                "threshold": threshold,
                "light_hit_rate": sum(hit for hit, _ in light_results) / len(light_results),
                "light_wrong_rate": sum(not ok for _, ok in light_results) / len(light_results),
                "heavy_hit_rate": sum(hit for hit, _ in heavy_results) / len(heavy_results),
                "heavy_wrong_rate": sum(not ok for _, ok in heavy_results) / len(heavy_results),
                "confuser_wrong_rate": confuser_wrong / len(CONFUSER_PAIRS),
                "llm_calls": llm.calls,
            }
        )
    return results


def demo() -> None:
    thresholds = [0.0, 0.3, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0]
    for name, embedder in [("char-trigram", CharTrigramEmbedder()), ("word-bag-of-words", WordBagOfWordsEmbedder())]:
        print(f"\n{name}:")
        print(
            f"   {'threshold':>9}  {'light hit%':>10}  {'heavy hit%':>10}  {'heavy wrong%':>12}  "
            f"{'confuser wrong%':>15}  {'llm calls':>9}"
        )
        for row in measure_tradeoff(embedder, thresholds):
            print(
                f"   {row['threshold']:>9.2f}  {row['light_hit_rate']*100:>9.0f}%  "
                f"{row['heavy_hit_rate']*100:>9.0f}%  {row['heavy_wrong_rate']*100:>11.0f}%  "
                f"{row['confuser_wrong_rate']*100:>14.0f}%  {row['llm_calls']:>9}"
            )

    print("\nThe core finding: a true (heavy) paraphrase scoring LOWER than a wrong-answer confuser")
    canonical = CANONICAL_QA["capital_france"][0]
    heavy = HEAVY_PARAPHRASES["capital_france"]
    a, b = CONFUSER_PAIRS[0]
    for name, embedder in [("char-trigram", CharTrigramEmbedder()), ("word-bag-of-words", WordBagOfWordsEmbedder())]:
        sim_heavy = cosine_similarity(embedder.embed(canonical), embedder.embed(heavy))
        sim_confuser = cosine_similarity(embedder.embed(a), embedder.embed(b))
        print(f"   {name:>18}: heavy paraphrase sim = {sim_heavy:.3f}   confuser sim = {sim_confuser:.3f}")


if __name__ == "__main__":
    demo()
