"""MongoDB search core: Atlas Search (full-text) and Atlas Vector Search.

Entrypoint: ``run(config, progress) -> SearchResult``. Imported by the
`demo_mdb_search.py` CLI and the search FastAPI app so they share one
implementation.

Both capabilities live alongside the operational document in one ``corpus``
collection: the embedding is a field on the same document as the text. Atlas
Search and Vector Search are served by the bundled ``mongot`` process, so the
indexes build *asynchronously* -- we poll ``$listSearchIndexes`` until the index
is ``queryable`` before timing any queries, and we count that wait as the build
time. The mongot index byte-size is not exposed by Atlas Local through a stable
API, so it is reported as unavailable rather than guessed (see search/README.md).
"""

from __future__ import annotations

import time
from typing import Any

from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

from search.bench.common import (
    ATLAS_TEXT_INDEX,
    ATLAS_VECTOR_INDEX,
    MONGO_COLLECTION,
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

_INDEX_WAIT_TIMEOUT_S = 180.0


def _client(cfg: SearchConfig) -> MongoClient:
    return MongoClient(cfg.mongo_uri)


def _preflight(uri: str) -> None:
    try:
        probe = MongoClient(uri, serverSelectionTimeoutMS=3000)
        probe.admin.command("ping")
        probe.close()
    except Exception as exc:
        raise RuntimeError(
            f"Cannot reach MongoDB at {uri!r}. Start the search stack with: "
            "docker compose -f search/docker-compose.yml up -d"
        ) from exc


def _load(db, corpus, progress: ProgressFn) -> None:
    coll = db[MONGO_COLLECTION]
    batch: list[dict] = []
    recs = corpus.records()
    for i, rec in enumerate(recs):
        rec["_id"] = rec.pop("id")  # use the corpus id as _id so it matches ground truth
        batch.append(rec)
        if len(batch) >= 200:
            coll.insert_many(batch, ordered=False)
            batch.clear()
            _emit(progress, "mongo:load", i / len(recs))
    if batch:
        coll.insert_many(batch, ordered=False)
    _emit(progress, "mongo:load", 1.0)


def _wait_queryable(coll, name: str, progress: ProgressFn) -> float:
    """Block until the named search index is queryable; return seconds waited."""
    t0 = now()
    while now() - t0 < _INDEX_WAIT_TIMEOUT_S:
        status = None
        for idx in coll.list_search_indexes(name):
            status = idx.get("status")
            if idx.get("queryable"):
                return now() - t0
        _emit(progress, "mongo:index", min(0.99, (now() - t0) / 30.0))
        time.sleep(2.0)
    raise RuntimeError(
        f"Atlas Search index {name!r} did not become queryable within "
        f"{_INDEX_WAIT_TIMEOUT_S:.0f}s (last status={status!r}). On Atlas Local, "
        "check that mongot is healthy: docker compose -f search/docker-compose.yml logs"
    )


def _create_vector_index(coll, cfg: SearchConfig) -> None:
    model = SearchIndexModel(
        definition={
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": cfg.dim,
                    "similarity": "cosine",
                }
            ]
        },
        name=ATLAS_VECTOR_INDEX,
        type="vectorSearch",
    )
    coll.create_search_index(model)


def _create_text_index(coll) -> None:
    model = SearchIndexModel(
        definition={
            "mappings": {
                "dynamic": False,
                "fields": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
            }
        },
        name=ATLAS_TEXT_INDEX,
        type="search",
    )
    coll.create_search_index(model)


def _run_vector_queries(
    coll, cfg: SearchConfig, qset, progress: ProgressFn
) -> tuple[list[float], float]:
    latencies: list[float] = []
    recalls: list[float] = []

    def pipeline(vec: list[float]) -> list[dict]:
        return [
            {
                "$vectorSearch": {
                    "index": ATLAS_VECTOR_INDEX,
                    "path": "embedding",
                    "queryVector": vec,
                    "numCandidates": int(cfg.mongo_num_candidates),
                    "limit": int(cfg.k),
                }
            },
            {"$project": {"_id": 1}},
        ]

    warm = min(10, cfg.n_queries)
    for i in range(warm):
        list(coll.aggregate(pipeline(qset.vectors[i].tolist())))
    for i in range(cfg.n_queries):
        vec = qset.vectors[i].tolist()
        t0 = now()
        rows = list(coll.aggregate(pipeline(vec)))
        latencies.append(now() - t0)
        returned = [int(r["_id"]) for r in rows]
        recalls.append(recall_at_k(returned, qset.vector_truth[i], cfg.k))
        _emit(progress, "mongo:query", (i + 1) / cfg.n_queries)
    return latencies, (sum(recalls) / len(recalls) if recalls else 0.0)


