# mdb-macrostrategy

[![CI](https://github.com/ranfysvalle02/mdb-macrostrategy/actions/workflows/ci.yml/badge.svg)](https://github.com/ranfysvalle02/mdb-macrostrategy/actions/workflows/ci.yml)

A short, deliberately honest essay about MongoDB's platform strategy, paired with
a **reproducible benchmark** (and a small FastAPI dashboard) that measures one of
its most-cited technical claims instead of asserting it.

The essay is an argument; treat it as opinion. The numbers in
[Evidence](#evidence-a-reproducible-benchmark) are facts you can regenerate on
your own machine in a couple of minutes.

---

# The Consolidation Play: Why MongoDB's Real Value is its Macrostrategy

There is a running joke in backend engineering: whenever a new data paradigm
catches fire, wait six months, and MongoDB will absorb it.

If you view databases purely through the lens of micro-benchmarks, this looks like
a jack-of-all-trades, master-of-none strategy. Viewed through the lens of
*macrostrategy*, it reads differently. Choosing a database isn't only a technical
evaluation of what it can do today; it is a multi-year bet on a platform's ability
to evolve with you. That framing is a point of view, not a proven fact, and the
rest of this document tries to be explicit about which is which.

---

## The Best-of-Breed Tax

For years, architectural wisdom dictated choosing "the right tool for the job."
Need time-series data? Spin up InfluxDB. Need a graph? Deploy Neo4j. Need vector
search for AI? Add Pinecone.

On paper this is pristine. In practice it introduces real operational cost:

* Multiple query languages for your developers to learn.
* Data-syncing pipelines between stores (and the consistency bugs they bring).
* Fragmented security models, compliance audits, and billing overhead.

These costs are real, but they are not free to avoid either. The honest claim is
narrow: **as the number of specialized stores grows, integration cost tends to
grow faster than the performance you bought by specializing.** For a small number
of stores, best-of-breed is often the right call.

---

## The Pattern of Absorption

Rather than forcing a migration when the industry shifts, MongoDB tends to extend
its engine to handle the new paradigm. These are real, shipped primitives:

* **Time-Series Collections** for sequential/IoT data, with column-oriented
  storage and automatic bucketing.
* **`$graphLookup`** for recursive/graph-style traversals inside the aggregation
  pipeline.
* **Atlas Vector Search** (`$vectorSearch`), which keeps embeddings in the same
  document as the operational data they describe.

Whether these match a dedicated engine at the extreme end is a separate question
(see the caveat below). The point of the pattern is consolidation, not winning
every micro-benchmark.

### Queryable Encryption

One unusual capability worth calling out factually: **Queryable Encryption** lets
an application encrypt fields client-side, store them encrypted, and still run a
constrained set of server-side queries (equality and range) over the ciphertext
without the server decrypting it. It is genuinely uncommon among general-purpose
databases. It is not magic: the supported query set is limited and there are
storage and performance costs. It solves a specific trust problem well.

---

## The Honest Caveat: Specialized Engines Still Win at the Edges

Purists will point out that a dedicated vector engine (Milvus, Pinecone) can beat
MongoDB's vector search at extreme scale and tail latency, and that a tuned
time-series database can out-compress and out-query Time-Series Collections.
**They are right.** At billion-scale indexes, sub-millisecond budgets, or
specialized tuning, purpose-built systems lead.

The consolidation argument doesn't dispute that. It argues that for many teams the
integration and operational savings outweigh that delta — and, importantly, that
"many" is not a number anyone should invent. Whether *your* workload sits at the
edge where specialization pays for its overhead is an empirical question about
your data, not a slogan. The next section is an attempt to answer one such
question honestly.

---

## What Actually Happens on an Update

A frequently repeated claim is that "MongoDB edits bytes in place while Postgres
rewrites the whole row." The first half is wrong, so it is worth stating the
mechanism precisely:

* **PostgreSQL `jsonb` + MVCC + TOAST.** A large `jsonb` value (anything past a
  couple of KB) is stored out-of-line in a TOAST table, and TOAST chunks are
  immutable. Updating one key inside it cannot patch the value: Postgres writes a
  new row version and a *new* TOAST value, orphaning the old one as dead data for
  autovacuum to reclaim later. Frequent updates to a big `jsonb` therefore cause
  heavy write amplification and storage bloat.
* **MongoDB field-level operators + WiredTiger.** `$inc` / `$set` send only the
  changed fields, and WiredTiger applies the modification through its own update
  structures. Crucially, **WiredTiger is also an MVCC / copy-on-write engine — it
  does not edit bytes in place on disk either.** What it avoids is the
  full-document-rewrite-plus-vacuum cycle that a TOASTed `jsonb` update triggers.

The difference is real, but it is about *write amplification on large documents*,
not in-place byte surgery. So I measured it.

---

## Evidence: A Reproducible Benchmark

The workload: seed 500 documents of ~32 KB each, then apply 18,000 measured
partial updates (`$inc` a counter, `$set` a timestamp on two fields buried inside
each document) across 4 concurrent workers. Identical logical data and workload on
both engines. Numbers below are the **mean of 5 trials (± sample standard
deviation)** from a local run on Apple Silicon macOS via Docker Desktop
(PostgreSQL 16.14, MongoDB 8.3.4 Atlas Local) — your numbers will differ; that is
the point.

| Metric | PostgreSQL (`jsonb`) | PostgreSQL (`normalized`) | MongoDB |
| --- | ---: | ---: | ---: |
| Throughput (ops/s) | 4,229 ± 265 | **10,417 ± 198** | 3,561 ± 128 |
| Latency p50 (ms) | 0.832 | **0.360** | 0.872 |
| Latency p99 (ms) | 2.596 ± 0.913 | **0.702 ± 0.134** | 3.173 ± 0.767 |
| Storage before (MB) | 8.55 | 8.55 | 9.13 |
| Storage after (MB) | 344.51 | 8.57 | 18.65 |
| **Storage growth (MB)** | **+335.96 ± 0.02** | **+0.02** | **+9.51 ± 0.12** |

What the data actually says — including the parts that are *not* flattering to the
"just use MongoDB" reflex:

1. **The JSONB penalty is real and large — for storage, not speed.** Burying a hot
   field in a 32 KB `jsonb` ballooned the TOAST table from ~8 MB to ~344 MB over
   18k updates (~40x write amplification), and it was the most consistent number
   in the whole run (±0.02 MB across trials). MongoDB grew ~9.5 MB for the same
   work.
2. **On raw speed, naive `jsonb` actually edged MongoDB here** — higher throughput
   (4,229 vs 3,561 ops/s) and a lower p99, though the tail is noisy (note the
   stdevs). MongoDB's advantage on this workload is overwhelmingly about avoiding
   bloat, not raw speed.
3. **Idiomatic Postgres wins this micro-benchmark outright.** Promote the hot
   counters to real columns (`normalized`) and the penalty vanishes (+0.02 MB)
   *and* it becomes the fastest option (10,417 ops/s) with the tightest variance.
   The lesson is not "Mongo beats Postgres." It is: don't bury a hot field in a
   large TOASTed `jsonb`.

The defensible version of the macrostrategy claim, then, is ergonomic rather than
triumphalist: with MongoDB, the natural document-shaped modeling choice (embed it,
update fields in place with operators) is *also* the efficient one. With Postgres
`jsonb` you can match or beat it, but you have to know to normalize the hot path.

### Methodology and Caveats

* One workload, one machine, via Docker — not a general statement about reads,
  analytics, joins, or production tuning.
* Each figure is the **mean of 5 trials** with the sample standard deviation shown
  (`--trials N` on the CLI, or the Trials control on the dashboard). The seed is
  held fixed across trials, so the variance reflects measurement noise rather than
  different input data. Tail latency (p99) is the noisiest metric; storage growth
  is the most stable.
* `normalized` mode is included precisely so the benchmark can argue against
  itself; it is the idiomatic Postgres fix and it is the headline winner.
* autovacuum is left on (the realistic default); it reclaims dead tuples lazily,
  but the TOAST table had already grown — only `VACUUM FULL` returns space to the
  OS. A `--no-autovacuum` flag exists to show worst-case behavior.
* Both engines compress on disk (Postgres TOAST / WiredTiger snappy), so the size
  comparison is apples-to-apples on compressed bytes.
* Numbers come from real runs and are never hand-edited into this file.

---

## Run It Yourself

Requirements: Docker, [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Start Postgres 16 + MongoDB 8.3 (Atlas Local). First pull is large.
docker compose up -d

# 2. Wait until both are healthy
docker compose ps

# 3a. Run from the CLI (add --trials N to report mean +/- stdev)
uv run python demo-psql.py --trials 5                       # jsonb strategy
uv run python demo-psql.py --strategy normalized --trials 5 # idiomatic fix
uv run python demo-mdb.py --trials 5
uv run python compare.py                                    # side-by-side table

# 3b. ...or use the dashboard
uv run uvicorn app:app --reload                             # open http://localhost:8000
```

Develop and test:

```bash
uv run pytest -m "not integration"   # fast, no databases required
uv run pytest -m integration         # end-to-end (needs the stack up)
uv run ruff check                     # lint
```

The dashboard lets you set the workload (rows, ops, concurrency, document size,
Postgres strategy), runs both engines with a live progress stream, and renders the
throughput / p99 latency / storage-growth comparison with the same
Methodology & Caveats shown above. Tear everything down with `docker compose down`.

> **Bound to this stack on purpose.** The benchmark drops and recreates its table
> and collection on every run, so it only ever talks to the bundled
> docker-compose databases — there are no DSN/URI overrides. The host ports are
> deliberately non-standard (Postgres `55432`, MongoDB `57017`) so it cannot grab a
> real database you happen to be running on the usual `5432` / `27017`. To connect
> manually: `psql "host=localhost port=55432 dbname=bench user=postgres password=postgres"`
> or `mongosh "mongodb://localhost:57017/?directConnection=true"`. Endpoints live
> as constants in `bench/common.py` and must match `docker-compose.yml`. If a
> database is unreachable, the demos and dashboard fail fast with a
> `docker compose up -d` hint rather than a raw driver traceback.

> **Editing `docker-compose.yml` (e.g. changing ports)?** Use a full
> `docker compose down && docker compose up -d`. An in-place `up -d` recreate can
> leave the Atlas Local single-node replica set stuck in an `RSGhost` /
> `ReplicaSetNoPrimary` state; a clean down/up re-initializes it correctly.

Repository layout:

* `bench/` — the benchmark package: `common.py` (config, seeded documents, stats,
  trial aggregation) and the engine cores `pg.py` / `mongo.py` (the single source
  of truth).
* `demo-psql.py` / `demo-mdb.py` / `compare.py` — CLI entrypoints.
* `app.py` + `static/` — FastAPI dashboard.
* `tests/` — unit tests (no DB) plus `integration`-marked end-to-end tests.
* `docker-compose.yml` — Postgres 16 + MongoDB Atlas Local 8.3.
* `.github/workflows/ci.yml` — lint + unit tests, and an integration job that
  spins up the compose stack.

---

## Bet on the Engine, Not Just the Features

The honest, narrowed thesis: when you choose MongoDB you are betting that one
engine, evolving its query layer instead of asking you to bolt on another store,
will keep the ergonomic path and the efficient path aligned for document-shaped,
update-heavy data. That is a real advantage for a real class of workloads — and,
as the benchmark shows, a well-modeled relational schema remains a formidable
competitor everywhere else.

Convergence is itself evidence the document model was a reasonable bet:
PostgreSQL added `jsonb`, and Oracle shipped JSON Relational Duality — relational
engines moving toward documents, not the reverse. That validates the *shape* of
the data; it does not by itself settle the operational trade-offs, which is why
the numbers above matter more than the narrative.

---

## References

* [PostgreSQL: TOAST](https://www.postgresql.org/docs/current/storage-toast.html)
* [PostgreSQL: Concurrency Control (MVCC)](https://www.postgresql.org/docs/current/mvcc-intro.html)
* [PostgreSQL: Routine Vacuuming](https://www.postgresql.org/docs/current/routine-vacuuming.html)
* [MongoDB: WiredTiger Storage Engine](https://www.mongodb.com/docs/manual/core/wiredtiger/)
* [MongoDB: Update Operators](https://www.mongodb.com/docs/manual/reference/operator/update/)
* [MongoDB: Queryable Encryption](https://www.mongodb.com/docs/manual/core/queryable-encryption/)
* [MongoDB Atlas Local (Docker image)](https://hub.docker.com/r/mongodb/mongodb-atlas-local)
* [MongoDB Server Side Public License (SSPL)](https://www.mongodb.com/legal/licensing/server-side-public-license)
* [Why MongoDB | The Shape of Memory — Oblivio Company](https://demos.oblivio-company.com/why-mongodb/)
