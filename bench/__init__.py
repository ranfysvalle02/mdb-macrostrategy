"""Benchmark harness for the PostgreSQL-JSONB vs MongoDB update-penalty demo.

Public API lives here; the engine cores are submodules you import explicitly so
that importing the package does not pull in both database drivers:

    from bench import BenchConfig, print_result, write_result
    from bench.pg import run as run_pg
    from bench.mongo import run as run_mongo
"""

from __future__ import annotations

from bench.common import (
    MONGO_DB,
    MONGO_URI,
    PG_DSN,
    BenchConfig,
    BenchResult,
    ProgressFn,
    aggregate_trials,
    compatibility_warnings,
    load_result,
    make_document,
    print_result,
    workload_key,
    write_result,
)

__all__ = [
    "BenchConfig",
    "BenchResult",
    "ProgressFn",
    "PG_DSN",
    "MONGO_URI",
    "MONGO_DB",
    "make_document",
    "print_result",
    "write_result",
    "load_result",
    "aggregate_trials",
    "workload_key",
    "compatibility_warnings",
]
