"""A RAG retrieval pipeline and, more importantly, the harness that measures it.

Pipeline:  documents -> chunk -> index -> retrieve top-k -> (fuse) -> dedupe
           -> pack into a token budget -> grounded prompt with citations.
Measurement: labeled questions, evidence-span relevance, recall@k / MRR /
nDCG, per-question paired-bootstrap confidence intervals, chunking sweeps,
duplicate-crowding analysis, and abstention (unanswerable-question) analysis.

Retrievers: BM25 (from scratch), character n-gram TF-IDF (from scratch), and
dense embeddings behind an `Embedder` interface (real model: bge-small via
fastembed), combined with reciprocal rank fusion. What is NOT here: a
generation model -- no LLM/API key is available in this environment, so
answer *faithfulness* is not evaluated; retrieval quality and prompt
assembly are.
"""

from __future__ import annotations

import hashlib
import heapq
import math
import os
import random
import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple

# ---------------------------------------------------------------------------------
# Corpus and chunking
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Document:
    id: str
    text: str


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    text: str
    index: int  # position within its document


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def load_corpus() -> List[Document]:
    """Python's language reference, as shipped with the interpreter. Real
    technical prose, no files to copy, and not written by me."""
    from pydoc_data.topics import topics

    return [Document(k, v) for k, v in sorted(topics.items())]


def chunk_words(doc: Document, size: int, overlap: int = 0) -> List[Chunk]:
    """Sliding window over whitespace-separated words."""
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("need size >= 1 and 0 <= overlap < size")
    words = doc.text.split()
    if not words:
        return []
    stride, chunks, start = size - overlap, [], 0
    while True:
        chunks.append(Chunk(doc.id, " ".join(words[start : start + size]), len(chunks)))
        if start + size >= len(words):  # this window already reaches the end
            return chunks
        start += stride


def chunk_paragraphs(doc: Document, max_words: int) -> List[Chunk]:
    """Pack whole paragraphs (blank-line separated) up to `max_words`; a
    paragraph longer than the limit is split into plain word windows."""
    if max_words < 1:
        raise ValueError("max_words must be >= 1")
    chunks: List[Chunk] = []
    current: List[str] = []
    count = 0

    def flush() -> None:
        nonlocal current, count
        if current:
            chunks.append(Chunk(doc.id, " ".join(current), len(chunks)))
        current, count = [], 0

    for para in re.split(r"\n\s*\n", doc.text):
        words = para.split()
        if not words:
            continue
        if len(words) > max_words:
            flush()
            for i in range(0, len(words), max_words):
                chunks.append(Chunk(doc.id, " ".join(words[i : i + max_words]), len(chunks)))
            continue
        if count + len(words) > max_words:
            flush()
        current.extend(words)
        count += len(words)
    flush()
    return chunks


def chunk_corpus(docs: Iterable[Document], chunker: Callable[[Document], List[Chunk]]) -> List[Chunk]:
    return [c for d in docs for c in chunker(d)]


# ---------------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------------

_STOP = frozenset(
    "a an and are as at be but by can do does for from how i if in is it its of on or so than that the then "
    "there these this to was what when where which who why will with you your not no".split()
)
_TOKEN_RE = re.compile(r"[a-z0-9_]+")  # underscores kept, so __getattr__ stays one token


def crude_stem(t: str) -> str:
    """Deliberately tiny suffix stripper (not Porter): applied identically to
    documents and queries so raise/raised/raises meet in the middle."""
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 4 and t.endswith("sses"):
        t = t[:-2]
    elif len(t) > 5 and t.endswith("ing"):
        t = t[:-3]
    elif len(t) > 4 and t.endswith("ed"):
        t = t[:-2]
    elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    if len(t) > 3 and t.endswith("e"):
        t = t[:-1]
    return t


def tokenize(text: str, stem: bool = False, stop: bool = True) -> List[str]:
    toks = _TOKEN_RE.findall(text.lower())
    if stop:
        toks = [t for t in toks if t not in _STOP]
    return [crude_stem(t) for t in toks] if stem else toks


# ---------------------------------------------------------------------------------
# Retrievers
# ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    index: int  # index into the chunk list the retriever was built over
    score: float


class Retriever(Protocol):
    def search(self, query: str, k: int) -> List[Hit]: ...


