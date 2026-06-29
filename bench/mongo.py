"""MongoDB benchmark core: field-level updates on large documents.

Entrypoint: ``run(config, progress) -> BenchResult``. Imported by the
`demo-mdb.py` CLI and the FastAPI app so they share a single implementation.
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pymongo import MongoClient

from bench.common import (
    BenchConfig,
    BenchResult,
    ProgressFn,
    _emit,
    aggregate_trials,
    make_document,
    now,
    split_ops,
    summarize_latencies,
)

COLLECTION = "sessions"
_BYTES_PER_MB = 1024 * 1024


def _client(cfg: BenchConfig) -> MongoClient:
    # Pool must be at least as large as the worker count or threads will block
    # waiting for a connection rather than exercising the server concurrently.
    return MongoClient(cfg.mongo_uri, maxPoolSize=max(8, cfg.workers + 4))


def _coll_stats(db) -> dict[str, Any]:
    try:
        db.client.admin.command("fsync", lock=False)  # flush so storageSize is current
    except Exception:
        pass
    stats = db.command("collStats", COLLECTION)
    compressor = "snappy"
    cs = stats.get("wiredTiger", {}).get("creationString", "")
    if "block_compressor=" in cs:
        compressor = cs.split("block_compressor=")[1].split(",")[0]
    return {
        "storage_mb": stats.get("storageSize", 0) / _BYTES_PER_MB,
        "logical_mb": stats.get("size", 0) / _BYTES_PER_MB,
        "index_mb": stats.get("totalIndexSize", 0) / _BYTES_PER_MB,
        "count": stats.get("count", 0),
        "compressor": compressor,
    }


def _insert_rows(db, cfg: BenchConfig, progress: ProgressFn) -> None:
    rng = random.Random(cfg.seed)
    coll = db[COLLECTION]
    batch: list[dict] = []
    for i in range(cfg.rows):
        doc = make_document(cfg.doc_kb, rng)
        doc["_id"] = i
        batch.append(doc)
        if len(batch) >= 100:
            coll.insert_many(batch, ordered=False)
            batch.clear()
            _emit(progress, "mongo:insert", i / cfg.rows)
    if batch:
        coll.insert_many(batch, ordered=False)
    _emit(progress, "mongo:insert", 1.0)


def _run_slice(cfg: BenchConfig, client: MongoClient, n_ops: int, worker_idx: int) -> list[float]:
    rng = random.Random(cfg.seed * 7919 + worker_idx)
    coll = client[cfg.mongo_db][COLLECTION]
    latencies: list[float] = []
    ts = 1_700_000_000
    for _ in range(n_ops):
        _id = rng.randrange(cfg.rows)
        ts += 1
        t0 = now()
        # Field-level update operators: only the changed fields travel to the
        # server, and WiredTiger records the modification without the
        # full-row-rewrite-plus-vacuum cycle that JSONB updates incur.
        coll.update_one({"_id": _id}, {"$inc": {"stats.score": 1}, "$set": {"stats.last_seen": ts}})
        latencies.append(now() - t0)
    return latencies


def _warmup(cfg: BenchConfig, client: MongoClient, n_ops: int) -> None:
    if n_ops <= 0:
        return
    rng = random.Random(cfg.seed + 101)
    coll = client[cfg.mongo_db][COLLECTION]
    ts = 1_690_000_000
    for _ in range(n_ops):
        ts += 1
        coll.update_one(
            {"_id": rng.randrange(cfg.rows)},
            {"$inc": {"stats.score": 1}, "$set": {"stats.last_seen": ts}},
        )


def _preflight(uri: str) -> None:
    try:
        probe = MongoClient(uri, serverSelectionTimeoutMS=3000)
        probe.admin.command("ping")
        probe.close()
    except Exception as exc:
        raise RuntimeError(
            f"Cannot reach MongoDB at {uri!r}. Start the stack with: docker compose up -d"
        ) from exc


def run(cfg: BenchConfig, progress: ProgressFn = None) -> BenchResult:
    """Run the benchmark, repeating ``cfg.trials`` times when >1 and reporting
    mean +/- stdev. The seed is held fixed across trials so the variance reflects
    measurement noise, not different input data."""
    if cfg.trials <= 1:
        return _run_once(cfg, progress)

    results: list[BenchResult] = []
    for t in range(cfg.trials):
        def scoped(phase: str, frac: float, _t: int = t) -> None:
            _emit(progress, phase, (_t + frac) / cfg.trials)

        results.append(_run_once(cfg, scoped))
    return aggregate_trials(results)


def _run_once(cfg: BenchConfig, progress: ProgressFn = None) -> BenchResult:
    _preflight(cfg.mongo_uri)
    _emit(progress, "mongo:setup", 0.0)
    client = _client(cfg)
    try:
        version = client.server_info().get("version", "unknown")
        db = client[cfg.mongo_db]
        db.drop_collection(COLLECTION)
        db.create_collection(COLLECTION)

        _insert_rows(db, cfg, progress)
        size_before = _coll_stats(db)

        _emit(progress, "mongo:warmup", 0.0)
        _warmup(cfg, client, cfg.warmup_ops())

        _emit(progress, "mongo:workload", 0.0)
        per_worker = split_ops(cfg.measured_ops(), cfg.workers)
        wall_start = now()
        latencies: list[float] = []
        with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
            futures = [
                pool.submit(_run_slice, cfg, client, n, idx)
                for idx, n in enumerate(per_worker)
                if n > 0
            ]
            done = 0
            for fut in futures:
                latencies.extend(fut.result())
                done += 1
                _emit(progress, "mongo:workload", done / max(1, len(futures)))
        wall = now() - wall_start

        size_after = _coll_stats(db)
    finally:
        client.close()

    measured = len(latencies)
    throughput = measured / wall if wall > 0 else 0.0

    extra: dict[str, Any] = {
        "server_version": version,
        "storage_engine": "wiredTiger",
        "compression": size_after.get("compressor", "snappy"),
        "logical_before_mb": round(size_before["logical_mb"], 3),
        "logical_after_mb": round(size_after["logical_mb"], 3),
        "index_mb": round(size_after["index_mb"], 3),
        "doc_count": size_after.get("count", cfg.rows),
    }

    _emit(progress, "mongo:done", 1.0)
    return BenchResult(
        engine="mongodb",
        label=f"MongoDB {version} (Atlas Local)",
        rows=cfg.rows,
        ops=measured,
        workers=cfg.workers,
        doc_kb=cfg.doc_kb,
        duration_s=wall,
        throughput_ops_s=throughput,
        latency_ms=summarize_latencies(latencies),
        size_before_mb=round(size_before["storage_mb"], 3),
        size_after_mb=round(size_after["storage_mb"], 3),
        size_growth_mb=round(size_after["storage_mb"] - size_before["storage_mb"], 3),
        extra=extra,
    )
