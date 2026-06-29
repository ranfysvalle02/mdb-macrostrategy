"""Search-angle config, result types, and trial aggregation.

The search workload measures *query* performance and *quality*, not writes, so
its config and result shapes differ from the basic angle: latency percentiles,
recall@k against a precomputed ground truth, index build time, and index size.

Connection endpoints are pinned to the ``search/`` docker-compose stack, which
binds different host ports than the basic stack (55433 / 57018 vs 55432 / 57017)
so the two angles can run side by side without colliding.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from shared.common import _agg, render_table

# Pinned to the search/ docker-compose stack. These DROP and recreate their
# table/collection on every run, so they must never point at a real database;
# the non-standard ports are the guardrail. Edit search/docker-compose.yml and
# these constants together.
PG_DSN = "host=localhost port=55433 dbname=bench user=postgres password=postgres"
MONGO_URI = "mongodb://localhost:57018/?directConnection=true"
MONGO_DB = "bench"

# Index names used on both engines (kept here so cores and tests agree).
PG_TABLE = "corpus"
MONGO_COLLECTION = "corpus"
ATLAS_TEXT_INDEX = "corpus_text"
ATLAS_VECTOR_INDEX = "corpus_vector"


@dataclass
class SearchConfig:
    """Everything needed to run one search benchmark (full-text or vector).

    The vector-index knobs (``pg_hnsw_ef_search`` and ``mongo_num_candidates``)
    are the fairness controls: ANN is approximate, so the only honest comparison
    tunes both engines toward a comparable recall before comparing latency.
    Recall is always reported next to latency for exactly this reason.
    """

    mode: str = "vector"  # "vector" | "fts"
    corpus_size: int = 5000
    dim: int = 256
    k: int = 10
    n_queries: int = 200
    seed: int = 1234
    trials: int = 1

    # pgvector HNSW build + search knobs.
    pg_hnsw_m: int = 16
    pg_hnsw_ef_construction: int = 64
    pg_hnsw_ef_search: int = 40

    # MongoDB $vectorSearch knob (candidate pool before the final top-k).
    mongo_num_candidates: int = 100

    # Full-text config. 'simple' avoids stemming so the Postgres analyzer, the
    # Lucene standard analyzer on the Atlas side, and the token-containment
    # precision check all see the same tokens.
    text_language: str = "simple"

    # Pinned to the search/ stack.
    pg_dsn: str = PG_DSN
    mongo_uri: str = MONGO_URI
    mongo_db: str = MONGO_DB

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "corpus_size": self.corpus_size,
            "dim": self.dim,
            "k": self.k,
            "n_queries": self.n_queries,
            "seed": self.seed,
            "trials": self.trials,
            "pg_hnsw_m": self.pg_hnsw_m,
            "pg_hnsw_ef_construction": self.pg_hnsw_ef_construction,
            "pg_hnsw_ef_search": self.pg_hnsw_ef_search,
            "mongo_num_candidates": self.mongo_num_candidates,
            "text_language": self.text_language,
        }


@dataclass
class SearchResult:
    engine: str  # "postgresql" | "mongodb"
    mode: str  # "vector" | "fts"
    label: str  # human-readable, e.g. "PostgreSQL 16 + pgvector (HNSW)"
    corpus_size: int
    dim: int
    k: int
    n_queries: int
    index_build_s: float
    index_size_mb: float
    latency_ms: dict[str, float]  # query latency: mean/p50/p95/p99/max
    recall_at_k: float
    qps: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def slug(self) -> str:
        eng = "psql" if self.engine == "postgresql" else "mdb"
        return f"search-{eng}-{self.mode}"


# --------------------------------------------------------------------------- #
# Multi-trial aggregation (mirrors basic.bench.aggregate_trials)
# --------------------------------------------------------------------------- #

def aggregate_search_trials(results: list["SearchResult"]) -> "SearchResult":
    """Collapse N per-trial search results into one whose scalar fields are the
    trial means, with per-metric variance recorded under ``extra["trials"]``."""
    if not results:
        raise ValueError("aggregate_search_trials requires at least one result")
    if len(results) == 1:
        return results[0]

    n = len(results)
    base = results[-1]

    def mean_of(attr: str) -> float:
        return sum(getattr(r, attr) for r in results) / n

    latency_ms = {
        key: sum(r.latency_ms.get(key, 0.0) for r in results) / n
        for key in results[0].latency_ms
    }

    extra = dict(base.extra)
    extra["trials"] = {
        "n": n,
        "latency_p99_ms": _agg([r.latency_ms.get("p99", 0.0) for r in results]),
        "recall_at_k": _agg([r.recall_at_k for r in results]),
        "index_build_s": _agg([r.index_build_s for r in results]),
        "qps": _agg([r.qps for r in results]),
    }

    return SearchResult(
        engine=base.engine,
        mode=base.mode,
        label=base.label,
        corpus_size=base.corpus_size,
        dim=base.dim,
        k=base.k,
        n_queries=base.n_queries,
        index_build_s=mean_of("index_build_s"),
        index_size_mb=mean_of("index_size_mb"),
        latency_ms=latency_ms,
        recall_at_k=mean_of("recall_at_k"),
        qps=mean_of("qps"),
        extra=extra,
    )


# --------------------------------------------------------------------------- #
# Comparison-set validation (used by compare_search.py)
# --------------------------------------------------------------------------- #

def search_workload_key(result: dict[str, Any]) -> tuple:
    """Fields that must match for two saved search results to be comparable."""
    return (
        result.get("mode"),
        result.get("corpus_size"),
        result.get("dim"),
        result.get("k"),
        result.get("n_queries"),
    )


def search_compatibility_warnings(results: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if len({search_workload_key(r) for r in results}) > 1:
        shapes = ", ".join(
            f"{r.get('slug', r.get('engine'))}=(mode={r.get('mode')}, "
            f"corpus={r.get('corpus_size')}, dim={r.get('dim')}, "
            f"k={r.get('k')}, queries={r.get('n_queries')})"
            for r in results
        )
        warnings.append(
            "results are not directly comparable -- differing workload shape: " + shapes
        )
    return warnings


# --------------------------------------------------------------------------- #
# Console rendering
# --------------------------------------------------------------------------- #

def print_search_result(result: SearchResult) -> None:
    lat = result.latency_ms
    recall_kind = result.extra.get("recall_kind", "")
    recall_label = "quality@k" + (f" ({recall_kind})" if recall_kind else "")
    rows = [
        ("engine", result.label),
        ("mode", result.mode),
        ("corpus / dim / k", f"{result.corpus_size} / {result.dim} / {result.k}"),
        ("queries", f"{result.n_queries}"),
        ("index build (s)", f"{result.index_build_s:.3f}"),
        ("index size (MB)", f"{result.index_size_mb:.3f}"),
        ("query throughput (qps)", f"{result.qps:,.0f}"),
        ("latency mean (ms)", f"{lat.get('mean', 0.0):.3f}"),
        ("latency p50 (ms)", f"{lat.get('p50', 0.0):.3f}"),
        ("latency p95 (ms)", f"{lat.get('p95', 0.0):.3f}"),
        ("latency p99 (ms)", f"{lat.get('p99', 0.0):.3f}"),
        (recall_label, f"{result.recall_at_k:.4f}"),
    ]
    trials = result.extra.get("trials")
    for key, value in result.extra.items():
        if key in ("trials",):
            continue
        rows.append((key, str(value)))
    if trials:
        rows.append(("trials", str(trials.get("n"))))
        for metric, unit in (
            ("latency_p99_ms", "ms"),
            ("recall_at_k", ""),
            ("index_build_s", "s"),
            ("qps", "qps"),
        ):
            a = trials.get(metric)
            if a:
                val = f"{a['mean']:,.4f} +/- {a['stdev']:,.4f} {unit} (cv {a['cv']:.1f}%)"
                rows.append((f"  {metric}", val))
    render_table(rows)
