#!/usr/bin/env python3
"""CLI: run the MongoDB Atlas search benchmark (full-text or vector).

Thin wrapper around the importable core in ``search/bench/mongo_search.py`` so
the CLI and the search FastAPI dashboard run identical code.

Examples:
    uv run python -m search.demo_mdb_search                        # vector
    uv run python -m search.demo_mdb_search --mode fts             # full-text
    uv run python -m search.demo_mdb_search --corpus 20000 --dim 384 --trials 5
    uv run python -m search.demo_mdb_search --num-candidates 200   # raise recall
"""

from __future__ import annotations

import argparse

from search.bench import SearchConfig, print_search_result, write_result
from search.bench.mongo_search import run as run_mongo_search


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--mode", choices=["vector", "fts"], default="vector",
                   help="vector = Atlas Vector Search; fts = Atlas Search (BM25)")
    p.add_argument("--corpus", type=int, default=5000, help="number of documents")
    p.add_argument("--dim", type=int, default=256, help="embedding dimensionality")
    p.add_argument("--k", type=int, default=10, help="top-k results per query")
    p.add_argument("--queries", type=int, default=200, help="number of measured queries")
    p.add_argument("--seed", type=int, default=1234, help="RNG seed (deterministic corpus)")
    p.add_argument("--trials", type=int, default=1, help="repeat run N times; report mean/stdev")
    p.add_argument("--num-candidates", type=int, default=100,
                   help="$vectorSearch numCandidates (recall/latency fairness knob; >= k)")
    args = p.parse_args()

    cfg = SearchConfig(
        mode=args.mode,
        corpus_size=args.corpus,
        dim=args.dim,
        k=args.k,
        n_queries=args.queries,
        seed=args.seed,
        trials=args.trials,
        mongo_num_candidates=args.num_candidates,
    )

    def progress(phase: str, frac: float) -> None:
        print(f"\r  {phase:<16} {frac * 100:5.1f}%", end="", flush=True)

    result = run_mongo_search(cfg, progress)
    print()
    print_search_result(result)
    path = write_result(result)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
