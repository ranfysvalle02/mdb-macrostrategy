"""FastAPI dashboard for the JSONB-vs-MongoDB update-penalty benchmark.

Run (the basic/ stack must be up via `docker compose -f basic/docker-compose.yml up -d`):
    uv run uvicorn basic.app:app --reload

Then open http://localhost:8000

The HTTP layer is a thin shell over the same cores the CLI uses (basic.bench.pg /
basic.bench.mongo); the benchmark logic lives there, not here.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from basic.bench import BenchConfig, BenchResult
from basic.bench import mongo as bench_mongo
from basic.bench import pg as bench_pg

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="MongoDB vs PostgreSQL JSONB Update Penalty")

# Each run DROPs and recreates the shared table/collection, so two concurrent
# runs would corrupt each other's measurements. Allow only one at a time.
_run_lock = threading.Lock()


class BenchParams(BaseModel):
    rows: int = 500
    ops: int = 20_000
    workers: int = 4
    doc_kb: int = 32
    seed: int = 1234
    trials: int = 1
    pg_strategy: str = "jsonb"
    pg_autovacuum: bool = True


def _result_dict(r: BenchResult) -> dict[str, Any]:
    d = r.to_dict()
    d["slug"] = r.slug()
    return d


def _config_from_params(p: BenchParams) -> BenchConfig:
    return BenchConfig(
        rows=p.rows,
        ops=p.ops,
        workers=p.workers,
        doc_kb=p.doc_kb,
        seed=p.seed,
        trials=p.trials,
        pg_strategy=p.pg_strategy,
        pg_autovacuum=p.pg_autovacuum,
    )


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #

def _pg_ok() -> bool:
    import psycopg

    try:
        with psycopg.connect(BenchConfig().pg_dsn, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _mongo_ok() -> bool:
    from pymongo import MongoClient

    try:
        client = MongoClient(BenchConfig().mongo_uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        client.close()
        return True
    except Exception:
        return False


@app.get("/api/health")
async def health() -> dict[str, bool]:
    pg, mongo = await asyncio.gather(
        asyncio.to_thread(_pg_ok), asyncio.to_thread(_mongo_ok)
    )
    return {"postgres": pg, "mongodb": mongo}


# --------------------------------------------------------------------------- #
# Benchmark (blocking) and streaming (SSE) variants
# --------------------------------------------------------------------------- #

@app.post("/api/benchmark")
async def benchmark(params: BenchParams) -> dict[str, Any]:
    cfg = _config_from_params(params)
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A benchmark is already running")
    try:
        pg = await asyncio.to_thread(bench_pg.run, cfg, None)
        md = await asyncio.to_thread(bench_mongo.run, cfg, None)
    finally:
        _run_lock.release()
    return {
        "postgres": _result_dict(pg),
        "mongodb": _result_dict(md),
        "config": cfg.to_public_dict(),
    }


@app.get("/api/benchmark/stream")
async def benchmark_stream(
    request: Request,
    rows: int = 500,
    ops: int = 20_000,
    workers: int = 4,
    doc_kb: int = 32,
    seed: int = 1234,
    trials: int = 1,
    pg_strategy: str = "jsonb",
    pg_autovacuum: bool = True,
) -> StreamingResponse:
    cfg = _config_from_params(
        BenchParams(
            rows=rows, ops=ops, workers=workers, doc_kb=doc_kb, seed=seed,
            trials=trials, pg_strategy=pg_strategy, pg_autovacuum=pg_autovacuum,
        )
    )
    q: "queue.Queue[dict[str, Any]]" = queue.Queue()

    if not _run_lock.acquire(blocking=False):
        busy = {"type": "error", "message": "A benchmark is already running"}

        async def busy_gen():
            yield f"data: {json.dumps(busy)}\n\n"

        return StreamingResponse(busy_gen(), media_type="text/event-stream")

    def worker() -> None:
        try:
            def prog(phase: str, frac: float) -> None:
                q.put({"type": "progress", "phase": phase, "frac": frac})

            pg = bench_pg.run(cfg, prog)
            q.put({"type": "partial", "engine": "postgres", "result": _result_dict(pg)})
            md = bench_mongo.run(cfg, prog)
            q.put({"type": "partial", "engine": "mongodb", "result": _result_dict(md)})
            q.put(
                {
                    "type": "done",
                    "postgres": _result_dict(pg),
                    "mongodb": _result_dict(md),
                    "config": cfg.to_public_dict(),
                }
            )
        except Exception as exc:  # surface failures to the UI
            q.put({"type": "error", "message": str(exc)})
        finally:
            _run_lock.release()
            q.put({"type": "_end"})

    threading.Thread(target=worker, daemon=True).start()

    async def event_gen():
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.to_thread(q.get, True, 0.5)
            except queue.Empty:
                continue
            if item.get("type") == "_end":
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# --------------------------------------------------------------------------- #
# Static dashboard
# --------------------------------------------------------------------------- #

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
