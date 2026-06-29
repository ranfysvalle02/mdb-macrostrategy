"""PostgreSQL benchmark core: the JSONB partial-update penalty under test.

Entrypoint: ``run(config, progress) -> BenchResult``. Imported by the
`basic.demo_psql` CLI and the FastAPI app so they share a single implementation.
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg
from psycopg.types.json import Json

from basic.bench.common import (
    BenchConfig,
    BenchResult,
    aggregate_trials,
    make_document,
)
from shared.common import (
    ProgressFn,
    _emit,
    now,
    split_ops,
    summarize_latencies,
)

TABLE = "sessions"
_BYTES_PER_MB = 1024 * 1024


def _server_version(conn: psycopg.Connection) -> str:
    return conn.execute("SHOW server_version").fetchone()[0].split(" ")[0]


def _size_info(conn: psycopg.Connection) -> dict[str, float]:
    total = conn.execute("SELECT pg_total_relation_size(%s)", (TABLE,)).fetchone()[0]
    heap = conn.execute("SELECT pg_relation_size(%s)", (TABLE,)).fetchone()[0]
    toast_row = conn.execute(
        """
        SELECT COALESCE(pg_total_relation_size(c.reltoastrelid), 0)
        FROM pg_class c WHERE c.relname = %s
        """,
        (TABLE,),
    ).fetchone()
    toast = toast_row[0] if toast_row else 0
    return {
        "total_mb": total / _BYTES_PER_MB,
        "heap_mb": heap / _BYTES_PER_MB,
        "toast_mb": toast / _BYTES_PER_MB,
    }


def _tuple_stats(conn: psycopg.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT n_live_tup, n_dead_tup, n_tup_upd, n_tup_hot_upd, autovacuum_count
        FROM pg_stat_user_tables WHERE relname = %s
        """,
        (TABLE,),
    ).fetchone()
    if not row:
        return {}
    return {
        "n_live_tup": row[0] or 0,
        "n_dead_tup": row[1] or 0,
        "n_tup_upd": row[2] or 0,
        "n_tup_hot_upd": row[3] or 0,
        "autovacuum_count": row[4] or 0,
    }


def _create_table(conn: psycopg.Connection, strategy: str, autovacuum: bool) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    if strategy == "normalized":
        # Idiomatic Postgres: the hot counters are first-class columns; the bulk
        # of the document stays in a (TOASTed) jsonb that the workload never touches.
        conn.execute(
            f"""
            CREATE TABLE {TABLE} (
                id        bigint PRIMARY KEY,
                score     bigint NOT NULL DEFAULT 0,
                last_seen bigint NOT NULL DEFAULT 0,
                doc       jsonb  NOT NULL
            )
            """
        )
    else:
        conn.execute(
            f"CREATE TABLE {TABLE} (id bigint PRIMARY KEY, doc jsonb NOT NULL)"
        )
    if not autovacuum:
        conn.execute(f"ALTER TABLE {TABLE} SET (autovacuum_enabled = false)")


def _insert_rows(conn: psycopg.Connection, cfg: BenchConfig, progress: ProgressFn) -> None:
    rng = random.Random(cfg.seed)
    batch: list[tuple] = []
    normalized = cfg.pg_strategy == "normalized"
    sql = (
        f"INSERT INTO {TABLE} (id, doc) VALUES (%s, %s)"
        if not normalized
        else f"INSERT INTO {TABLE} (id, score, last_seen, doc) VALUES (%s, 0, 0, %s)"
    )
    with conn.cursor() as cur:
        for i in range(cfg.rows):
            doc = make_document(cfg.doc_kb, rng)
            batch.append((i, Json(doc)))
            if len(batch) >= 100:
                cur.executemany(sql, batch)
                batch.clear()
                _emit(progress, "pg:insert", i / cfg.rows)
        if batch:
            cur.executemany(sql, batch)
    conn.commit()
    _emit(progress, "pg:insert", 1.0)


def _update_sql(strategy: str) -> str:
    if strategy == "normalized":
        return f"UPDATE {TABLE} SET score = score + 1, last_seen = %(ts)s WHERE id = %(id)s"
    # Mutate two keys buried inside the (TOASTed) jsonb. Postgres cannot edit the
    # value in place: MVCC writes a whole new row version and rewrites the TOAST
    # chunks, leaving the prior version as a dead tuple.
    return f"""
        UPDATE {TABLE}
        SET doc = jsonb_set(
                    jsonb_set(doc, '{{stats,score}}',
                              to_jsonb(((doc #>> '{{stats,score}}')::bigint + 1))),
                    '{{stats,last_seen}}', to_jsonb(%(ts)s::bigint))
        WHERE id = %(id)s
    """


