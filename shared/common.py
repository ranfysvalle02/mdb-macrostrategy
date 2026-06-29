"""Engine- and workload-agnostic benchmark primitives shared by both angles.

This module is intentionally dependency-free (stdlib only) so that the ``basic``
(update-penalty) and ``search`` (full-text + vector) angles, their CLIs, and
their FastAPI dashboards can all import it without pulling in a database driver
they do not need.

It holds only things that do not depend on a particular engine or workload:
latency statistics, op partitioning, trial-variance aggregation, deterministic
text primitives, JSON result persistence, and a small console table renderer.
Connection endpoints and workload-specific config/result dataclasses live in
each angle's own ``bench`` package, because they differ per angle (the two
docker-compose stacks bind different ports on purpose).
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import string
import time
from typing import Any, Callable, Optional, Protocol

# A progress hook receives (phase_label, fraction_complete in [0, 1]).
ProgressFn = Optional[Callable[[str, float], None]]


def _emit(progress: ProgressFn, phase: str, frac: float) -> None:
    if progress is not None:
        progress(phase, max(0.0, min(1.0, frac)))


def now() -> float:
    return time.perf_counter()


# --------------------------------------------------------------------------- #
# Deterministic text primitives (shared by document and corpus generation)
# --------------------------------------------------------------------------- #

_WORDS = [
    "atlas", "vector", "shard", "replica", "oplog", "wired", "tiger", "index",
    "pipeline", "cluster", "tundra", "ember", "quartz", "nimbus", "vertex",
    "cobalt", "zephyr", "onyx", "raptor", "lumen", "cipher", "delta", "echo",
]


def _word(rng: random.Random, n: int = 6) -> str:
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(n))


# --------------------------------------------------------------------------- #
# Latency statistics
# --------------------------------------------------------------------------- #

def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def summarize_latencies(latencies_s: list[float]) -> dict[str, float]:
    """Convert a list of per-op latencies (seconds) into ms summary stats."""
    if not latencies_s:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    ms = sorted(v * 1000.0 for v in latencies_s)
    return {
        "mean": sum(ms) / len(ms),
        "p50": _percentile(ms, 0.50),
        "p95": _percentile(ms, 0.95),
        "p99": _percentile(ms, 0.99),
        "max": ms[-1],
    }


def split_ops(total: int, workers: int) -> list[int]:
    """Divide `total` ops as evenly as possible across `workers`."""
    workers = max(1, workers)
    base = total // workers
    rem = total % workers
    return [base + (1 if i < rem else 0) for i in range(workers)]


# --------------------------------------------------------------------------- #
# Trial-variance aggregation
# --------------------------------------------------------------------------- #

def _agg(values: list[float]) -> dict[str, float]:
    """mean / sample-stdev / coefficient-of-variation for a list of trial values."""
    n = len(values)
    mean = sum(values) / n if n else 0.0
    sd = statistics.stdev(values) if n >= 2 else 0.0
    cv = (sd / mean * 100.0) if mean else 0.0
    return {"mean": mean, "stdev": sd, "cv": cv}


# --------------------------------------------------------------------------- #
# Result persistence + console rendering
# --------------------------------------------------------------------------- #

class _Result(Protocol):
    """Structural type both angles' result dataclasses satisfy."""

    def to_dict(self) -> dict[str, Any]: ...

    def slug(self) -> str: ...


def write_result(result: _Result, path: Optional[str] = None) -> str:
    path = path or f"results-{result.slug()}.json"
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return path


def load_result(path: str) -> Optional[dict[str, Any]]:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def render_table(rows: list[tuple[str, str]]) -> None:
    """Print an aligned ``key : value`` table bracketed by dashed rules.

    Both angles build their own list of rows (the metrics differ) and hand it
    here so the console formatting lives in exactly one place.
    """
    if not rows:
        return
    width = max(len(k) for k, _ in rows)
    print("-" * (width + 30))
    for k, v in rows:
        print(f"{k.rjust(width)} : {v}")
    print("-" * (width + 30))
