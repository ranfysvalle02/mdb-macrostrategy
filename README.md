# mdb-macrostrategy

[![CI](https://github.com/ranfysvalle02/mdb-macrostrategy/actions/workflows/ci.yml/badge.svg)](https://github.com/ranfysvalle02/mdb-macrostrategy/actions/workflows/ci.yml)

A short, deliberately honest essay about MongoDB's platform strategy, paired with
**two reproducible benchmarks** (each with its own FastAPI dashboard) that
*measure* the most-cited technical claims instead of asserting them.

The essay is an argument; treat it as opinion. The benchmark numbers are facts
you can regenerate on your own machine in a couple of minutes — including the
cases where MongoDB loses.

- **Full strategic assessment:** [TLDR.md](TLDR.md)
- **Narrative writeup:** [blog.md](blog.md)
- **Angle 1 — the update penalty:** [`basic/`](basic/README.md)
- **Angle 2 — search on the document:** [`search/`](search/README.md)

---

# The Consolidation Play: Why MongoDB's Real Value is its Macrostrategy

There is a running joke in backend engineering: whenever a new data paradigm
catches fire, wait six months, and MongoDB will absorb it.

If you view databases purely through the lens of micro-benchmarks, this looks like
a jack-of-all-trades, master-of-none strategy. Viewed through the lens of
*macrostrategy*, it reads differently. Choosing a database isn't only a technical
evaluation of what it can do today; it is a multi-year bet on a platform's ability
to evolve with you. That framing is a point of view, not a proven fact, and this
repo tries to be explicit about which is which.

## The Best-of-Breed Tax

For years, architectural wisdom dictated choosing "the right tool for the job."
Need time-series data? Spin up InfluxDB. Need a graph? Deploy Neo4j. Need vector
search for AI? Add Pinecone.

On paper this is pristine. In practice it introduces real operational cost:
multiple query languages, data-syncing pipelines (and the consistency bugs they
bring), and fragmented security, compliance, and billing. These costs are real,
but they are not free to avoid either. The honest claim is narrow: **as the number
of specialized stores grows, integration cost tends to grow faster than the
performance you bought by specializing.** For a small number of stores,
best-of-breed is often the right call.

## The Pattern of Absorption

Rather than forcing a migration when the industry shifts, MongoDB tends to extend
its engine to handle the new paradigm. These are real, shipped primitives:
Time-Series Collections; `$graphLookup` for recursive traversals; and **Atlas
Vector Search** (`$vectorSearch`), which keeps embeddings in the same document as
the operational data they describe. Whether these match a dedicated engine at the
extreme end is a separate question (see the caveat below). The point of the
pattern is consolidation, not winning every micro-benchmark.

**Queryable Encryption** is one unusual capability worth calling out factually: it
lets an application encrypt fields client-side, store them encrypted, and still
run a constrained set of server-side queries (equality and range) over the
ciphertext without the server decrypting it. It is genuinely uncommon among
general-purpose databases — and not magic: the supported query set is limited and
there are storage and performance costs.

## The Honest Caveat: Specialized Engines Still Win at the Edges

Purists will point out that a dedicated vector engine (Milvus, Pinecone) can beat
MongoDB's vector search at extreme scale and tail latency, and that a tuned
time-series database can out-compress Time-Series Collections. **They are right.**
At billion-scale indexes, sub-millisecond budgets, or specialized tuning,
purpose-built systems lead. The consolidation argument doesn't dispute that; it
argues that for many teams the integration and operational savings outweigh that
delta — and "many" is not a number anyone should invent. Whether *your* workload
sits at the edge where specialization pays is an empirical question about your
data, not a slogan. The two benchmarks below answer two such questions honestly.

---

## Two reproducible benchmarks

Each angle is **self-contained**: its own `docker-compose.yml` (on distinct,
non-standard ports so both can run at once), its own CLIs and dashboard, and its
own README. They share only a tiny engine-agnostic stats harness (`shared/`).

### Angle 1 — [`basic/`](basic/README.md): the JSONB update penalty

*Does burying a frequently-updated field inside a large document cost more on
PostgreSQL `jsonb` than on MongoDB?* Measured finding: the storage penalty is
real and large (~38x write amplification: +319 MB vs +9.5 MB over 18k updates),
but naive `jsonb` actually matched MongoDB on raw speed — and **idiomatic Postgres
(`normalized`) won outright**. The lesson is ergonomic, not triumphalist.

```bash
docker compose -f basic/docker-compose.yml up -d
uv run python -m basic.demo_psql --trials 5
uv run python -m basic.demo_mdb  --trials 5
uv run python -m basic.compare
uv run uvicorn basic.app:app --reload          # http://localhost:8000
```

### Angle 2 — [`search/`](search/README.md): full-text + vector on the same record

*Is doing full-text and vector search "on the document" a real MongoDB advantage
over PostgreSQL?* Measured finding: **both engines search the same record fine** —
Postgres via `tsvector` + `pgvector`, transactionally in-line; MongoDB via Atlas
Search + Vector Search on a separate, eventually-consistent `mongot` process. At
default knobs Postgres vector was faster but at lower recall (0.57) while MongoDB
returned ~0.99 recall; full-text was correct (precision 1.0) on both. The edge is
the **unified, managed, scale-out platform**, not an exclusive capability.

```bash
docker compose -f search/docker-compose.yml up -d
uv run python -m search.demo_psql_search --mode vector --trials 3
uv run python -m search.demo_mdb_search  --mode vector --trials 3
uv run python -m search.compare_search
uv run uvicorn search.app:app --reload --port 8001   # http://localhost:8001
```

> **Bound to their stacks on purpose.** Both benchmarks drop and recreate their
> table/collection on every run, so each only ever talks to its own bundled
> stack — no DSN/URI overrides. Host ports are non-standard *and* distinct
> (basic: `55432`/`57017`; search: `55433`/`57018`) so neither can grab a real
> database and both can run side by side. Endpoints are constants in each angle's
> `bench/common.py` and must match that angle's `docker-compose.yml`.

> **Editing a `docker-compose.yml` (e.g. changing ports)?** Use a full
> `docker compose -f <file> down && docker compose -f <file> up -d`. An in-place
> `up -d` recreate can leave the Atlas Local single-node replica set stuck in an
> `RSGhost` / `ReplicaSetNoPrimary` state; a clean down/up re-initializes it.

---

## Repository layout

```
shared/          tiny engine-agnostic harness (stats, percentiles, trial
                 aggregation, JSON persistence, table renderer, seeded text)
basic/           Angle 1: JSONB update penalty
  bench/         common.py (config/docs/aggregation) + pg.py / mongo.py cores
  demo_psql.py demo_mdb.py compare.py     CLIs (run via -m basic.<name>)
  app.py static/                          FastAPI dashboard
  docker-compose.yml                      postgres:16 + atlas-local (55432/57017)
  tests/                                  unit + integration
search/          Angle 2: full-text + vector search
  bench/         corpus.py (data + ground truth) + pg_search.py / mongo_search.py
  demo_psql_search.py demo_mdb_search.py compare_search.py   CLIs
  app.py static/                          FastAPI dashboard
  docker-compose.yml                      pgvector + atlas-local (55433/57018)
  tests/                                  unit + integration
TLDR.md blog.md                           strategy essay + narrative
.github/workflows/ci.yml                  lint+unit, plus basic & search integration jobs
```

## Develop and test

Requirements: Docker, [`uv`](https://docs.astral.sh/uv/). All commands run from
the repo root.

```bash
uv run pytest -m "not integration"           # fast, no databases (shared + basic + search)
uv run pytest basic/tests  -m integration     # needs the basic stack up
uv run pytest search/tests -m integration      # needs the search stack up
uv run ruff check
```

---

## Bet on the Engine, Not Just the Features

The honest, narrowed thesis: when you choose MongoDB you are betting that one
engine, evolving its query layer instead of asking you to bolt on another store,
will keep the ergonomic path and the efficient path aligned for document-shaped
data — and that the consolidation of operational, full-text, and vector workloads
onto one managed platform is worth more than the per-capability delta against a
specialist. Both benchmarks here support the *narrow* version of that claim and
puncture the inflated one: a well-modeled relational schema with `jsonb`,
`tsvector`, and `pgvector` is a formidable competitor, and "on the document" is
table stakes, not a moat.

Convergence is itself evidence the document model was a reasonable bet: PostgreSQL
added `jsonb`, and Oracle shipped JSON Relational Duality — relational engines
moving toward documents, not the reverse. That validates the *shape* of the data;
it does not settle the operational trade-offs, which is why the numbers matter
more than the narrative.

---

## References

- [PostgreSQL: TOAST](https://www.postgresql.org/docs/current/storage-toast.html)
- [PostgreSQL: Concurrency Control (MVCC)](https://www.postgresql.org/docs/current/mvcc-intro.html)
- [PostgreSQL: Full Text Search](https://www.postgresql.org/docs/current/textsearch.html)
- [pgvector](https://github.com/pgvector/pgvector)
- [MongoDB: WiredTiger Storage Engine](https://www.mongodb.com/docs/manual/core/wiredtiger/)
- [MongoDB: Atlas Search](https://www.mongodb.com/docs/atlas/atlas-search/) · [Atlas Vector Search](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/)
- [MongoDB: Queryable Encryption](https://www.mongodb.com/docs/manual/core/queryable-encryption/)
- [MongoDB Atlas Local (Docker image)](https://hub.docker.com/r/mongodb/mongodb-atlas-local)
- [Why MongoDB | The Shape of Memory — Oblivio Company](https://demos.oblivio-company.com/why-mongodb/)