def _top_k(scores: Dict[int, float], k: int) -> List[Hit]:
    # ties broken by lower chunk index so results are deterministic
    best = heapq.nsmallest(k, scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [Hit(i, s) for i, s in best]


class BM25Index:
    """Okapi BM25 over an inverted index."""

    def __init__(self, chunks: Sequence[Chunk], k1: float = 1.2, b: float = 0.75, stem: bool = False):
        self.k1, self.b, self.stem = k1, b, stem
        self.postings: Dict[str, List[Tuple[int, int]]] = {}
        self.doc_len: List[int] = []
        for i, c in enumerate(chunks):
            toks = tokenize(c.text, stem)
            self.doc_len.append(len(toks))
            tf: Dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, n in tf.items():
                self.postings.setdefault(t, []).append((i, n))
        self.n = len(chunks)
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0

    def search(self, query: str, k: int) -> List[Hit]:
        scores: Dict[int, float] = {}
        for term in set(tokenize(query, self.stem)):
            plist = self.postings.get(term)
            if not plist:
                continue
            idf = math.log(1 + (self.n - len(plist) + 0.5) / (len(plist) + 0.5))
            for i, tf in plist:
                norm = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                scores[i] = scores.get(i, 0.0) + idf * tf * (self.k1 + 1) / norm
        return _top_k(scores, k)


class NgramIndex:
    """TF-IDF cosine over character n-grams of word tokens ('#word#' padded).
    A *subword lexical* matcher: it forgives morphology and typos but knows
    nothing about meaning. It is not a semantic model."""

    def __init__(self, chunks: Sequence[Chunk], n_lo: int = 3, n_hi: int = 5):
        self.n_lo, self.n_hi = n_lo, n_hi
        tfs = [self._tf(c.text) for c in chunks]
        df: Dict[str, int] = {}
        for tf in tfs:
            for f in tf:
                df[f] = df.get(f, 0) + 1
        self.n = len(chunks)
        self.idf = {f: math.log((self.n + 1) / (d + 1)) + 1 for f, d in df.items()}
        self.postings: Dict[str, List[Tuple[int, float]]] = {}
        for i, tf in enumerate(tfs):
            weights = {f: (1 + math.log(c)) * self.idf[f] for f, c in tf.items()}
            norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
            for f, w in weights.items():
                self.postings.setdefault(f, []).append((i, w / norm))

    def _tf(self, text: str) -> Dict[str, int]:
        tf: Dict[str, int] = {}
        for tok in tokenize(text):
            padded = f"#{tok}#"
            for n in range(self.n_lo, self.n_hi + 1):
                for j in range(len(padded) - n + 1):
                    g = padded[j : j + n]
                    tf[g] = tf.get(g, 0) + 1
        return tf

    def search(self, query: str, k: int) -> List[Hit]:
        qtf = self._tf(query)
        qw = {f: (1 + math.log(c)) * self.idf[f] for f, c in qtf.items() if f in self.idf}
        norm = math.sqrt(sum(w * w for w in qw.values())) or 1.0
        scores: Dict[int, float] = {}
        for f, w in qw.items():
            for i, dw in self.postings[f]:
                scores[i] = scores.get(i, 0.0) + (w / norm) * dw
        return _top_k(scores, k)


class Embedder(Protocol):
    def embed_documents(self, texts: Sequence[str]): ...  # -> array (n, dim)
    def embed_query(self, text: str): ...  # -> array (dim,)


class HashedBagEmbedder:
    """Toy embedder for tests and mechanics checks: hashed bag-of-words. It
    has no semantic knowledge -- it exists so the dense-retrieval plumbing can
    be tested without downloading a model."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _vec(self, text: str):
        import numpy as np

        v = np.zeros(self.dim, dtype=np.float32)
        for t in tokenize(text):
            v[int(hashlib.md5(t.encode()).hexdigest(), 16) % self.dim] += 1.0
        return v

    def embed_documents(self, texts):
        import numpy as np

        return np.stack([self._vec(t) for t in texts]) if len(texts) else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text):
        return self._vec(text)


class FastEmbedEmbedder:
    """A real embedding model (default BAAI/bge-small-en-v1.5, 384-d, ONNX,
    runs on CPU) via the `fastembed` package. Queries use the model's
    query-side encoding. Note the model's 512-token input limit: longer chunks
    are silently truncated, i.e. the tail of a big chunk is invisible to it."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", cache_dir: Optional[str] = None):
        from fastembed import TextEmbedding

        cache_dir = cache_dir or os.path.join(os.path.expanduser("~"), ".cache", "daily-code-lab", "fastembed")
        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)

    def embed_documents(self, texts):
        import numpy as np

        return np.array(list(self._model.embed(list(texts), batch_size=32)), dtype=np.float32)

    def embed_query(self, text):
        import numpy as np

        return np.array(list(self._model.query_embed([text]))[0], dtype=np.float32)


