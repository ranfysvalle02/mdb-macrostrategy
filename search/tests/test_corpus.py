"""Unit tests for the deterministic search corpus and ground truth.

No database required. These guard the two properties the whole search benchmark
leans on: the corpus/queries are reproducible from the seed, and the recall
ground truth is actually correct.
"""

from __future__ import annotations

import numpy as np
import pytest

from search.bench.common import (
    SearchResult,
    aggregate_search_trials,
    search_compatibility_warnings,
)
from search.bench.corpus import (
    build_corpus,
    build_queries,
    lexical_precision,
    recall_at_k,
)

# --------------------------------------------------------------------------- #
# Corpus determinism + shape
# --------------------------------------------------------------------------- #

def test_corpus_is_deterministic_for_equal_seed():
    a = build_corpus(corpus_size=200, dim=32, seed=99)
    b = build_corpus(corpus_size=200, dim=32, seed=99)
    assert a.ids == b.ids
    assert a.bodies == b.bodies
    assert np.array_equal(a.embeddings, b.embeddings)


def test_corpus_shape_and_normalization():
    c = build_corpus(corpus_size=120, dim=16, seed=1)
    assert len(c) == 120
    assert c.embeddings.shape == (120, 16)
    # Rows are L2-normalized -> norms are ~1.
    norms = np.linalg.norm(c.embeddings, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_records_roundtrip_fields():
    c = build_corpus(corpus_size=10, dim=8, seed=3)
    recs = c.records()
    assert len(recs) == 10
    assert set(recs[0]) == {"id", "title", "body", "tags", "category", "embedding"}
    assert len(recs[0]["embedding"]) == 8


# --------------------------------------------------------------------------- #
# Queries + ground truth correctness
# --------------------------------------------------------------------------- #

def test_vector_ground_truth_matches_bruteforce():
    c = build_corpus(corpus_size=300, dim=24, seed=7)
    q = build_queries(c, n_queries=15, k=10, seed=7)
    assert q.vectors.shape == (15, 24)
    # Independently recompute exact cosine top-1 and confirm it matches truth[0].
    for i in range(15):
        sims = c.embeddings @ q.vectors[i]
        assert int(np.argmax(sims)) == q.vector_truth[i][0]


def test_vector_self_recall_is_perfect_when_query_equals_doc():
    c = build_corpus(corpus_size=200, dim=16, seed=5)
    # A query identical to a document must have that document as its top-1.
    sims = c.embeddings @ c.embeddings[42]
    assert int(np.argmax(sims)) == 42


def test_text_ground_truth_docs_contain_query_terms():
    c = build_corpus(corpus_size=300, dim=16, seed=11)
    q = build_queries(c, n_queries=20, k=10, seed=11)
    for qi, text in enumerate(q.texts):
        terms = text.split()
        for doc_id in q.text_truth[qi]:
            doc_text = (c.titles[doc_id] + " " + c.bodies[doc_id]).lower()
            assert any(t in doc_text for t in terms)


# --------------------------------------------------------------------------- #
# recall_at_k
# --------------------------------------------------------------------------- #

def test_recall_perfect():
    assert recall_at_k([1, 2, 3], [1, 2, 3], 3) == pytest.approx(1.0)


def test_recall_partial():
    assert recall_at_k([1, 9, 8], [1, 2, 3], 3) == pytest.approx(1 / 3)


def test_recall_empty_truth_is_one():
    assert recall_at_k([1, 2], [], 3) == 1.0


def test_recall_ignores_order_within_k():
    assert recall_at_k([3, 2, 1], [1, 2, 3], 3) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# lexical_precision
# --------------------------------------------------------------------------- #

def test_lexical_precision_all_match():
    c = build_corpus(corpus_size=120, dim=8, seed=2)
    # Pick a doc and use one of its own tokens as the query term.
    doc_id = 0
    term = next(iter(c.doc_tokens[doc_id]))
    # A returned list of docs that all contain `term` -> precision 1.0.
    matching = [i for i in range(len(c)) if term in c.doc_tokens[i]][:5]
    assert lexical_precision(matching, [term], c, 5) == pytest.approx(1.0)


def test_lexical_precision_partial():
    c = build_corpus(corpus_size=120, dim=8, seed=2)
    term = next(iter(c.doc_tokens[0]))
    matching = [i for i in range(len(c)) if term in c.doc_tokens[i]]
    non_matching = [i for i in range(len(c)) if term not in c.doc_tokens[i]]
    # One match, one miss -> precision 0.5.
    returned = [matching[0], non_matching[0]]
    assert lexical_precision(returned, [term], c, 2) == pytest.approx(0.5)


def test_lexical_precision_empty_returned_is_zero():
    c = build_corpus(corpus_size=50, dim=8, seed=2)
    assert lexical_precision([], ["x"], c, 5) == 0.0


# --------------------------------------------------------------------------- #
# aggregate_search_trials + compatibility warnings
# --------------------------------------------------------------------------- #

def _sr(p99: float, recall: float, build: float, qps: float) -> SearchResult:
    return SearchResult(
        engine="mongodb", mode="vector", label="m",
        corpus_size=100, dim=8, k=10, n_queries=10,
        index_build_s=build, index_size_mb=1.0,
        latency_ms={"mean": p99, "p50": p99, "p95": p99, "p99": p99, "max": p99},
        recall_at_k=recall, qps=qps, extra={"recall_kind": "ann_recall"},
    )


def test_aggregate_search_trials_means_and_variance():
    agg = aggregate_search_trials([_sr(1.0, 0.9, 2.0, 100.0), _sr(3.0, 1.0, 4.0, 200.0)])
    assert agg.recall_at_k == pytest.approx(0.95)
    assert agg.index_build_s == pytest.approx(3.0)
    assert agg.qps == pytest.approx(150.0)
    t = agg.extra["trials"]
    assert t["n"] == 2
    assert t["recall_at_k"]["mean"] == pytest.approx(0.95)


def test_aggregate_search_trials_single_passthrough():
    r = _sr(1.0, 0.9, 2.0, 100.0)
    assert aggregate_search_trials([r]) is r


def test_search_compatibility_warns_on_shape_mismatch():
    a = {"mode": "vector", "corpus_size": 100, "dim": 8, "k": 10, "n_queries": 10, "slug": "a"}
    b = {"mode": "vector", "corpus_size": 200, "dim": 8, "k": 10, "n_queries": 10, "slug": "b"}
    assert search_compatibility_warnings([a, b])
    assert search_compatibility_warnings([a, a]) == []
