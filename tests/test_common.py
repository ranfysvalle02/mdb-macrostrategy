"""Unit tests for the dependency-free benchmark harness (bench.common).

These never touch a database, so they run anywhere (including the CI lint+unit
job) in milliseconds.
"""

from __future__ import annotations

import json
import random
import statistics

import pytest

from bench import (
    BenchConfig,
    BenchResult,
    aggregate_trials,
    load_result,
    make_document,
    write_result,
)
from bench.common import _percentile, split_ops, summarize_latencies

# --------------------------------------------------------------------------- #
# _percentile
# --------------------------------------------------------------------------- #

def test_percentile_empty_returns_zero():
    assert _percentile([], 0.5) == 0.0


def test_percentile_single_value():
    assert _percentile([3.0], 0.99) == 3.0


def test_percentile_interpolates_on_known_series():
    vals = [float(i) for i in range(1, 101)]  # 1..100 inclusive
    assert _percentile(vals, 0.0) == 1.0
    assert _percentile(vals, 1.0) == 100.0
    assert _percentile(vals, 0.50) == pytest.approx(50.5)


# --------------------------------------------------------------------------- #
# summarize_latencies
# --------------------------------------------------------------------------- #

def test_summarize_latencies_empty():
    assert summarize_latencies([]) == {
        "mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0,
    }


def test_summarize_latencies_converts_seconds_to_ms():
    s = summarize_latencies([0.001, 0.002, 0.003])  # 1, 2, 3 ms
    assert s["mean"] == pytest.approx(2.0)
    assert s["p50"] == pytest.approx(2.0)
    assert s["max"] == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# split_ops
# --------------------------------------------------------------------------- #

def test_split_ops_even():
    assert split_ops(100, 4) == [25, 25, 25, 25]


def test_split_ops_distributes_remainder_to_first_workers():
    parts = split_ops(10, 3)
    assert parts == [4, 3, 3]
    assert sum(parts) == 10


def test_split_ops_more_workers_than_ops():
    parts = split_ops(2, 5)
    assert sum(parts) == 2
    assert len(parts) == 5


def test_split_ops_zero_workers_guard():
    assert split_ops(10, 0) == [10]


# --------------------------------------------------------------------------- #
# make_document
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("doc_kb", [1, 8, 32])
def test_make_document_hits_target_size(doc_kb):
    doc = make_document(doc_kb, random.Random(7))
    size = len(json.dumps(doc, separators=(",", ":")))
    assert size >= doc_kb * 1024


def test_make_document_is_deterministic_for_equal_seed():
    assert make_document(8, random.Random(42)) == make_document(8, random.Random(42))


def test_make_document_has_hot_fields():
    doc = make_document(4, random.Random(1))
    assert doc["stats"]["score"] == 0
    assert "last_seen" in doc["stats"]


# --------------------------------------------------------------------------- #
# BenchConfig / BenchResult
# --------------------------------------------------------------------------- #

def test_config_warmup_and_measured_ops():
    cfg = BenchConfig(ops=1000, warmup_frac=0.1)
    assert cfg.warmup_ops() == 100
    assert cfg.measured_ops() == 900


def test_config_to_public_dict_includes_trials_and_hides_connections():
    pub = BenchConfig(trials=5).to_public_dict()
    assert pub["trials"] == 5
    assert "pg_dsn" not in pub
    assert "mongo_uri" not in pub


def _result(engine: str, extra: dict | None = None) -> BenchResult:
    return BenchResult(
        engine=engine,
        label="x",
        rows=1, ops=1, workers=1, doc_kb=1,
        duration_s=0.0, throughput_ops_s=0.0,
        latency_ms={}, size_before_mb=0.0, size_after_mb=0.0, size_growth_mb=0.0,
        extra=extra or {},
    )


def test_slug_variants():
    assert _result("postgresql", {"strategy": "jsonb"}).slug() == "psql"
    assert _result("postgresql", {"strategy": "normalized"}).slug() == "psql-normalized"
    assert _result("mongodb").slug() == "mdb"


# --------------------------------------------------------------------------- #
# write_result / load_result
# --------------------------------------------------------------------------- #

def test_write_then_load_roundtrip(tmp_path):
    r = BenchResult(
        engine="mongodb", label="m", rows=1, ops=1, workers=1, doc_kb=1,
        duration_s=1.0, throughput_ops_s=2.0, latency_ms={"p99": 1.0},
        size_before_mb=1.0, size_after_mb=2.0, size_growth_mb=1.0,
    )
    path = str(tmp_path / "out.json")
    write_result(r, path)
    loaded = load_result(path)
    assert loaded is not None
    assert loaded["throughput_ops_s"] == 2.0


def test_load_result_missing_returns_none(tmp_path):
    assert load_result(str(tmp_path / "nope.json")) is None


# --------------------------------------------------------------------------- #
# aggregate_trials
# --------------------------------------------------------------------------- #

def _trial(throughput: float, p99: float, growth: float) -> BenchResult:
    return BenchResult(
        engine="mongodb", label="m", rows=10, ops=100, workers=4, doc_kb=32,
        duration_s=1.0, throughput_ops_s=throughput,
        latency_ms={"mean": p99, "p50": p99, "p95": p99, "p99": p99, "max": p99},
        size_before_mb=5.0, size_after_mb=5.0 + growth, size_growth_mb=growth,
        extra={"server_version": "8.3"},
    )


def test_aggregate_trials_empty_raises():
    with pytest.raises(ValueError):
        aggregate_trials([])


def test_aggregate_trials_single_passthrough():
    r = _trial(100, 1.0, 2.0)
    assert aggregate_trials([r]) is r


def test_aggregate_trials_means_and_stdev():
    agg = aggregate_trials([_trial(100, 1.0, 2.0), _trial(200, 3.0, 4.0)])
    assert agg.throughput_ops_s == pytest.approx(150.0)
    assert agg.size_growth_mb == pytest.approx(3.0)
    assert agg.latency_ms["p99"] == pytest.approx(2.0)

    t = agg.extra["trials"]
    assert t["n"] == 2
    assert t["throughput_ops_s"]["mean"] == pytest.approx(150.0)
    assert t["throughput_ops_s"]["stdev"] == pytest.approx(statistics.stdev([100, 200]))
    assert t["size_growth_mb"]["stdev"] == pytest.approx(statistics.stdev([2.0, 4.0]))


def test_aggregate_trials_preserves_representative_extra():
    agg = aggregate_trials([_trial(100, 1.0, 2.0), _trial(200, 3.0, 4.0)])
    assert agg.extra["server_version"] == "8.3"