class CachedEmbedder:
    """Disk-caches document embeddings keyed by sha1(namespace + text), so a
    sweep over chunking configs only ever embeds each distinct text once.

    Saves are atomic (write a temp file, then os.replace) and merge with
    whatever is already on disk, so a concurrent reader never sees a
    half-written file and two processes sharing a cache don't erase each
    other's entries."""

    def __init__(self, inner, path: str, namespace: str):
        self.inner, self.path, self.ns = inner, path, namespace
        self._cache = self._read()

    def _read(self):
        import numpy as np

        if not os.path.exists(self.path):
            return {}
        with np.load(self.path, allow_pickle=False) as z:
            return dict(zip(z["keys"].tolist(), z["vecs"]))

    def _save(self) -> None:
        import numpy as np

        merged = {**self._read(), **self._cache}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.tmp.npz"  # must end in .npz or numpy appends it
        np.savez(tmp, keys=np.array(list(merged)), vecs=np.stack(list(merged.values())))
        os.replace(tmp, self.path)

    def _key(self, text: str) -> str:
        return hashlib.sha1((self.ns + "\0" + text).encode()).hexdigest()

    def embed_documents(self, texts):
        import numpy as np

        keys = [self._key(t) for t in texts]
        missing = list(dict.fromkeys(k for k in keys if k not in self._cache))
        if missing:
            by_key = dict(zip(keys, texts))
            vecs = self.inner.embed_documents([by_key[k] for k in missing])
            self._cache.update(zip(missing, vecs))
            self._save()
        return np.stack([self._cache[k] for k in keys]) if keys else np.zeros((0, 0), np.float32)

    def embed_query(self, text):
        return self.inner.embed_query(text)


class DenseIndex:
    """Cosine similarity over embedding vectors (brute force, exact)."""

    def __init__(self, chunks: Sequence[Chunk], embedder: Embedder):
        import numpy as np

        self.embedder = embedder
        m = np.asarray(embedder.embed_documents([c.text for c in chunks]), dtype=np.float32)
        self.matrix = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)

    def search(self, query: str, k: int) -> List[Hit]:
        import numpy as np

        q = np.asarray(self.embedder.embed_query(query), dtype=np.float32)
        q = q / max(float(np.linalg.norm(q)), 1e-12)
        # numpy 2.0 on macOS raises spurious divide/overflow/invalid FP *flags* from float32 matmul
        # even though every result is finite and matches a float64 recomputation to ~1e-7
        # (verified). Silence the false flags, but keep a real check that fails loudly.
        with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
            sims = self.matrix @ q
        if not np.isfinite(sims).all():
            raise FloatingPointError("non-finite similarity scores")
        top = np.lexsort((np.arange(len(sims)), -sims))[:k]  # score desc, then lower index
        return [Hit(int(i), float(sims[i])) for i in top]


# ---------------------------------------------------------------------------------
# Fusion, de-duplication, prompt assembly
# ---------------------------------------------------------------------------------


