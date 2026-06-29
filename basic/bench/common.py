"""Basic-angle config, seeded document generation, and update-result types.

This is the update-penalty workload: large documents whose hot fields are
mutated many times. The engine-agnostic statistics live in ``shared.common``;
this module holds only what is specific to the update benchmark -- including the
connection endpoints, which are pinned to the ``basic/`` docker-compose stack.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from typing import Any

from shared.common import _WORDS, _agg, _word, render_table

# Connection targets are pinned to the bundled basic/ docker-compose stack on
# purpose. This benchmark DROPs its table/collection on every run, so it must
# never be pointed at an arbitrary database. There are intentionally no
# environment-variable or CLI overrides. To change endpoints, edit
# basic/docker-compose.yml and these constants together (the host ports are
# non-standard for the same safety reason, and differ from the search/ stack).
PG_DSN = "host=localhost port=55432 dbname=bench user=postgres password=postgres"
MONGO_URI = "mongodb://localhost:57017/?directConnection=true"
MONGO_DB = "bench"


@dataclass
class BenchConfig:
    """Everything needed to run one update-penalty benchmark.

    Connection endpoints are fixed to the basic/ docker-compose stack (see the
    module constants above) and are intentionally not configurable; only the
    workload shape is tunable.
    """

    rows: int = 500
    ops: int = 20_000
    workers: int = 4
    doc_kb: int = 32
    seed: int = 1234
    warmup_frac: float = 0.1
    trials: int = 1  # repeat the whole run N times and report mean +/- stdev

    # Postgres -- pinned to the compose stack.
    pg_dsn: str = PG_DSN
    pg_strategy: str = "jsonb"  # "jsonb" (penalty under test) | "normalized" (idiomatic fix)
    pg_autovacuum: bool = True

    # MongoDB (Atlas Local is a single-node replica set -> directConnection=true).
    mongo_uri: str = MONGO_URI
    mongo_db: str = MONGO_DB

    def measured_ops(self) -> int:
        return max(1, self.ops - self.warmup_ops())

    def warmup_ops(self) -> int:
        return int(self.ops * self.warmup_frac)

    def to_public_dict(self) -> dict[str, Any]:
        """Config minus secrets/connection details, safe to echo to a UI."""
        return {
            "rows": self.rows,
            "ops": self.ops,
            "workers": self.workers,
            "doc_kb": self.doc_kb,
            "seed": self.seed,
            "warmup_frac": self.warmup_frac,
            "trials": self.trials,
            "pg_strategy": self.pg_strategy,
            "pg_autovacuum": self.pg_autovacuum,
        }


@dataclass
class BenchResult:
    engine: str  # "postgresql" | "mongodb"
    label: str  # human-readable, e.g. "PostgreSQL 16 (jsonb)"
    rows: int
    ops: int
    workers: int
    doc_kb: int
    duration_s: float
    throughput_ops_s: float
    latency_ms: dict[str, float]  # mean/p50/p95/p99/max
    size_before_mb: float
    size_after_mb: float
    size_growth_mb: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def slug(self) -> str:
        if self.engine == "postgresql":
            strat = self.extra.get("strategy", "jsonb")
            return f"psql-{strat}" if strat != "jsonb" else "psql"
        return "mdb"


# --------------------------------------------------------------------------- #
# Deterministic large-document generation
# --------------------------------------------------------------------------- #

def make_document(doc_kb: int, rng: random.Random) -> dict[str, Any]:
    """Build a ~doc_kb KB nested document with a hot `stats` sub-object.

    The document is padded with an `event_log` array until its serialized size
    reaches the target. At ~32 KB Postgres stores the value out-of-line in TOAST,
    which is exactly the condition that makes a partial JSONB update expensive.
    """
    doc: dict[str, Any] = {
        "schema_version": 3,
        "player": {
            "handle": _word(rng, 10),
            "guild": rng.choice(_WORDS),
            "level": rng.randint(1, 99),
            "region": rng.choice(["na", "eu", "apac", "sa", "me"]),
            "joined_ts": rng.randint(1_500_000_000, 1_700_000_000),
        },
        # The fields the benchmark mutates on every op.
        "stats": {
            "score": 0,
            "kills": 0,
            "deaths": 0,
            "status": "active",
            "last_seen": 0,
        },
        "inventory": [
            {
                "sku": _word(rng, 8),
                "qty": rng.randint(1, 20),
                "rarity": rng.choice(["common", "rare", "epic", "legendary"]),
                "bound": rng.random() < 0.3,
            }
            for _ in range(8)
        ],
        "event_log": [],
    }

    target_bytes = doc_kb * 1024
    log = doc["event_log"]
    # Pad until we hit the target size. Recompute length periodically to avoid
    # serializing the whole document on every iteration.
    while True:
        log.extend(
            {
                "t": rng.randint(1_600_000_000, 1_700_000_000),
                "kind": rng.choice(["login", "match", "purchase", "chat", "craft"]),
                "detail": _word(rng, 24),
                "value": rng.randint(0, 100_000),
            }
            for _ in range(8)
        )
        if len(json.dumps(doc, separators=(",", ":"))) >= target_bytes:
            break
    return doc


# --------------------------------------------------------------------------- #
# Multi-trial aggregation
# --------------------------------------------------------------------------- #

def aggregate_trials(results: list["BenchResult"]) -> "BenchResult":
    """Collapse N per-trial results into one whose scalar fields are the trial
    means, with per-metric variance recorded under ``extra["trials"]``.

    Keeping the scalar fields as means means every existing renderer (CLI table,
    dashboard, compare.py) keeps working unchanged; the variance is additive.
    """
    if not results:
        raise ValueError("aggregate_trials requires at least one result")
    if len(results) == 1:
        return results[0]

    n = len(results)
    base = results[-1]  # representative source for non-numeric extras

    def mean_of(attr: str) -> float:
        return sum(getattr(r, attr) for r in results) / n

    latency_ms = {
        k: sum(r.latency_ms.get(k, 0.0) for r in results) / n
        for k in results[0].latency_ms
    }

    extra = dict(base.extra)
    extra["trials"] = {
        "n": n,
        "throughput_ops_s": _agg([r.throughput_ops_s for r in results]),
        "latency_p99_ms": _agg([r.latency_ms.get("p99", 0.0) for r in results]),
        "size_growth_mb": _agg([r.size_growth_mb for r in results]),
    }

    return BenchResult(
        engine=base.engine,
        label=base.label,
        rows=base.rows,
        ops=base.ops,
        workers=base.workers,
        doc_kb=base.doc_kb,
        duration_s=mean_of("duration_s"),
        throughput_ops_s=mean_of("throughput_ops_s"),
        latency_ms=latency_ms,
        size_before_mb=mean_of("size_before_mb"),
        size_after_mb=mean_of("size_after_mb"),
        size_growth_mb=mean_of("size_growth_mb"),
        extra=extra,
    )


# --------------------------------------------------------------------------- #
# Comparison-set validation (used by compare.py)
# --------------------------------------------------------------------------- #

def workload_key(result: dict[str, Any]) -> tuple:
    """The fields that must match for two saved results to be comparable."""
    return (result.get("rows"), result.get("doc_kb"), result.get("workers"))


def compatibility_warnings(results: list[dict[str, Any]]) -> list[str]:
    """Return human-readable warnings if results describe different workloads."""
    warnings: list[str] = []
    if len({workload_key(r) for r in results}) > 1:
        shapes = ", ".join(
            f"{r.get('slug', r.get('engine'))}=(rows={r.get('rows')}, "
            f"doc_kb={r.get('doc_kb')}, workers={r.get('workers')})"
            for r in results
        )
        warnings.append(
            "results are not directly comparable -- differing workload shape: " + shapes
        )
    return warnings


# --------------------------------------------------------------------------- #
# Console rendering
# --------------------------------------------------------------------------- #

def print_result(result: BenchResult) -> None:
    lat = result.latency_ms
    rows = [
        ("engine", result.label),
        ("rows / ops / workers", f"{result.rows} / {result.ops} / {result.workers}"),
        ("doc size (KB)", f"{result.doc_kb}"),
        ("duration (s)", f"{result.duration_s:.2f}"),
        ("throughput (ops/s)", f"{result.throughput_ops_s:,.0f}"),
        ("latency mean (ms)", f"{lat['mean']:.3f}"),
        ("latency p50 (ms)", f"{lat['p50']:.3f}"),
        ("latency p95 (ms)", f"{lat['p95']:.3f}"),
        ("latency p99 (ms)", f"{lat['p99']:.3f}"),
        ("size before (MB)", f"{result.size_before_mb:.2f}"),
        ("size after (MB)", f"{result.size_after_mb:.2f}"),
        ("size growth (MB)", f"{result.size_growth_mb:+.2f}"),
    ]
    trials = result.extra.get("trials")
    for k, v in result.extra.items():
        if k == "trials":
            continue
        rows.append((k, str(v)))
    if trials:
        rows.append(("trials", str(trials.get("n"))))
        for metric, unit in (
            ("throughput_ops_s", "ops/s"),
            ("latency_p99_ms", "ms"),
            ("size_growth_mb", "MB"),
        ):
            a = trials.get(metric)
            if a:
                val = f"{a['mean']:,.3f} +/- {a['stdev']:,.3f} {unit} (cv {a['cv']:.1f}%)"
                rows.append((f"  {metric}", val))
    render_table(rows)
