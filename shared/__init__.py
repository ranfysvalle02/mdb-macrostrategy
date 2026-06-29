"""Tiny shared benchmark harness imported by both the ``basic`` and ``search``
angles. Engine-agnostic only -- no database drivers, no connection endpoints.

    from shared.common import summarize_latencies, split_ops, _agg
"""

from __future__ import annotations

from shared.common import (
    _WORDS,
    ProgressFn,
    _agg,
    _emit,
    _percentile,
    _word,
    load_result,
    now,
    render_table,
    split_ops,
    summarize_latencies,
    write_result,
)

__all__ = [
    "ProgressFn",
    "_agg",
    "_emit",
    "_percentile",
    "_word",
    "_WORDS",
    "load_result",
    "now",
    "render_table",
    "split_ops",
    "summarize_latencies",
    "write_result",
]
