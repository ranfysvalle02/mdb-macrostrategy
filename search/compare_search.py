#!/usr/bin/env python3
"""CLI: render side-by-side tables from saved search-angle results.

Run as a module from the repo root:
    uv run python -m search.compare_search

Reads whichever of these exist in the current directory and prints a comparison
grouped by mode (vector, then full-text):
    results-search-psql-vector.json   results-search-mdb-vector.json
    results-search-psql-fts.json      results-search-mdb-fts.json

All numbers come from actual runs; nothing here is synthesized.
"""

from __future__ import annotations

from search.bench import load_result, search_compatibility_warnings

FILES = [
    "results-search-psql-vector.json",
    "results-search-mdb-vector.json",
    "results-search-psql-fts.json",
    "results-search-mdb-fts.json",
]

METRICS = [
    ("Index build (s)", lambda r: f"{r['index_build_s']:.3f}"),
    ("Index size (MB)", lambda r: (
        f"{r['index_size_mb']:.3f}" if r["index_size_mb"] else "n/a*"
    )),
    ("Query throughput (qps)", lambda r: f"{r['qps']:,.0f}"),
    ("Latency p50 (ms)", lambda r: f"{r['latency_ms']['p50']:.3f}"),
    ("Latency p95 (ms)", lambda r: f"{r['latency_ms']['p95']:.3f}"),
    ("Latency p99 (ms)", lambda r: f"{r['latency_ms']['p99']:.3f}"),
    ("Quality@k", lambda r: f"{r['recall_at_k']:.4f}"),
    ("Quality kind", lambda r: r["extra"].get("recall_kind", "-")),
    ("Trials", lambda r: str(r["extra"].get("trials", {}).get("n", 1))),
]


def _print_group(title: str, results: list[dict]) -> None:
    if not results:
        return
    first = results[0]
    print(f"\n{title}: corpus={first.get('corpus_size')} dim={first.get('dim')} "
          f"k={first.get('k')} queries={first.get('n_queries')}")
    for warning in search_compatibility_warnings(results):
        print(f"WARNING: {warning}")

    labels = [r["label"] for r in results]
    label_w = max(len(m[0]) for m in METRICS)
    col_w = max(24, *(len(c) for c in labels))

    def row(cells: list[str]) -> str:
        return f"{cells[0].ljust(label_w)}  " + "  ".join(c.ljust(col_w) for c in cells[1:])

    print(row(["metric"] + labels))
    print("-" * (label_w + 2 + (col_w + 2) * len(results)))
    for name, fn in METRICS:
        print(row([name] + [fn(r) for r in results]))
    if any(not r["index_size_mb"] for r in results):
        print("\n* Atlas Search index size is not exposed by Atlas Local; "
              "see search/README.md (index-size comparison is one-sided).")


def main() -> None:
    loaded = {f: load_result(f) for f in FILES}
    if not any(loaded.values()):
        print("No search result files found. Run demo_psql_search.py and "
              "demo_mdb_search.py first (per mode).")
        return

    vector = [r for f, r in loaded.items() if r is not None and r.get("mode") == "vector"]
    fts = [r for f, r in loaded.items() if r is not None and r.get("mode") == "fts"]
    _print_group("VECTOR SEARCH", vector)
    _print_group("FULL-TEXT SEARCH", fts)
    print()


if __name__ == "__main__":
    main()
