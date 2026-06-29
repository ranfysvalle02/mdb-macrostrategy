# The JSONB Update Penalty: PostgreSQL vs MongoDB

*The first angle. It tests one of the most-cited technical claims about MongoDB —
that frequent partial updates to a field buried inside a large document are
dramatically cheaper than on PostgreSQL `jsonb` — by measuring it instead of
asserting it. The companion [search angle](../search/README.md) asks a different
question (full-text + vector on the same record). The strategy essay lives in
[../TLDR.md](../TLDR.md).*

---

## What actually happens on an update

A frequently repeated claim is that "MongoDB edits bytes in place while Postgres
rewrites the whole row." The first half is wrong, so it is worth stating the
mechanism precisely:

- **PostgreSQL `jsonb` + MVCC + TOAST.** A large `jsonb` value (anything past a
  couple of KB) is stored out-of-line in a TOAST table, and TOAST chunks are
  immutable. Updating one key inside it cannot patch the value: Postgres writes a
  new row version and a *new* TOAST value, orphaning the old one as dead data for
  autovacuum to reclaim later. Frequent updates to a big `jsonb` therefore cause
  heavy write amplification and storage bloat.
- **MongoDB field-level operators + WiredTiger.** `$inc` / `$set` send only the
  changed fields, and WiredTiger applies the modification through its own update
  structures. Crucially, **WiredTiger is also an MVCC / copy-on-write engine — it
  does not edit bytes in place on disk either.** What it avoids is the
  full-document-rewrite-plus-vacuum cycle that a TOASTed `jsonb` update triggers.

The difference is real, but it is about *write amplification on large documents*,
not in-place byte surgery. So this benchmark measures it.

---

## The workload

Seed 500 documents of ~32 KB each, then apply 18,000 measured partial updates
(`$inc` a counter, `$set` a timestamp on two fields buried inside each document)
across 4 concurrent workers. Identical logical data and workload on both engines.

The Postgres update is the naive, document-store-style move (mutate keys inside
the TOASTed `jsonb`); the MongoDB update is the natural one (`$inc`/`$set`). A
**fairness control** — Postgres `normalized` mode — promotes the hot counters to
real columns so the big document stays in a `jsonb` the workload never touches.
If a benchmark can't argue against itself, it's marketing; this one can, and
`normalized` is the headline winner.

---

## Evidence

Mean of 5 trials (± sample standard deviation) from a local run on Apple Silicon
macOS via Docker Desktop (PostgreSQL 16.14, MongoDB 8.3.4 Atlas Local). Your
numbers will differ — that is the point.

| Metric | PostgreSQL (`jsonb`) | PostgreSQL (`normalized`) | MongoDB |
| --- | ---: | ---: | ---: |
| Throughput (ops/s) | 4,189 ± 180 | **10,264 ± 403** | 3,005 ± 581 |
| Latency p50 (ms) | 0.834 | **0.356** | 0.990 |
| Latency p99 (ms) | 2.813 ± 0.666 | **0.781 ± 0.151** | 4.642 ± 1.106 |
| Storage before (MB) | 8.55 | 8.55 | 9.13 |
| Storage after (MB) | 328.00 | 8.57 | 18.67 |
| **Storage growth (MB)** | **+319.45 ± 36.93** | **+0.01** | **+9.53 ± 0.10** |

1. **The JSONB penalty is real and large — for storage, not speed.** Burying a
   hot field in a 32 KB `jsonb` ballooned the TOAST table from ~8.5 MB to ~328 MB
   over 18k updates (~38x write amplification). The absolute bloat swings run to
   run with autovacuum timing (±37 MB here), but it never lands anywhere near
   MongoDB, which grew ~9.5 MB for the same work.
2. **On raw speed, naive `jsonb` actually edged MongoDB here** (4,189 vs 3,005
   ops/s, lower p99 — though both tails are noisy). MongoDB's win on this workload
   is about avoiding bloat, not raw latency.
3. **Idiomatic Postgres wins this micro-benchmark outright.** Promote the hot
   counters to real columns (`normalized`) and the penalty vanishes (+0.01 MB)
   *and* it becomes the fastest option (10,264 ops/s). The lesson is not "Mongo
   beats Postgres." It is: don't bury a hot field in a large TOASTed `jsonb`.

The defensible takeaway is ergonomic, not triumphalist: with MongoDB the natural
document-shaped modeling choice is *also* the efficient one; with Postgres `jsonb`
you can match or beat it, but you have to know to normalize the hot path.

---

## Run it yourself

Requirements: Docker, [`uv`](https://docs.astral.sh/uv/). Commands run from the
repo root.

```bash
# 1. Start the basic stack: Postgres 16 + MongoDB Atlas Local 8.3.
docker compose -f basic/docker-compose.yml up -d

# 2. Wait until both are healthy.
docker compose -f basic/docker-compose.yml ps

# 3a. CLI (--trials N reports mean +/- stdev).
uv run python -m basic.demo_psql --trials 5                       # jsonb strategy
uv run python -m basic.demo_psql --strategy normalized --trials 5 # the fair fight
uv run python -m basic.demo_mdb --trials 5
uv run python -m basic.compare                                    # side-by-side table

# 3b. ...or the dashboard.
uv run uvicorn basic.app:app --reload                             # http://localhost:8000
```

Tear down with `docker compose -f basic/docker-compose.yml down`.

> **Bound to this stack on purpose.** The benchmark drops and recreates its table
> and collection on every run, so it only ever talks to the bundled basic stack.
> Host ports are deliberately non-standard (Postgres `55432`, MongoDB `57017`) and
> differ from the search stack (`55433` / `57018`) so the two angles can run side
> by side and neither can grab a real database. Endpoints live as constants in
> `basic/bench/common.py` and must match `basic/docker-compose.yml`.

Develop and test:

```bash
uv run pytest -m "not integration"        # fast, no databases
uv run pytest basic/tests -m integration  # end-to-end (needs the basic stack)
uv run ruff check
```

---

## Methodology and caveats

- One workload, one machine, via Docker — not a general statement about reads,
  analytics, joins, or production tuning.
- Each figure is the **mean of 5 trials** with the sample standard deviation
  shown (`--trials N`). The seed is fixed across trials, so the variance reflects
  measurement noise, not different input data. Tail latency (p99) is the noisiest
  metric. The `normalized` and MongoDB storage numbers are rock-steady, but
  `jsonb` growth itself swings with autovacuum timing — the order-of-magnitude
  gap between them never does.
- `normalized` mode is included precisely so the benchmark can argue against
  itself; it is the idiomatic Postgres fix and the headline winner.
- autovacuum is left on (the realistic default). A `--no-autovacuum` flag exists
  to show worst-case behavior.
- Both engines compress on disk (Postgres TOAST / WiredTiger snappy), so the size
  comparison is apples-to-apples on compressed bytes.
- Numbers come from real runs and are never hand-edited into this file.

---

## References

- [PostgreSQL: TOAST](https://www.postgresql.org/docs/current/storage-toast.html)
- [PostgreSQL: Concurrency Control (MVCC)](https://www.postgresql.org/docs/current/mvcc-intro.html)
- [PostgreSQL: Routine Vacuuming](https://www.postgresql.org/docs/current/routine-vacuuming.html)
- [MongoDB: WiredTiger Storage Engine](https://www.mongodb.com/docs/manual/core/wiredtiger/)
- [MongoDB: Update Operators](https://www.mongodb.com/docs/manual/reference/operator/update/)
