"""End-to-end tests for the search angle against the real engines.

These require the search docker-compose stack
(``docker compose -f search/docker-compose.yml up -d``) and are marked
``integration`` so the fast unit job can skip them. The CI integration job runs
them with ``-m integration`` after bringing the stack up. The corpus is
intentionally tiny so the round-trip (including the asynchronous Atlas Search
index build) stays quick.
"""

from __future__ import annotations

import pytest

from search.bench import SearchConfig
from search.bench import mongo_search as bench_mongo
from search.bench import pg_search as bench_pg

pytestmark = pytest.mark.integration


def _cfg(mode: str) -> SearchConfig:
    return SearchConfig(
        mode=mode, corpus_size=300, dim=32, k=10, n_queries=25, trials=1,
        pg_hnsw_ef_search=80, mongo_num_candidates=120,
    )


def _assert_sane(result, engine: str, cfg: SearchConfig) -> None:
    assert result.engine == engine
    assert result.mode == cfg.mode
    assert result.n_queries == cfg.n_queries
    assert result.index_build_s >= 0.0
    assert result.qps > 0.0
    assert result.latency_ms["p99"] >= 0.0
    assert 0.0 <= result.recall_at_k <= 1.0
    assert result.extra.get("recall_kind") in ("ann_recall", "lexical_precision")


def test_pg_vector_end_to_end():
    cfg = _cfg("vector")
    r = bench_pg.run(cfg)
    _assert_sane(r, "postgresql", cfg)
    # HNSW over a tiny corpus should find most true neighbors.
    assert r.recall_at_k > 0.5
    assert r.index_size_mb > 0.0


def test_pg_fts_end_to_end():
    cfg = _cfg("fts")
    r = bench_pg.run(cfg)
    _assert_sane(r, "postgresql", cfg)
    assert r.index_size_mb > 0.0
    # Every returned doc should genuinely contain a query term (precision@k).
    assert r.recall_at_k > 0.9


def test_mongo_vector_end_to_end():
    cfg = _cfg("vector")
    r = bench_mongo.run(cfg)
    _assert_sane(r, "mongodb", cfg)
    assert r.recall_at_k > 0.5


def test_mongo_fts_end_to_end():
    cfg = _cfg("fts")
    r = bench_mongo.run(cfg)
    _assert_sane(r, "mongodb", cfg)
    assert r.recall_at_k > 0.9
