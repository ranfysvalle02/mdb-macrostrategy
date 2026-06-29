"""End-to-end tests that exercise the real engines.

These require the docker-compose stack (``docker compose up -d``) and are marked
``integration`` so the fast unit job can skip them with ``-m "not integration"``.
The CI integration job runs them with ``-m integration`` after bringing the
stack up. The workload is intentionally tiny so the round-trip stays quick.
"""

from __future__ import annotations

import math

import pytest

from basic.bench import BenchConfig
from basic.bench import mongo as bench_mongo
from basic.bench import pg as bench_pg

pytestmark = pytest.mark.integration


def _cfg() -> BenchConfig:
    return BenchConfig(rows=50, ops=2000, workers=2, doc_kb=8, trials=2)


def _assert_sane(result, engine: str, cfg: BenchConfig) -> None:
    assert result.engine == engine
    assert result.throughput_ops_s > 0
    assert result.ops == cfg.measured_ops()
    assert math.isfinite(result.size_before_mb)
    assert math.isfinite(result.size_growth_mb)
    assert result.latency_ms["p99"] >= 0
    assert result.extra["trials"]["n"] == cfg.trials


def test_pg_jsonb_end_to_end():
    cfg = _cfg()
    _assert_sane(bench_pg.run(cfg), "postgresql", cfg)


def test_pg_normalized_end_to_end():
    cfg = BenchConfig(rows=50, ops=2000, workers=2, doc_kb=8, trials=2, pg_strategy="normalized")
    _assert_sane(bench_pg.run(cfg), "postgresql", cfg)


def test_mongo_end_to_end():
    cfg = _cfg()
    _assert_sane(bench_mongo.run(cfg), "mongodb", cfg)
