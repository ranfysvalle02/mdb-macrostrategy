#!/usr/bin/env python3
"""CLI: render a side-by-side table from saved basic-angle benchmark results.

Run as a module from the repo root:
    uv run python -m basic.compare

Reads whichever of these exist in the current directory and prints a comparison:
    results-psql.json             (PostgreSQL, jsonb strategy)
    results-psql-normalized.json  (PostgreSQL, normalized strategy)
    results-mdb.json              (MongoDB)

All numbers come from actual runs; nothing here is synthesized.
"""

from __future__ import annotations

from basic.bench import compatibility_warnings, load_result

FILES = [
    "results-psql.json",
    "results-psql-normalized.json",
    "results-mdb.json",
]


def main() -> None:
    results = [r for r in (load_result(f) for f in FILES) if r is not None]
    if not results:
        print("No result files found. Run -m basic.demo_psql and -m basic.demo_mdb first.")
        return

    first = results[0]
    trials = {r.get("extra", {}).get("trials", {}).get("n", 1) for r in results}
    trials_str = str(next(iter(trials))) if len(trials) == 1 else "mixed"
    print(
        f"\nWorkload: rows={first.get('rows')} "
        f"doc_kb={first.get('doc_kb')} workers={first.get('workers')} trials={trials_str}"
    )
    for warning in compatibility_warnings(results):
        print(f"WARNING: {warning}")

    metrics = [
        ("Engine", lambda r: r["label"]),
        ("Throughput (ops/s)", lambda r: f"{r['throughput_ops_s']:,.0f}"),
        ("Latency p50 (ms)", lambda r: f"{r['latency_ms']['p50']:.3f}"),
        ("Latency p95 (ms)", lambda r: f"{r['latency_ms']['p95']:.3f}"),
        ("Latency p99 (ms)", lambda r: f"{r['latency_ms']['p99']:.3f}"),
        ("Size before (MB)", lambda r: f"{r['size_before_mb']:.2f}"),
        ("Size after (MB)", lambda r: f"{r['size_after_mb']:.2f}"),
        ("Size growth (MB)", lambda r: f"{r['size_growth_mb']:+.2f}"),
        ("Trials", lambda r: str(r["extra"].get("trials", {}).get("n", 1))),
        ("Measured ops", lambda r: f"{r['ops']:,}"),
        ("Dead tuples", lambda r: str(r["extra"].get("n_dead_tup", "-"))),
        ("HOT updates", lambda r: str(r["extra"].get("n_tup_hot_upd", "-"))),
    ]

    col_labels = [r["slug"] if "slug" in r else r["engine"] for r in results]
    label_w = max(len(m[0]) for m in metrics)
    col_w = max(24, *(len(c) for c in col_labels))

    def row(cells: list[str]) -> str:
        head = cells[0].ljust(label_w)
        rest = "  ".join(c.ljust(col_w) for c in cells[1:])
        return f"{head}  {rest}"

    print()
    print(row(["metric"] + [r["label"] for r in results]))
    print("-" * (label_w + 2 + (col_w + 2) * len(results)))
    for name, fn in metrics:
        if name == "Engine":
            continue
        print(row([name] + [fn(r) for r in results]))
    print()


if __name__ == "__main__":
    main()
