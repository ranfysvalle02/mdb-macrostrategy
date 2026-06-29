"""Unit tests for the dependency-free shared harness (shared.common).

These never touch a database, so they run anywhere (including the CI lint+unit
job) in milliseconds.
"""

from __future__ import annotations

import statistics

import pytest

from shared.common import (
    _agg,
    _percentile,
    load_result,
    render_table,
    split_ops,
    summarize_latencies,
    write_result,
)

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
# _agg
# --------------------------------------------------------------------------- #

def test_agg_empty():
    a = _agg([])
    assert a == {"mean": 0.0, "stdev": 0.0, "cv": 0.0}


def test_agg_single_value_has_zero_stdev():
    a = _agg([5.0])
    assert a["mean"] == pytest.approx(5.0)
    assert a["stdev"] == 0.0


def test_agg_matches_statistics_stdev():
    a = _agg([100.0, 200.0])
    assert a["mean"] == pytest.approx(150.0)
    assert a["stdev"] == pytest.approx(statistics.stdev([100.0, 200.0]))
    assert a["cv"] == pytest.approx(a["stdev"] / a["mean"] * 100.0)


# --------------------------------------------------------------------------- #
# write_result / load_result (generic over any dataclass-like result)
# --------------------------------------------------------------------------- #

class _FakeResult:
    def to_dict(self):
        return {"throughput_ops_s": 2.0}

    def slug(self):
        return "fake"


def test_write_then_load_roundtrip(tmp_path):
    path = str(tmp_path / "out.json")
    write_result(_FakeResult(), path)
    loaded = load_result(path)
    assert loaded is not None
    assert loaded["throughput_ops_s"] == 2.0


def test_load_result_missing_returns_none(tmp_path):
    assert load_result(str(tmp_path / "nope.json")) is None


# --------------------------------------------------------------------------- #
# render_table
# --------------------------------------------------------------------------- #

def test_render_table_empty_is_noop(capsys):
    render_table([])
    assert capsys.readouterr().out == ""


def test_render_table_aligns_and_prints(capsys):
    render_table([("a", "1"), ("longer", "2")])
    out = capsys.readouterr().out
    assert "a : 1" in out
    assert "longer : 2" in out
