#!/usr/bin/env python3
"""CLI: run the PostgreSQL search benchmark (full-text or vector).

Thin wrapper around the importable core in ``search/bench/pg_search.py`` so the
CLI and the search FastAPI dashboard run identical code.

Examples:
    uv run python -m search.demo_psql_search                       # vector (HNSW)
    uv run python -m search.demo_psql_search --mode fts            # full-text
    uv run python -m search.demo_psql_search --corpus 20000 --dim 384 --trials 5
    uv run python -m search.demo_psql_search --ef-search 100       # raise recall
"""

from __future__ import annotations

import argparse

from search.bench import SearchConfig, print_search_result, write_result
from search.bench.pg_search import run as run_pg_search


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--mode", choices=["vector", "fts"], default="vector",
                   help="vector = pgvector HNSW; fts = tsvector + GIN full-text")
    p.add_argument("--corpus", type=int, default=5000, help="number of documents")
    p.add_argument("--dim", type=int, default=256, help="embedding dimensionality")
    p.add_argument("--k", type=int, default=10, help="top-k results per query")
    p.add_argument("--queries", type=int, default=200, help="number of measured queries")
    p.add_argument("--seed", type=int, default=1234, help="RNG seed (deterministic corpus)")
    p.add_argument("--trials", type=int, default=1, help="repeat run N times; report mean/stdev")
    p.add_argument("--ef-search", type=int, default=40,
                   help="HNSW ef_search (recall/latency fairness knob; >= k)")
    p.add_argument("--ef-construction", type=int, default=64, help="HNSW ef_construction")
    p.add_argument("--hnsw-m", type=int, default=16, help="HNSW M (graph degree)")
    p.add_argument("--text-config", default="simple",
                   help="Postgres text search config (default 'simple' avoids stemming)")
    args = p.parse_args()

    cfg = SearchConfig(
        mode=args.mode,
        corpus_size=args.corpus,
        dim=args.dim,
        k=args.k,
        n_queries=args.queries,
        seed=args.seed,
        trials=args.trials,
        pg_hnsw_ef_search=args.ef_search,
        pg_hnsw_ef_construction=args.ef_construction,
        pg_hnsw_m=args.hnsw_m,
        text_language=args.text_config,
    )

    def progress(phase: str, frac: float) -> None:
        print(f"\r  {phase:<16} {frac * 100:5.1f}%", end="", flush=True)

    result = run_pg_search(cfg, progress)
    print()
    print_search_result(result)
    path = write_result(result)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