def _run_slice(cfg: BenchConfig, n_ops: int, worker_idx: int) -> list[float]:
    rng = random.Random(cfg.seed * 7919 + worker_idx)
    sql = _update_sql(cfg.pg_strategy)
    latencies: list[float] = []
    ts = 1_700_000_000
    with psycopg.connect(cfg.pg_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for _ in range(n_ops):
                params = {"id": rng.randrange(cfg.rows), "ts": ts}
                ts += 1
                t0 = now()
                cur.execute(sql, params, prepare=True)
                latencies.append(now() - t0)
    return latencies


def _warmup(cfg: BenchConfig, n_ops: int) -> None:
    if n_ops <= 0:
        return
    rng = random.Random(cfg.seed + 101)
    sql = _update_sql(cfg.pg_strategy)
    ts = 1_690_000_000
    with psycopg.connect(cfg.pg_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            for _ in range(n_ops):
                cur.execute(sql, {"id": rng.randrange(cfg.rows), "ts": ts}, prepare=True)
                ts += 1


def _preflight(dsn: str) -> None:
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        raise RuntimeError(
            f"Cannot reach Postgres at {dsn!r}. Start the stack with: docker compose up -d"
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
    strategy = cfg.pg_strategy
    label = f"PostgreSQL ({strategy})"

    _preflight(cfg.pg_dsn)
    _emit(progress, "pg:setup", 0.0)
    with psycopg.connect(cfg.pg_dsn, autocommit=True) as conn:
        version = _server_version(conn)
        label = f"PostgreSQL {version} ({strategy})"
        _create_table(conn, strategy, cfg.pg_autovacuum)

    # Bulk load
    with psycopg.connect(cfg.pg_dsn) as conn:
        _insert_rows(conn, cfg, progress)

    with psycopg.connect(cfg.pg_dsn, autocommit=True) as conn:
        conn.execute(f"VACUUM ANALYZE {TABLE}")
        size_before = _size_info(conn)
        stats_before = _tuple_stats(conn)

    # Warmup (not measured)
    _emit(progress, "pg:warmup", 0.0)
    _warmup(cfg, cfg.warmup_ops())

    # Measured workload, parallel across workers
    _emit(progress, "pg:workload", 0.0)
    per_worker = split_ops(cfg.measured_ops(), cfg.workers)
    wall_start = now()
    latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=cfg.workers) as pool:
        futures = [
            pool.submit(_run_slice, cfg, n, idx)
            for idx, n in enumerate(per_worker)
            if n > 0
        ]
        done = 0
        for fut in futures:
            latencies.extend(fut.result())
            done += 1
            _emit(progress, "pg:workload", done / max(1, len(futures)))
    wall = now() - wall_start

    with psycopg.connect(cfg.pg_dsn, autocommit=True) as conn:
        size_after = _size_info(conn)
        stats_after = _tuple_stats(conn)

    measured = len(latencies)
    throughput = measured / wall if wall > 0 else 0.0

    def _delta(key: str) -> int:
        return stats_after.get(key, 0) - stats_before.get(key, 0)

    extra: dict[str, Any] = {
        "strategy": strategy,
        "server_version": version,
        "heap_before_mb": round(size_before["heap_mb"], 3),
        "heap_after_mb": round(size_after["heap_mb"], 3),
        "toast_before_mb": round(size_before["toast_mb"], 3),
        "toast_after_mb": round(size_after["toast_mb"], 3),
        "n_tup_upd": _delta("n_tup_upd"),
        "n_tup_hot_upd": _delta("n_tup_hot_upd"),
        "n_dead_tup": stats_after.get("n_dead_tup", 0),
        "autovacuum_count": _delta("autovacuum_count"),
        "autovacuum_enabled": cfg.pg_autovacuum,
    }

    _emit(progress, "pg:done", 1.0)
    return BenchResult(
        engine="postgresql",
        label=label,
        rows=cfg.rows,
        ops=measured,
        workers=cfg.workers,
        doc_kb=cfg.doc_kb,
        duration_s=wall,
        throughput_ops_s=throughput,
        latency_ms=summarize_latencies(latencies),
        size_before_mb=round(size_before["total_mb"], 3),
        size_after_mb=round(size_after["total_mb"], 3),
        size_growth_mb=round(size_after["total_mb"] - size_before["total_mb"], 3),
        extra=extra,
    )
