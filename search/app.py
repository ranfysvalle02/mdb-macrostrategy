"""FastAPI dashboard for the search angle (full-text + vector on the same record).

Run (the search/ stack must be up):
    docker compose -f search/docker-compose.yml up -d
    uv run uvicorn search.app:app --reload --port 8001

Then open http://localhost:8001

The HTTP layer is a thin shell over the same cores the CLI uses
(search.bench.pg_search / search.bench.mongo_search); the benchmark logic lives
there, not here. Each run rebuilds the deterministic corpus from the seed, so
both engines index byte-for-byte identical data and are scored against the same
ground truth.
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

from search.bench import SearchConfig, SearchResult
from search.bench import mongo_search as bench_mongo
from search.bench import pg_search as bench_pg

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="MongoDB vs PostgreSQL: full-text + vector search")

# Each run DROPs and recreates the shared table/collection, so two concurrent
# runs would corrupt each other's measurements. Allow only one at a time.
_run_lock = threading.Lock()


class SearchParams(BaseModel):
    mode: str = "vector"
    corpus_size: int = 5000
    dim: int = 256
    k: int = 10
    n_queries: int = 200
    seed: int = 1234
    trials: int = 1
    pg_hnsw_ef_search: int = 40
    mongo_num_candidates: int = 100


def _result_dict(r: SearchResult) -> dict[str, Any]:
    d = r.to_dict()
    d["slug"] = r.slug()
    return d


def _config_from_params(p: SearchParams) -> SearchConfig:
    return SearchConfig(
        mode=p.mode,
        corpus_size=p.corpus_size,
        dim=p.dim,
        k=p.k,
        n_queries=p.n_queries,
        seed=p.seed,
        trials=p.trials,
        pg_hnsw_ef_search=p.pg_hnsw_ef_search,
        mongo_num_candidates=p.mongo_num_candidates,
    )


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #

def _pg_ok() -> bool:
    import psycopg

    try:
        with psycopg.connect(SearchConfig().pg_dsn, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _mongo_ok() -> bool:
    from pymongo import MongoClient

    try:
        client = MongoClient(SearchConfig().mongo_uri, serverSelectionTimeoutMS=2000)
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

@app.post("/api/search-benchmark")
async def search_benchmark(params: SearchParams) -> dict[str, Any]:
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


@app.get("/api/search-benchmark/stream")
async def search_benchmark_stream(
    request: Request,
    mode: str = "vector",
    corpus_size: int = 5000,
    dim: int = 256,
    k: int = 10,
    n_queries: int = 200,
    seed: int = 1234,
    trials: int = 1,
    pg_hnsw_ef_search: int = 40,
    mongo_num_candidates: int = 100,
) -> StreamingResponse:
    cfg = _config_from_params(
        SearchParams(
            mode=mode, corpus_size=corpus_size, dim=dim, k=k, n_queries=n_queries,
            seed=seed, trials=trials, pg_hnsw_ef_search=pg_hnsw_ef_search,
            mongo_num_candidates=mongo_num_candidates,
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
