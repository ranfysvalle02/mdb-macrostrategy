"""Benchmark harness for the basic angle: PostgreSQL JSONB vs MongoDB update penalty.

Public API lives here; the engine cores are submodules you import explicitly so
that importing the package does not pull in both database drivers:

    from basic.bench import BenchConfig, print_result, write_result
    from basic.bench.pg import run as run_pg
    from basic.bench.mongo import run as run_mongo
"""

from __future__ import annotations

from basic.bench.common import (
    MONGO_DB,
    MONGO_URI,
    PG_DSN,
    BenchConfig,
    BenchResult,
    aggregate_trials,
    compatibility_warnings,
    make_document,
    print_result,
    workload_key,
)
from shared.common import (
    ProgressFn,
    load_result,
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