def _run_text_queries(
    coll, cfg: SearchConfig, corpus, qset, progress: ProgressFn
) -> tuple[list[float], float]:
    latencies: list[float] = []
    precisions: list[float] = []

    def pipeline(text: str) -> list[dict]:
        return [
            {
                "$search": {
                    "index": ATLAS_TEXT_INDEX,
                    "text": {"query": text, "path": ["title", "body"]},
                }
            },
            {"$limit": int(cfg.k)},
            {"$project": {"_id": 1}},
        ]

    warm = min(10, cfg.n_queries)
    for i in range(warm):
        list(coll.aggregate(pipeline(qset.texts[i])))
    for i in range(cfg.n_queries):
        t0 = now()
        rows = list(coll.aggregate(pipeline(qset.texts[i])))
        latencies.append(now() - t0)
        returned = [int(r["_id"]) for r in rows]
        terms = qset.texts[i].split()
        precisions.append(lexical_precision(returned, terms, corpus, cfg.k))
        _emit(progress, "mongo:query", (i + 1) / cfg.n_queries)
    return latencies, (sum(precisions) / len(precisions) if precisions else 0.0)


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

    _preflight(cfg.mongo_uri)
    _emit(progress, "mongo:corpus", 0.0)
    corpus = build_corpus(cfg.corpus_size, cfg.dim, cfg.seed)
    qset = build_queries(corpus, cfg.n_queries, cfg.k, cfg.seed)

    client = _client(cfg)
    try:
        version = client.server_info().get("version", "unknown")
        db = client[cfg.mongo_db]
        db.drop_collection(MONGO_COLLECTION)
        db.create_collection(MONGO_COLLECTION)
        coll = db[MONGO_COLLECTION]

        _emit(progress, "mongo:load", 0.0)
        _load(db, corpus, progress)

        _emit(progress, "mongo:index", 0.0)
        build_t0 = now()
        if cfg.mode == "vector":
            _create_vector_index(coll, cfg)
            build_s = _wait_queryable(coll, ATLAS_VECTOR_INDEX, progress)
            label = f"MongoDB {version} Atlas Vector Search (HNSW)"
            recall_kind = "ann_recall"
        else:
            _create_text_index(coll)
            build_s = _wait_queryable(coll, ATLAS_TEXT_INDEX, progress)
            label = f"MongoDB {version} Atlas Search (BM25)"
            recall_kind = "lexical_precision"
        # build_t0 retained for clarity; _wait_queryable already returns elapsed.
        _ = build_t0
        _emit(progress, "mongo:index", 1.0)

        _emit(progress, "mongo:query", 0.0)
        wall_start = now()
        if cfg.mode == "vector":
            latencies, recall = _run_vector_queries(coll, cfg, qset, progress)
        else:
            latencies, recall = _run_text_queries(coll, cfg, corpus, qset, progress)
        wall = now() - wall_start
    finally:
        client.close()

    qps = len(latencies) / wall if wall > 0 else 0.0

    extra: dict[str, Any] = {
        "server_version": version,
        "search_engine": "mongot (Lucene)",
        "recall_kind": recall_kind,
        # mongot does not expose its Lucene index byte-size via a stable Atlas
        # Local API, so we do not guess one. The Postgres side reports a real
        # index size; cross-engine index-size comparison is therefore one-sided.
        "index_size_note": "Atlas Search index size not exposed by Atlas Local",
        "consistency_note": (
            "Atlas Search/Vector are eventually consistent with mongod (mongot sync)"
        ),
    }
    if cfg.mode == "vector":
        extra["num_candidates"] = cfg.mongo_num_candidates

    _emit(progress, "mongo:done", 1.0)
    return SearchResult(
        engine="mongodb",
        mode=cfg.mode,
        label=label,
        corpus_size=cfg.corpus_size,
        dim=cfg.dim,
        k=cfg.k,
        n_queries=len(latencies),
        index_build_s=round(build_s, 4),
        index_size_mb=0.0,
        latency_ms=summarize_latencies(latencies),
        recall_at_k=round(recall, 4),
        qps=qps,
        extra=extra,
    )
