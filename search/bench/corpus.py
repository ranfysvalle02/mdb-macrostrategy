"""Deterministic corpus generation for the search angle.

Everything here is reproducible from a single integer seed so that both engines
index byte-for-byte identical data and are scored against the *same* ground
truth. There is no embedding model and no network download: text is sampled from
fixed per-category vocabularies, and vectors are synthetic cluster-structured
embeddings. That keeps the benchmark falsifiable and fast.

Two ground truths are precomputed in pure Python/NumPy so neither database is
asked to grade its own homework:

* Vector: exact brute-force cosine top-k over every document embedding.
* Full-text: a deterministic TF-IDF top-k over the tokenized text.

Recall@k for each engine is measured against these references. The full-text
reference is a *lexical* sanity signal, not a relevance verdict -- Postgres
``ts_rank`` and Atlas Search BM25 rank differently, so an honest reading expects
high-but-not-perfect overlap (see search/README.md).
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass

import numpy as np

# Fixed per-category vocabularies. Documents draw most of their words from their
# own category's pool plus a few shared "stop-ish" connector words, which gives
# lexical search something real to discriminate on.
_CATEGORY_VOCAB: dict[str, list[str]] = {
    "database": [
        "index", "shard", "replica", "query", "schema", "transaction", "vector",
        "document", "cluster", "rollback", "commit", "partition", "aggregate",
    ],
    "gaming": [
        "raid", "loot", "guild", "respawn", "combo", "ranked", "inventory",
        "quest", "arena", "patch", "cooldown", "matchmaking", "leaderboard",
    ],
    "finance": [
        "ledger", "equity", "hedge", "yield", "dividend", "portfolio", "margin",
        "liquidity", "arbitrage", "futures", "spread", "valuation", "coupon",
    ],
    "weather": [
        "monsoon", "humidity", "cyclone", "frost", "drought", "barometer",
        "thunder", "isobar", "windchill", "precipitation", "gale", "overcast",
    ],
    "space": [
        "orbit", "nebula", "thruster", "telescope", "asteroid", "payload",
        "aphelion", "rover", "lander", "ion", "gravity", "spectrometer",
    ],
}
_CATEGORIES = list(_CATEGORY_VOCAB.keys())
_CONNECTORS = ["the", "a", "of", "and", "with", "for", "in", "on", "to"]

_TOKEN_RE = re.compile(r"[a-z]+")


@dataclass
class Corpus:
    """Identical input data for both engines."""

    ids: list[int]
    titles: list[str]
    bodies: list[str]
    tags: list[list[str]]
    categories: list[str]
    embeddings: np.ndarray  # (N, dim) float32, L2-normalized
    doc_tokens: list[set[str]]  # token set of (title + body) per doc, for precision scoring

    def __len__(self) -> int:
        return len(self.ids)

    def records(self) -> list[dict]:
        """Materialize the corpus as plain dicts for bulk loading."""
        return [
            {
                "id": self.ids[i],
                "title": self.titles[i],
                "body": self.bodies[i],
                "tags": self.tags[i],
                "category": self.categories[i],
                "embedding": self.embeddings[i].tolist(),
            }
            for i in range(len(self.ids))
        ]


@dataclass
class QuerySet:
    """Deterministic queries plus the exact ground truth used to score recall."""

    # Vector queries.
    vectors: np.ndarray  # (M, dim) float32, L2-normalized
    vector_truth: list[list[int]]  # exact cosine top-k doc ids per query
    # Text queries.
    texts: list[str]
    text_truth: list[list[int]]  # lexical TF-IDF top-k doc ids per query


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (mat / norms).astype(np.float32)


def build_corpus(corpus_size: int, dim: int, seed: int) -> Corpus:
    """Build a deterministic corpus of ``corpus_size`` documents of dimension ``dim``.

    Vectors are cluster-structured (each document is a noisy draw around one of
    a handful of centers) so that approximate-nearest-neighbor recall is a
    meaningful, non-trivial number rather than noise.
    """
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)

    n_clusters = max(2, min(32, corpus_size // 50 or 2))
    centers = _normalize_rows(nrng.standard_normal((n_clusters, dim)))

    ids: list[int] = []
    titles: list[str] = []
    bodies: list[str] = []
    tags: list[list[str]] = []
    categories: list[str] = []
    raw = np.empty((corpus_size, dim), dtype=np.float32)

    for i in range(corpus_size):
        category = _CATEGORIES[i % len(_CATEGORIES)]
        pool = _CATEGORY_VOCAB[category]

        title_words = rng.sample(pool, k=min(3, len(pool)))
        body_len = rng.randint(20, 40)
        body_words = []
        for _ in range(body_len):
            # Mostly category words, occasionally a connector for realism.
            if rng.random() < 0.2:
                body_words.append(rng.choice(_CONNECTORS))
            else:
                body_words.append(rng.choice(pool))
        doc_tags = rng.sample(pool, k=min(4, len(pool)))

        ids.append(i)
        titles.append(" ".join(title_words))
        bodies.append(" ".join(body_words))
        tags.append(doc_tags)
        categories.append(category)

        cluster = i % n_clusters
        raw[i] = centers[cluster] + 0.35 * nrng.standard_normal(dim)

    doc_tokens = [set(_tokenize(titles[i] + " " + bodies[i])) for i in range(corpus_size)]

    return Corpus(
        ids=ids,
        titles=titles,
        bodies=bodies,
        tags=tags,
        categories=categories,
        embeddings=_normalize_rows(raw),
        doc_tokens=doc_tokens,
    )


def _exact_cosine_topk(embeddings: np.ndarray, query: np.ndarray, k: int) -> list[int]:
    # Unit-normalized rows -> cosine similarity is just the dot product.
    sims = embeddings @ query
    if k >= len(sims):
        order = np.argsort(-sims, kind="stable")
    else:
        # argpartition for the top-k, then sort just those for stable ordering.
        part = np.argpartition(-sims, k)[:k]
        order = part[np.argsort(-sims[part], kind="stable")]
    return [int(i) for i in order[:k]]


def _tfidf_topk(
    counts: list[dict[str, int]], df: dict[str, int], n: int, query_terms: list[str], k: int
) -> list[int]:
    """Deterministic TF-IDF top-k over precomputed per-doc term counts.

    This is a *reference* lexical ranking, not the metric the engines are scored
    on (BM25 and ts_rank diverge from it by design); see search/README.md.
    """
    scores: list[tuple[float, int]] = []
    for i in range(n):
        c = counts[i]
        score = 0.0
        for qt in query_terms:
            tf = c.get(qt, 0)
            if tf:
                score += tf * math.log(n / (1 + df.get(qt, 0)))
        scores.append((score, i))
    # Highest score first; ties broken by ascending doc id for determinism.
    scores.sort(key=lambda s: (-s[0], s[1]))
    return [i for score, i in scores[:k] if score > 0.0]


def build_queries(corpus: Corpus, n_queries: int, k: int, seed: int) -> QuerySet:
    """Build deterministic vector and text queries plus their exact ground truth."""
    rng = random.Random(seed + 7)
    nrng = np.random.default_rng(seed + 7)
    dim = corpus.embeddings.shape[1]
    n = len(corpus)

    # Vector queries: noisy draws around random corpus points so the true
    # neighbors are well-defined and clustered.
    anchors = nrng.integers(0, n, size=n_queries)
    qvecs = corpus.embeddings[anchors] + 0.25 * nrng.standard_normal((n_queries, dim))
    qvecs = _normalize_rows(qvecs)
    vector_truth = [_exact_cosine_topk(corpus.embeddings, qvecs[i], k) for i in range(n_queries)]

    # Precompute per-doc term counts and document frequencies once (cheap).
    counts: list[dict[str, int]] = []
    df: dict[str, int] = {}
    for i in range(n):
        toks = _tokenize(corpus.titles[i] + " " + corpus.bodies[i])
        c: dict[str, int] = {}
        for t in toks:
            c[t] = c.get(t, 0) + 1
        counts.append(c)
        for t in c:
            df[t] = df.get(t, 0) + 1

    # Text queries: a couple of words from a chosen category.
    texts: list[str] = []
    text_truth: list[list[int]] = []
    for _ in range(n_queries):
        category = rng.choice(_CATEGORIES)
        terms = rng.sample(_CATEGORY_VOCAB[category], k=2)
        texts.append(" ".join(terms))
        text_truth.append(_tfidf_topk(counts, df, n, terms, k))

    return QuerySet(
        vectors=qvecs,
        vector_truth=vector_truth,
        texts=texts,
        text_truth=text_truth,
    )


def recall_at_k(returned: list[int], truth: list[int], k: int) -> float:
    """Fraction of the ground-truth top-k that the engine actually returned.

    With an empty ground truth (a text query that matches nothing under the
    lexical reference) recall is defined as 1.0 -- there was nothing to miss.
    """
    truth_k = set(truth[:k])
    if not truth_k:
        return 1.0
    hit = len(truth_k & set(returned[:k]))
    return hit / len(truth_k)


def lexical_precision(returned: list[int], terms: list[str], corpus: Corpus, k: int) -> float:
    """Fraction of returned docs that genuinely contain at least one query term.

    This is the ranker-neutral full-text quality metric. Postgres ``ts_rank`` and
    Atlas Search BM25 *order* lexical matches differently, so comparing their
    top-k against a third ranker (e.g. TF-IDF) measures ranker similarity, not
    correctness. Precision answers the fair question instead: are the documents
    each engine returns actually matches? A correct full-text setup scores ~1.0
    on both engines, leaving latency/throughput as the real comparison.
    """
    top = returned[:k]
    if not top:
        return 0.0
    wanted = set(terms)

    def _matches(doc_id: int) -> bool:
        return 0 <= doc_id < len(corpus) and bool(corpus.doc_tokens[doc_id] & wanted)

    hits = sum(1 for doc_id in top if _matches(doc_id))
    return hits / len(top)
