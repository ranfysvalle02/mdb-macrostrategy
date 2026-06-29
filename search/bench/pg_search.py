"""PostgreSQL search core: full-text (tsvector + GIN) and vector (pgvector + HNSW).

Entrypoint: ``run(config, progress) -> SearchResult``. Imported by the
`demo_psql_search.py` CLI and the search FastAPI app so they share one
implementation.

The point of the search angle is that both capabilities live on the *same row*:
one ``corpus`` table holds the text and the embedding, and Postgres indexes
either with built-in full-text (``tsvector``) or the ``pgvector`` extension. We
measure query latency, recall@k against the precomputed ground truth, index
build time, and index size.
"""

from __future__ import annotations

from typing import Any

import psycopg
from pgvector.psycopg import register_vector

from search.bench.common import (
    PG_TABLE,
    SearchConfig,
    SearchResult,
    aggregate_search_trials,
)
from search.bench.corpus import (
    build_corpus,
    build_queries,
    lexical_precision,
    recall_at_k,
)
from shared.common import ProgressFn, _emit, now, summarize_latencies

_BYTES_PER_MB = 1024 * 1024
_HNSW_INDEX = f"{PG_TABLE}_hnsw"
_GIN_INDEX = f"{PG_TABLE}_gin"


def _preflight(dsn: str) -> None:
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        raise RuntimeError(
            f"Cannot reach Postgres at {dsn!r}. Start the search stack with: "
            "docker compose -f search/docker-compose.yml up -d"
        ) from exc


def _server_version(conn: psycopg.Connection) -> str:
    return conn.execute("SHOW server_version").fetchone()[0].split(" ")[0]


def _pgvector_version(conn: psycopg.Connection) -> str:
    row = conn.execute(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
    ).fetchone()
    return row[0] if row else "unknown"


def _index_size_mb(conn: psycopg.Connection, index: str) -> float:
    row = conn.execute("SELECT pg_relation_size(%s::regclass)", (index,)).fetchone()
    return (row[0] if row else 0) / _BYTES_PER_MB


def _create_and_load(
    conn: psycopg.Connection, cfg: SearchConfig, corpus, progress: ProgressFn
) -> None:
    cfgname = _ts_config(cfg)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.execute(f"DROP TABLE IF EXISTS {PG_TABLE}")
    # Text, the precomputed tsvector, and the embedding all share one row -- that
    # is the whole "search on the document" point. The tsvector is a STORED
    # generated column so ranking never recomputes it (the idiomatic, fast setup).
    conn.execute(
        f"""
        CREATE TABLE {PG_TABLE} (
            id        bigint PRIMARY KEY,
            title     text   NOT NULL,
            body      text   NOT NULL,
            tags      text[] NOT NULL,
            category  text   NOT NULL,
            embedding vector({cfg.dim}) NOT NULL,
            tsv       tsvector GENERATED ALWAYS AS
                      (to_tsvector('{cfgname}', title || ' ' || body)) STORED
        )
        """
    )
    sql = (
        f"INSERT INTO {PG_TABLE} (id, title, body, tags, category, embedding) "
        "VALUES (%s, %s, %s, %s, %s, %s)"
    )
    batch: list[tuple] = []
    with conn.cursor() as cur:
        for i in range(len(corpus)):
            batch.append(
                (
                    corpus.ids[i],
                    corpus.titles[i],
                    corpus.bodies[i],
                    corpus.tags[i],
                    corpus.categories[i],
                    corpus.embeddings[i],
                )
            )
            if len(batch) >= 200:
                cur.executemany(sql, batch)
                batch.clear()
                _emit(progress, "pg:load", i / len(corpus))
        if batch:
            cur.executemany(sql, batch)
    conn.commit()
    conn.execute(f"VACUUM ANALYZE {PG_TABLE}")
    _emit(progress, "pg:load", 1.0)


def _build_vector_index(conn: psycopg.Connection, cfg: SearchConfig) -> float:
    t0 = now()
    conn.execute(
        f"""
        CREATE INDEX {_HNSW_INDEX} ON {PG_TABLE}
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = {int(cfg.pg_hnsw_m)}, ef_construction = {int(cfg.pg_hnsw_ef_construction)})
        """
    )
    return now() - t0


def _build_text_index(conn: psycopg.Connection, cfg: SearchConfig) -> float:
    t0 = now()
    conn.execute(f"CREATE INDEX {_GIN_INDEX} ON {PG_TABLE} USING gin (tsv)")
    return now() - t0


def _ts_config(cfg: SearchConfig) -> str:
    # 'simple' avoids stemming so the three lexical views (the Python TF-IDF
    # reference, Postgres, and Lucene's standard analyzer on the Atlas side) see
    # the same tokens. Anything else is accepted but will diverge slightly.
    name = (cfg.text_language or "simple").strip().lower()
    return name if name.isalnum() else "simple"


def _run_vector_queries(
    cfg: SearchConfig, qset, progress: ProgressFn
) -> tuple[list[float], float]:
    latencies: list[float] = []
    recalls: list[float] = []
    with psycopg.connect(cfg.pg_dsn, autocommit=True) as conn:
        register_vector(conn)
        conn.execute(f"SET hnsw.ef_search = {int(cfg.pg_hnsw_ef_search)}")
        sql = f"SELECT id FROM {PG_TABLE} ORDER BY embedding <=> %s LIMIT %s"
        with conn.cursor() as cur:
            # Warmup (not measured): prime the plan and OS cache.
            warm = min(10, cfg.n_queries)
            for i in range(warm):
                cur.execute(sql, (qset.vectors[i], cfg.k), prepare=True)
                cur.fetchall()
            for i in range(cfg.n_queries):
                q = qset.vectors[i]
                t0 = now()
                cur.execute(sql, (q, cfg.k), prepare=True)
                rows = cur.fetchall()
                latencies.append(now() - t0)
                returned = [int(r[0]) for r in rows]
                recalls.append(recall_at_k(returned, qset.vector_truth[i], cfg.k))
                _emit(progress, "pg:query", (i + 1) / cfg.n_queries)
    return latencies, (sum(recalls) / len(recalls) if recalls else 0.0)


