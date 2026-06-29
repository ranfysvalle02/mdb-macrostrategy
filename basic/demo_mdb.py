#!/usr/bin/env python3
"""CLI: run the MongoDB field-level update benchmark.

Thin wrapper around the importable core in ``basic/bench/mongo.py`` so the CLI
and the FastAPI dashboard run identical code.

Examples:
    uv run python -m basic.demo_mdb
    uv run python -m basic.demo_mdb --rows 1000 --ops 40000 --workers 8 --doc-kb 32
"""

from __future__ import annotations

import argparse

from basic.bench import BenchConfig, print_result, write_result
from basic.bench.mongo import run as run_mongo


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--rows", type=int, default=500, help="number of documents to seed")
    p.add_argument("--ops", type=int, default=20_000, help="total update operations (incl. warmup)")
    p.add_argument("--workers", type=int, default=4, help="concurrent client threads")
    p.add_argument("--doc-kb", type=int, default=32, help="target document size in KB")
    p.add_argument("--seed", type=int, default=1234, help="RNG seed (deterministic data)")
    p.add_argument("--trials", type=int, default=1, help="repeat run N times; report mean/stdev")
    args = p.parse_args()

    # Connection is pinned to the docker-compose stack in bench/common.py; only
    # the workload shape is configurable here.
    cfg = BenchConfig(
        rows=args.rows,
        ops=args.ops,
        workers=args.workers,
        doc_kb=args.doc_kb,
        seed=args.seed,
        trials=args.trials,
    )

    def progress(phase: str, frac: float) -> None:
        print(f"\r  {phase:<16} {frac * 100:5.1f}%", end="", flush=True)

    result = run_mongo(cfg, progress)
    print()
    print_result(result)
    path = write_result(result)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