def rrf_fuse(rankings: Sequence[Sequence[int]], k: int = 60) -> List[Hit]:
    """Reciprocal rank fusion: score(d) = sum over rankers of 1/(k + rank).
    Uses only ranks, so rankers with incomparable score scales (BM25 is
    unbounded, cosine is in [-1, 1]) can be combined without calibration."""
    scores: Dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    return [Hit(i, s) for i, s in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


class HybridRetriever:
    def __init__(self, retrievers: Sequence[Retriever], rrf_k: int = 60, depth: int = 50):
        self.retrievers, self.rrf_k, self.depth = list(retrievers), rrf_k, depth

    def search(self, query: str, k: int) -> List[Hit]:
        rankings = [[h.index for h in r.search(query, self.depth)] for r in self.retrievers]
        return rrf_fuse(rankings, self.rrf_k)[:k]


def _shingles(text: str, n: int = 4) -> Set[Tuple[str, ...]]:
    w = re.findall(r"\w+", text.lower())
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def dedupe_hits(hits: Sequence[Hit], chunks: Sequence[Chunk], threshold: float = 0.6, n: int = 4) -> List[Hit]:
    """Drop a hit when >= `threshold` of its word n-grams already appear in
    higher-ranked kept hits. Catches both overlapping windows of one document
    and the same passage duplicated across documents.

    Choosing the threshold: adjacent 25%-overlap windows share ~0.25 of their
    n-grams, while the same passage copied into another section -- with chunk
    boundaries shifted -- measured 0.75-0.79. So it must sit between them; 0.6
    is roughly that midpoint. (An a-priori 0.8 missed the copies entirely.)"""
    kept: List[Hit] = []
    seen: Set[Tuple[str, ...]] = set()
    for h in hits:
        sh = _shingles(chunks[h.index].text, n)
        if sh and len(sh & seen) / len(sh) >= threshold:
            continue
        kept.append(h)
        seen |= sh
    return kept


def approx_tokens(text: str) -> int:
    """~1.3 tokens per word. An approximation -- no tokenizer is available here."""
    return math.ceil(len(text.split()) * 1.3)


@dataclass
class Prompt:
    text: str
    used: List[int]  # chunk indices included, in citation order [1], [2], ...
    dropped: List[int]  # chunk indices that did not fit the budget


_INSTRUCTIONS = (
    "Answer the question using ONLY the numbered sources below. Cite the sources you use like [1]. "
    'If the sources do not contain the answer, reply exactly: "I don\'t know based on the provided sources."'
)


def build_prompt(question: str, hits: Sequence[Hit], chunks: Sequence[Chunk], token_budget: int) -> Prompt:
    """Pack hits in rank order until the token budget is exhausted. A hit that
    does not fit is skipped and later, smaller ones may still be tried."""
    used: List[int] = []
    dropped: List[int] = []
    parts: List[str] = []
    remaining = token_budget - approx_tokens(_INSTRUCTIONS) - approx_tokens(question) - 8
    for h in hits:
        c = chunks[h.index]
        block = f"[{len(used) + 1}] (source: {c.doc_id})\n{c.text}"
        cost = approx_tokens(block)
        if cost <= remaining:
            used.append(h.index)
            parts.append(block)
            remaining -= cost
        else:
            dropped.append(h.index)
    text = f"{_INSTRUCTIONS}\n\nSources:\n" + "\n\n".join(parts) + f"\n\nQuestion: {question}\nAnswer:"
    return Prompt(text, used, dropped)


# ---------------------------------------------------------------------------------
# Evaluation harness
# ---------------------------------------------------------------------------------


class Judge:
    """Decides relevance by evidence span, not chunk id: a chunk is relevant
    to a question iff it contains one of the question's evidence spans (the
    primary one, plus audited alternates unless `use_alternates=False`). So
    the same labels stay valid under any chunking. A span cut in two by a
    chunk boundary is contained by no chunk -- the pipeline genuinely cannot
    surface it -- and shows up as `n_relevant == 0`."""

    def __init__(self, chunks: Sequence[Chunk], docs: Sequence[Document], questions: Sequence, use_alternates: bool = True):
        self.chunks = list(chunks)
        norm_chunks = [norm_ws(c.text) for c in chunks]
        norm_docs = [(d.id, norm_ws(d.text)) for d in docs]
        self.relevant: Dict[str, Set[int]] = {}
        self.gold_docs: Dict[str, Set[str]] = {}
        for q in questions:
            if q.evidence is None:
                continue
            spans = [norm_ws(q.evidence)] + ([norm_ws(a) for a in q.alt_evidence] if use_alternates else [])
            self.relevant[q.id] = {i for i, t in enumerate(norm_chunks) if any(sp in t for sp in spans)}
            self.gold_docs[q.id] = {d for d, t in norm_docs if any(sp in t for sp in spans)}

    def n_split_evidence(self) -> int:
        """Questions whose evidence no chunk contains in full (cut by boundaries)."""
        return sum(1 for rel in self.relevant.values() if not rel)


@dataclass
class QResult:
    question: object
    first_rank: Optional[int]  # 1-based rank of the first chunk containing the evidence
    doc_rank: Optional[int]  # rank of the first chunk from any document containing the evidence
    ndcg: float
    n_relevant: int  # chunks in the whole index that contain the evidence
    top: List[int]  # retrieved chunk indices, best first
    top_score: float


def _ndcg(rels: Sequence[int], total_relevant: int, depth: int) -> float:
    ideal = sum(1 / math.log2(i + 2) for i in range(min(total_relevant, depth)))
    if ideal == 0:
        return 0.0
    return sum(r / math.log2(i + 2) for i, r in enumerate(rels[:depth])) / ideal


def evaluate(
    retriever: Retriever,
    judge: Judge,
    questions: Sequence,
    depth: int = 10,
    fetch: Optional[int] = None,
    post: Optional[Callable[[List[Hit]], List[Hit]]] = None,
) -> List[QResult]:
    """Run answerable questions. `fetch` > `depth` plus a `post` step (e.g.
    dedupe) lets a post-retrieval stage choose what fills the final `depth` slots."""
    out: List[QResult] = []
    for q in questions:
        if q.evidence is None:
            continue
        hits = retriever.search(q.text, fetch or depth)
        if post:
            hits = post(hits)
        hits = hits[:depth]
        relevant = judge.relevant[q.id]
        rels = [1 if h.index in relevant else 0 for h in hits]
        first = next((r + 1 for r, v in enumerate(rels) if v), None)
        doc_rank = next(
            (r + 1 for r, h in enumerate(hits) if judge.chunks[h.index].doc_id in judge.gold_docs[q.id]), None
        )
        out.append(
            QResult(q, first, doc_rank, _ndcg(rels, len(relevant), depth), len(relevant),
                    [h.index for h in hits], hits[0].score if hits else 0.0)
        )
    return out


def top1_scores(retriever: Retriever, questions: Sequence) -> List[float]:
    scores = []
    for q in questions:
        hits = retriever.search(q.text, 1)
        scores.append(hits[0].score if hits else 0.0)
    return scores


def per_question(results: Sequence[QResult], metric: str) -> List[float]:
    """Per-question values, for paired comparisons. metric: 'hit@K', 'dochit@K', 'rr', 'ndcg'."""
    if metric.startswith("hit@"):
        k = int(metric[4:])
        return [1.0 if r.first_rank is not None and r.first_rank <= k else 0.0 for r in results]
    if metric.startswith("dochit@"):
        k = int(metric[7:])
        return [1.0 if r.doc_rank is not None and r.doc_rank <= k else 0.0 for r in results]
    if metric == "rr":
        return [1.0 / r.first_rank if r.first_rank else 0.0 for r in results]
    if metric == "ndcg":
        return [r.ndcg for r in results]
    raise ValueError(metric)


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def summarize(results: Sequence[QResult], ks: Sequence[int] = (1, 3, 5, 10)) -> Dict[str, float]:
    s = {f"recall@{k}": mean(per_question(results, f"hit@{k}")) for k in ks}
    s.update({f"doc_recall@{k}": mean(per_question(results, f"dochit@{k}")) for k in ks})
    s["mrr"] = mean(per_question(results, "rr"))
    s["ndcg"] = mean(per_question(results, "ndcg"))
    return s


def bootstrap_ci(values: Sequence[float], n_boot: int = 4000, seed: int = 0, alpha: float = 0.05):
    rng = random.Random(seed)
    n = len(values)
    means = sorted(mean([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    return mean(values), means[int(alpha / 2 * n_boot)], means[int((1 - alpha / 2) * n_boot) - 1]


def paired_bootstrap(a: Sequence[float], b: Sequence[float], n_boot: int = 4000, seed: int = 0, alpha: float = 0.05):
    """(mean(a-b), lo, hi): 95% percentile CI of the mean per-question
    difference, resampling questions with replacement. If the interval
    contains 0, the data cannot distinguish the two systems."""
    return bootstrap_ci([x - y for x, y in zip(a, b)], n_boot, seed, alpha)


def auroc(positive: Sequence[float], negative: Sequence[float]) -> float:
    """P(random positive scores higher than random negative); ties count half.
    0.5 = no separation. Scale-free, so it compares retrievers whose raw
    scores are not comparable."""
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def false_accept_at_recall(positive: Sequence[float], negative: Sequence[float], recall: float = 0.9) -> float:
    """Set the accept-threshold so `recall` of answerable questions are still
    answered; return the fraction of unanswerable ones that get answered too."""
    ordered = sorted(positive, reverse=True)
    threshold = ordered[max(math.ceil(recall * len(ordered)) - 1, 0)]
    return sum(1 for s in negative if s >= threshold) / len(negative)


def question_overlap(q) -> float:
    """Fraction of the question's content words (stemmed) that appear in its
    gold evidence span -- a measured check on how 'lexical' vs 'paraphrase' a
    question really is."""
    qt = set(tokenize(q.text, stem=True))
    et = set(tokenize(q.evidence or "", stem=True))
    return len(qt & et) / len(qt) if qt else 0.0


# ---------------------------------------------------------------------------------
# Demo: measure, don't assert
# ---------------------------------------------------------------------------------

_DEFAULT_CHUNK = (100, 25)  # words, overlap


def _cache_path() -> str:
    root = os.environ.get("RAG_CACHE_DIR") or os.path.join(os.path.expanduser("~"), ".cache", "daily-code-lab", "rag")
    return os.path.join(root, "bge-small-en-v1.5.npz")


def _make_embedder() -> Optional[Embedder]:
    try:
        return CachedEmbedder(FastEmbedEmbedder(), _cache_path(), "bge-small-en-v1.5")
    except Exception as exc:  # fastembed/numpy missing or model unavailable: the demo still runs lexically
        print(f"   (dense retrieval unavailable: {type(exc).__name__}: {exc})")
        return None


def _systems(chunks: Sequence[Chunk], embedder: Optional[Embedder]) -> Dict[str, Retriever]:
    bm25 = BM25Index(chunks)
    systems: Dict[str, Retriever] = {"bm25": bm25, "bm25+stem": BM25Index(chunks, stem=True), "ngram": NgramIndex(chunks)}
    if embedder is not None:
        dense = DenseIndex(chunks, embedder)
        systems["dense (bge-small)"] = dense
        systems["bm25+dense (RRF)"] = HybridRetriever([bm25, dense])
        systems["bm25+ngram+dense"] = HybridRetriever([bm25, systems["ngram"], dense])
    return systems


def _kind(results: Sequence[QResult], kind: str) -> List[QResult]:
    return [r for r in results if r.question.kind == kind]


def demo() -> None:
    import time

    from eval_set import ANSWERABLE, QUESTIONS, UNANSWERABLE

    docs = load_corpus()
    words = sum(len(d.text.split()) for d in docs)
    print(f"Corpus: Python language reference, {len(docs)} sections, {words:,} words")
    print(f"Eval set: {len(ANSWERABLE)} answerable ({sum(q.kind == 'lexical' for q in QUESTIONS)} lexical, "
          f"{sum(q.kind == 'paraphrase' for q in QUESTIONS)} paraphrase) + {len(UNANSWERABLE)} unanswerable; "
          f"{sum(bool(q.alt_evidence) for q in QUESTIONS)} questions carry an audited alternate answer span")
    for kind in ("lexical", "paraphrase"):
        ov = [question_overlap(q) for q in QUESTIONS if q.kind == kind]
        print(f"  measured overlap of question words with the answer span: {kind:10} mean {mean(ov):.2f}")

    embedder = _make_embedder()
    size, ov = _DEFAULT_CHUNK
    chunks = chunk_corpus(docs, lambda d: chunk_words(d, size, ov))
    judge = Judge(chunks, docs, QUESTIONS)  # audited labels (primary + alternate spans)
    strict = Judge(chunks, docs, QUESTIONS, use_alternates=False)
    t = time.time()
    systems = _systems(chunks, embedder)
    print(f"\nDefault chunking: {size} words, {ov} overlap -> {len(chunks)} chunks (indexes built in {time.time() - t:.1f}s)")

    print("\n1) Retriever comparison, audited labels (hit = a top-k chunk contains an answer span)")
    print(f"   {'system':20} {'R@1':>5} {'R@5':>5} {'R@10':>5} {'MRR':>5} {'nDCG':>5} | {'lexical R@5':>11} {'paraphrase R@5':>14} | {'R@5 strict labels':>17}")
    results: Dict[str, List[QResult]] = {}
    for name, r in systems.items():
        results[name] = evaluate(r, judge, QUESTIONS)
        s_ = summarize(results[name])
        lex = mean(per_question(_kind(results[name], "lexical"), "hit@5"))
        par = mean(per_question(_kind(results[name], "paraphrase"), "hit@5"))
        strict_r5 = mean(per_question(evaluate(r, strict, QUESTIONS), "hit@5"))
        print(f"   {name:20} {s_['recall@1']:>5.2f} {s_['recall@5']:>5.2f} {s_['recall@10']:>5.2f} {s_['mrr']:>5.2f} {s_['ndcg']:>5.2f} | {lex:>11.2f} {par:>14.2f} | {strict_r5:>17.2f}")

    if "dense (bge-small)" in results:
        print("\n   Is any difference real, or noise from only 36 questions? (paired bootstrap, 95% CI of the difference in R@5)")
        D = "dense (bge-small)"
        pairs = [(D, "bm25", None), ("bm25+dense (RRF)", D, None), (D, "bm25", "paraphrase"), (D, "bm25", "lexical")]
        for a_name, b, kind in pairs:
            ra, rb = results[a_name], results[b]
            if kind:
                ra, rb = _kind(ra, kind), _kind(rb, kind)
            d, lo, hi = paired_bootstrap(per_question(ra, "hit@5"), per_question(rb, "hit@5"))
            verdict = "distinguishable" if lo > 0 or hi < 0 else "cannot tell apart"
            label = f"{a_name} - {b}" + (f" [{kind} only, n={len(ra)}]" if kind else f" [n={len(ra)}]")
            print(f"   {label:62} {d:+.2f}  [{lo:+.2f}, {hi:+.2f}]  {verdict}")

    print("\n2) Chunking sweep at an EQUAL CONTEXT BUDGET (~600 words of retrieved text: k = 600 / avg chunk words)")
    print("   R@5 alone is confounded: five 345-word chunks hand the reader ~12x more text than five 30-word ones.")
    print(f"   {'chunking':22} {'chunks':>6} {'avg w':>6} {'split':>5} | {'R@5 bm25':>8} {'dense':>6} {'hybrid':>6} | {'k':>3} {'R@k bm25':>8} {'dense':>6} {'hybrid':>6} | {'dense doc-R@5':>13}")
    configs = [(s_, o_, "words") for s_ in (30, 60, 120, 240, 480) for o_ in (0, s_ // 4)] + [(120, 0, "para")]
    for s_, o_, kind in configs:
        chunker = (lambda d, s_=s_, o_=o_: chunk_words(d, s_, o_)) if kind == "words" else (lambda d, s_=s_: chunk_paragraphs(d, s_))
        cs = chunk_corpus(docs, chunker)
        j = Judge(cs, docs, QUESTIONS)
        avg_w = mean([len(c.text.split()) for c in cs])
        k = max(1, round(600 / avg_w))
        depth = max(5, k)
        bm = BM25Index(cs)
        rb = evaluate(bm, j, QUESTIONS, depth=depth)
        row = {"bm25": (mean(per_question(rb, "hit@5")), mean(per_question(rb, f"hit@{k}")))}
        if embedder is not None:
            dn = DenseIndex(cs, embedder)
            rd = evaluate(dn, j, QUESTIONS, depth=depth)
            rh = evaluate(HybridRetriever([bm, dn]), j, QUESTIONS, depth=depth)
            row["dense"] = (mean(per_question(rd, "hit@5")), mean(per_question(rd, f"hit@{k}")))
            row["hybrid"] = (mean(per_question(rh, "hit@5")), mean(per_question(rh, f"hit@{k}")))
            row["doc"] = mean(per_question(rd, "dochit@5"))
        label = f"{kind} {s_}w, overlap {o_}" if kind == "words" else f"paragraphs <= {s_}w"
        f2 = lambda key, i: f"{row[key][i]:>6.2f}" if key in row else "   n/a"
        doc_ = f"{row['doc']:>13.2f}" if "doc" in row else "          n/a"
        print(f"   {label:22} {len(cs):>6} {avg_w:>6.0f} {j.n_split_evidence():>5} | {row['bm25'][0]:>8.2f} {f2('dense', 0)} {f2('hybrid', 0)} | {k:>3} {row['bm25'][1]:>8.2f} {f2('dense', 1)} {f2('hybrid', 1)} | {doc_}")
    print("   (split = questions whose evidence span is cut in half by every chunk boundary; one question = 0.028)")

    print("\n3) Duplicate crowding: the reference repeats passages across sections; do copies fill the top-5?")
    print("   Adjacent 25%-overlap windows share ~0.25 of their word 4-grams; a copied passage with shifted chunk")
    print("   boundaries shares ~0.75. The dedupe threshold has to sit between. The whole curve, not one pick:")
    hyb = systems.get("bm25+dense (RRF)", systems["bm25"])
    r_plain = evaluate(hyb, judge, QUESTIONS, depth=5)
    print(f"   {'threshold':>9} {'redundant top-5 slots (of 180)':>32} {'R@5 after fetch-25+dedupe':>27} {'gain vs no dedupe (95% CI)':>30}")
    print(f"   {'(none)':>9} {'-':>32} {mean(per_question(r_plain, 'hit@5')):>27.2f} {'-':>30}")
    gains = []
    for th in (0.5, 0.6, 0.8, 0.95):
        redundant = sum(len(h := hyb.search(q.text, 5)) - len(dedupe_hits(h, chunks, threshold=th)) for q in ANSWERABLE)
        r_dedup = evaluate(hyb, judge, QUESTIONS, depth=5, fetch=25, post=lambda h, th=th: dedupe_hits(h, chunks, threshold=th))
        d, lo, hi = paired_bootstrap(per_question(r_dedup, "hit@5"), per_question(r_plain, "hit@5"))
        gains.append(round(d, 4))
        print(f"   {th:>9} {redundant:>32} {mean(per_question(r_dedup, 'hit@5')):>27.2f} {d:>+14.2f} [{lo:+.2f}, {hi:+.2f}]")
    if len(set(gains)) == 1:
        print("   The recall gain is identical at every threshold: the choice of threshold changes how many slots are")
        print("   called redundant, but not which questions get fixed, so the default is not inflating the gain.")
    else:
        print("   The gain varies with the threshold, and the default (0.6) was chosen after seeing these same")
        print("   questions' retrievals, so treat its gain as optimistic.")

    print("\n4) Abstention: can a score threshold tell answerable from unanswerable questions? (10 unanswerable: 1 question = 10%)")
    print(f"   {'system':20} {'AUROC':>6} {'AUROC(near-domain only)':>24} {'false-accept @90% answered':>28}")
    near = [q for q in UNANSWERABLE if q.id in {"U04", "U05", "U06", "U07", "U08", "U09", "U10"}]
    for name, r in systems.items():
        pos = top1_scores(r, ANSWERABLE)
        neg, neg_near = top1_scores(r, UNANSWERABLE), top1_scores(r, near)
        print(f"   {name:20} {auroc(pos, neg):>6.2f} {auroc(pos, neg_near):>24.2f} {false_accept_at_recall(pos, neg):>28.0%}")

    print("\n5) Where the keyword retriever fails and dense succeeds (audited labels, R@5)")
    if "dense (bge-small)" in results:
        b_miss = {r.question.id for r in results["bm25"] if not (r.first_rank and r.first_rank <= 5)}
        d_miss = {r.question.id for r in results["dense (bge-small)"] if not (r.first_rank and r.first_rank <= 5)}
        print(f"   bm25 misses {len(b_miss)}; dense misses {len(d_miss)}; fixed by dense: {sorted(b_miss - d_miss)}; broken by dense: {sorted(d_miss - b_miss)}")
        ex = next(q for q in QUESTIONS if q.id == "P15")
        print(f"   {ex.id} {ex.text!r}")
        for name in ("bm25", "dense (bge-small)"):
            top = systems[name].search(ex.text, 1)[0]
            print(f"     {name:18} -> [{chunks[top.index].doc_id}] {chunks[top.index].text[:95]}...")

    print("\n6) Grounded prompt (hybrid+dedupe, 350-token budget)")
    q = next(q for q in QUESTIONS if q.id == "P01")
    hits = dedupe_hits(hyb.search(q.text, 10), chunks)
    p = build_prompt(q.text, hits, chunks, token_budget=350)
    print(f"   packed {len(p.used)} chunk(s), dropped {len(p.dropped)} that did not fit; ~{approx_tokens(p.text)} tokens")
    print("   " + p.text[:520].replace("\n", "\n   ") + "\n   ...")


if __name__ == "__main__":
    demo()
