"""Unit tests for the comparison-set validation helpers used by compare.py."""

from __future__ import annotations

from basic.bench import compatibility_warnings, workload_key


def _r(rows: int, doc_kb: int, workers: int, slug: str) -> dict:
    return {"rows": rows, "doc_kb": doc_kb, "workers": workers, "slug": slug}


def test_workload_key():
    assert workload_key(_r(500, 32, 4, "mdb")) == (500, 32, 4)


def test_no_warning_when_workloads_match():
    results = [_r(500, 32, 4, "psql"), _r(500, 32, 4, "mdb")]
    assert compatibility_warnings(results) == []


def test_warns_when_rows_differ():
    results = [_r(500, 32, 4, "psql"), _r(1000, 32, 4, "mdb")]
    warnings = compatibility_warnings(results)
    assert len(warnings) == 1
    assert "not directly comparable" in warnings[0]


def test_warns_when_doc_kb_differs():
    results = [_r(500, 32, 4, "psql"), _r(500, 64, 4, "mdb")]
    assert compatibility_warnings(results)