def _run_text_queries(
    cfg: SearchConfig, corpus, qset, progress: ProgressFn
) -> tuple[list[float], float]:
    latencies: list[float] = []
    precisions: list[float] = []
    cfgname = _ts_config(cfg)
    with psycopg.connect(cfg.pg_dsn, autocommit=True) as conn:
        # OR semantics (term1 | term2) to match the Lucene standard analyzer's
        # default, ranked with ts_rank_cd over the precomputed tsvector column.
        sql = f"""
            SELECT id
            FROM {PG_TABLE}
            WHERE tsv @@ to_tsquery('{cfgname}', %(q)s)
            ORDER BY ts_rank_cd(tsv, to_tsquery('{cfgname}', %(q)s)) DESC
            LIMIT %(k)s
        """
        with conn.cursor() as cur:
            warm = min(10, cfg.n_queries)
            for i in range(warm):
                cur.execute(sql, {"q": _or_query(qset.texts[i]), "k": cfg.k}, prepare=True)
                cur.fetchall()
            for i in range(cfg.n_queries):
                t0 = now()
                cur.execute(sql, {"q": _or_query(qset.texts[i]), "k": cfg.k}, prepare=True)
                rows = cur.fetchall()
                latencies.append(now() - t0)
                returned = [int(r[0]) for r in rows]
                terms = qset.texts[i].split()
                precisions.append(lexical_precision(returned, terms, corpus, cfg.k))
                _emit(progress, "pg:query", (i + 1) / cfg.n_queries)
    return latencies, (sum(precisions) / len(precisions) if precisions else 0.0)


def _or_query(text: str) -> str:
    terms = [t for t in text.split() if t.isalnum()]
    return " | ".join(terms)


def run(cfg: SearchConfig, progress: ProgressFn = None) -> SearchResult:
    """Run the search benchmark, repeating ``cfg.trials`` times when >1 and
    reporting mean +/- stdev. The seed is held fixed across trials so the
    variance reflects measurement noise, not different data."""
    if cfg.trials <= 1:
        return _run_once(cfg, progress)

    results: list[SearchResult] = []
    for t in range(cfg.trials):
        def scoped(phase: str, frac: float, _t: int = t) -> None:
            _emit(progress, phase, (_t + frac) / cfg.trials)

        results.append(_run_once(cfg, scoped))
    return aggregate_search_trials(results)


def _run_once(cfg: SearchConfig, progress: ProgressFn = None) -> SearchResult:
    if cfg.mode not in ("vector", "fts"):
        raise ValueError(f"unknown search mode: {cfg.mode!r}")

    _preflight(cfg.pg_dsn)
    _emit(progress, "pg:corpus", 0.0)
    corpus = build_corpus(cfg.corpus_size, cfg.dim, cfg.seed)
    qset = build_queries(corpus, cfg.n_queries, cfg.k, cfg.seed)

    _emit(progress, "pg:setup", 0.0)
    with psycopg.connect(cfg.pg_dsn, autocommit=True) as conn:
        # The vector type must exist before pgvector can register its adapter.
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        version = _server_version(conn)
        pgv = _pgvector_version(conn)
        _create_and_load(conn, cfg, corpus, progress)

        _emit(progress, "pg:index", 0.0)
        if cfg.mode == "vector":
            build_s = _build_vector_index(conn, cfg)
            index_size = _index_size_mb(conn, _HNSW_INDEX)
            label = f"PostgreSQL {version} + pgvector {pgv} (HNSW)"
            recall_kind = "ann_recall"
        else:
            build_s = _build_text_index(conn, cfg)
            index_size = _index_size_mb(conn, _GIN_INDEX)
            label = f"PostgreSQL {version} full-text (tsvector + GIN)"
            recall_kind = "lexical_precision"
        conn.execute(f"VACUUM ANALYZE {PG_TABLE}")
        _emit(progress, "pg:index", 1.0)

    _emit(progress, "pg:query", 0.0)
    wall_start = now()
    if cfg.mode == "vector":
        latencies, recall = _run_vector_queries(cfg, qset, progress)
    else:
        latencies, recall = _run_text_queries(cfg, corpus, qset, progress)
    wall = now() - wall_start
    qps = len(latencies) / wall if wall > 0 else 0.0

    extra: dict[str, Any] = {
        "server_version": version,
        "pgvector_version": pgv,
        "recall_kind": recall_kind,
        "ts_config": _ts_config(cfg),
    }
    if cfg.mode == "vector":
        extra.update(
            {
                "hnsw_m": cfg.pg_hnsw_m,
                "hnsw_ef_construction": cfg.pg_hnsw_ef_construction,
                "hnsw_ef_search": cfg.pg_hnsw_ef_search,
            }
        )

    _emit(progress, "pg:done", 1.0)
    return SearchResult(
        engine="postgresql",
        mode=cfg.mode,
        label=label,
        corpus_size=cfg.corpus_size,
        dim=cfg.dim,
        k=cfg.k,
        n_queries=len(latencies),
        index_build_s=round(build_s, 4),
        index_size_mb=round(index_size, 4),
        latency_ms=summarize_latencies(latencies),
        recall_at_k=round(recall, 4),
        qps=qps,
        extra=extra,
    )
