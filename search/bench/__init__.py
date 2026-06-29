"""Search-angle harness: full-text and vector search on the same record.

Public API lives here; the engine cores are submodules you import explicitly so
that importing the package does not pull in both database drivers:

    from search.bench import SearchConfig, print_search_result, write_result
    from search.bench.pg_search import run as run_pg_search
    from search.bench.mongo_search import run as run_mongo_search
"""

from __future__ import annotations

from search.bench.common import (
    ATLAS_TEXT_INDEX,
    ATLAS_VECTOR_INDEX,
    MONGO_COLLECTION,
    MONGO_DB,
    MONGO_URI,
    PG_DSN,
    PG_TABLE,
    SearchConfig,
    SearchResult,
    aggregate_search_trials,
    print_search_result,
    search_compatibility_warnings,
    search_workload_key,
)
from search.bench.corpus import build_corpus, build_queries, lexical_precision, recall_at_k
from shared.common import ProgressFn, load_result, write_result

__all__ = [
    "SearchConfig",
    "SearchResult",
    "ProgressFn",
    "PG_DSN",
    "MONGO_URI",
    "MONGO_DB",
    "PG_TABLE",
    "MONGO_COLLECTION",
    "ATLAS_TEXT_INDEX",
    "ATLAS_VECTOR_INDEX",
    "aggregate_search_trials",
    "print_search_result",
    "search_workload_key",
    "search_compatibility_warnings",
    "build_corpus",
    "build_queries",
    "recall_at_k",
    "lexical_precision",
    "write_result",
    "load_result",
]
