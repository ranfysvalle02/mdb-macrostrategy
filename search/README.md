# Search on the Document: full-text + vector, PostgreSQL vs MongoDB

*The second angle. The [basic angle](../basic/README.md) asks whether the natural
document-modeling choice is also the efficient one for update-heavy workloads.
This angle asks a question that gets oversold a lot: **is doing full-text and
vector search "on the document" a real MongoDB advantage over PostgreSQL?**
The honest answer has a strong version and a weak version, and the benchmark in
this folder is built to tell them apart.*

---

## The claim, stated fairly

The strong, defensible version (opinion, but a good one): MongoDB lets you keep
**operational data, full-text indexes, and vector embeddings in one managed
platform, addressed by one query language**, with the embedding living on the
*same document* as the data it describes. For teams who would otherwise run
Postgres **plus** a separate vector store and sync between them, that
consolidation is a genuine operational saving, and it scales out.

The weak version — the one worth retiring — is "**Postgres can't search the same
record**." It can. `tsvector` gives Postgres mature full-text search, and the
`pgvector` extension gives it ANN vector search, both as columns on the same row,
queried in the same `SELECT`, inside the same transaction. So "search on the same
record" is **table stakes**, not a moat. This is exactly the position the
[strategic writeup](../TLDR.md) takes (threat #4: don't over-claim on secondary
capabilities).

Two technical facts sharpen the comparison and both are worth saying out loud:

- **MongoDB's search is not literally "in" the storage engine.** Atlas Search and
  Vector Search run in a separate Lucene-based process (`mongot`) fed by change
  streams. The developer experience is unified (one query language, `$search` /
  `$vectorSearch` in the aggregation pipeline), but there is an internal
  replication hop and the index is **eventually consistent** with your writes.
- **Postgres's indexes are transactionally in-line.** `tsvector`/`pgvector`
  indexes update inside the same MVCC transaction, so Postgres actually has
  *stronger* read-your-write consistency for search than Atlas Search does.

So the real contest is platform, ergonomics, and scale — not "who can search a
record." This benchmark measures the parts a single laptop *can* measure
honestly, and is explicit about the parts it can't.

---

## What this measures

A deterministic corpus of documents, each carrying text (`title`, `body`,
`tags`, `category`) **and** an embedding **on the same record** — a Postgres row,
a MongoDB document. Identical, seed-reproducible data on both engines. Then, per
mode:

- **Vector** — approximate nearest neighbor over the embeddings.
  Postgres: `pgvector` `vector` column + HNSW index (`vector_cosine_ops`).
  MongoDB: Atlas `$vectorSearch` (HNSW).
- **Full-text** — lexical retrieval over the text.
  Postgres: a `STORED` generated `tsvector` column + GIN, `to_tsquery` +
  `ts_rank_cd`. MongoDB: an Atlas Search index queried with `$search`.

Recorded for each: **query latency** percentiles, **throughput** (qps),
**index build time**, **index size**, and a **quality@k** score scored against a
ground truth computed in pure Python/NumPy so neither database grades its own
homework:

- **Vector quality = recall@k** against the *exact* brute-force cosine top-k.
- **Full-text quality = precision@k**: the fraction of returned documents that
  genuinely contain a query term. (See "Why precision, not recall, for
  full-text" below.)

The fairness control — the search analog of the basic angle's `normalized`
Postgres mode — is that **recall is always reported next to latency**, plus the
`ef_search` (Postgres) and `numCandidates` (MongoDB) knobs are exposed. ANN is
*approximate*: a fast configuration is usually just a low-recall configuration.
A latency number without a recall number is marketing.

---

## Evidence (a local run)

**Mean of 3 trials (± sample standard deviation)** from a local run via Docker on
macOS (PostgreSQL 16.14 + pgvector 0.8.3, MongoDB 8.3.4 Atlas Local). Corpus =
5,000 documents, `dim` = 256, `k` = 10, 200 measured queries.
The seed is fixed across trials, so the spread is measurement noise. **Your
numbers will differ — that is the point; regenerate them yourself.**

### Vector search

Knobs: Postgres HNSW `m=16`, `ef_construction=64`, `ef_search=40`; MongoDB
`numCandidates=100`.

| Metric | PostgreSQL (`pgvector` HNSW) | MongoDB (Atlas Vector Search) |
| --- | ---: | ---: |
| Recall@10 | 0.5730 ± 0.0035 | **0.9878 ± 0.0028** |
| Latency p50 (ms) | **0.585** | 2.309 |
| Latency p99 (ms) | **2.420 ± 0.398** | 4.577 ± 0.759 |
| Throughput (qps) | **1,173 ± 60** | 354 ± 50 |
| Index build (s) | **0.406 ± 0.006** | 2.015 ± 0.008 |
| Index size (MB) | 6.52 | n/a* |

### Full-text search

| Metric | PostgreSQL (`tsvector` + GIN) | MongoDB (Atlas Search, BM25) |
| --- | ---: | ---: |
| Precision@10 | 1.0000 | 1.0000 |
| Latency p50 (ms) | 2.174 | **1.424** |
| Latency p99 (ms) | 4.253 ± 0.682 | **3.436 ± 0.194** |
| Throughput (qps) | 402 ± 7 | **540 ± 91** |
| Index build (s) | **0.009 ± 0.000** | 2.013 ± 0.002 |
| Index size (MB) | 0.25 | n/a* |

\* Atlas Local does not expose the `mongot` Lucene index byte-size through a
stable API, so it is reported as unavailable rather than guessed. The index-size
comparison is therefore one-sided; only the Postgres number is real.

---

## What the data actually says

**Vector — the headline is the recall/latency trade, not a winner.**

1. **At default knobs the two engines sit at different operating points** (fact).
   Postgres at `ef_search=40` is ~3x the throughput and about half the
   p99 — *but it returns only ~57% of the true neighbors*. MongoDB at
   `numCandidates=100` returns ~99%. These are not comparable until you put them
   on the same recall. Raise Postgres `ef_search` until recall ≈ 0.98 (try
   `--ef-search 200`) and its latency rises toward MongoDB's; that is the honest
   comparison, and the knob is there so you can run it.
2. **The "it just works" point goes to MongoDB** (opinion). Atlas's default
   landed at ~0.99 recall without tuning; Postgres's default needed knowledge of
   `ef_search` to avoid silently shipping a 57%-recall result. That is the same
   ergonomic theme as the basic angle: the efficient/accurate path being the
   default path has real value.
3. **Build + footprint go to Postgres here** (fact). The HNSW index built in
   ~0.4s; the Atlas index "build" (~2s) is the asynchronous `mongot` sync and is
   not directly comparable to an in-process `CREATE INDEX`.

**Full-text — both are correct; the differences are operational.**

1. **Both return only matching documents** (fact): precision@10 = 1.000 on both.
   Full-text *correctness* is not where these engines differ.
2. **The trade-offs run both directions** (fact). Postgres builds the GIN index
   almost instantly (~9ms) and tiny (0.25 MB); Atlas Search served queries faster
   here (540 vs 402 qps) but its index builds asynchronously and its size is not
   exposed locally.
3. **We deliberately do not crown a ranker** (fact). Postgres `ts_rank_cd` and
   Atlas Search BM25 order matches differently; declaring one "more relevant"
   requires labeled relevance judgments this benchmark does not have. Measuring
   recall against a third ranker (e.g. TF-IDF) would measure *ranker similarity*,
   not quality — so we don't.

**The strategic read** (opinion, consistent with [TLDR.md](../TLDR.md)): on a
single node, both engines do full-text and vector search on the same record
perfectly well. That *undercuts* "search on the document is a unique MongoDB
advantage" and *supports* the narrower, truer claim — MongoDB's edge is the
**unified, managed, scale-out platform** (one query language over operational +
text + vector data, embeddings co-located, multi-cloud, no second store to sync),
not an exclusive capability. The place that edge actually compounds —
horizontally-scaled, managed, multi-cloud search — is precisely what a laptop
benchmark cannot show, and saying so is more credible than a green bar chart.

---

## Why precision, not recall, for full-text

An earlier version scored full-text recall against a TF-IDF reference ranking.
That produced a misleadingly low MongoDB number — not because Atlas Search was
wrong, but because Lucene BM25 (length-normalized, tf-saturated) legitimately
ranks differently from raw TF-IDF, so their top-10 sets barely overlap when
hundreds of documents match. That metric measured *agreement with TF-IDF*, not
quality. Precision@k — "did the engine return documents that actually match?" —
is ranker-neutral and fair, and both engines score ~1.0. The honest full-text
comparison is therefore latency, throughput, and index build, not a recall race.

---

## Run it yourself

Requirements: Docker, [`uv`](https://docs.astral.sh/uv/). Commands run from the
repo root.

```bash
# 1. Start the search stack: pgvector/pgvector:pg16 + MongoDB Atlas Local 8.3.
docker compose -f search/docker-compose.yml up -d

# 2. Wait until both are healthy.
docker compose -f search/docker-compose.yml ps

# 3a. CLI (per engine, per mode; --trials N reports mean +/- stdev).
uv run python -m search.demo_psql_search --mode vector --trials 3
uv run python -m search.demo_psql_search --mode fts    --trials 3
uv run python -m search.demo_mdb_search  --mode vector --trials 3
uv run python -m search.demo_mdb_search  --mode fts    --trials 3
uv run python -m search.compare_search                 # side-by-side tables

# Raise recall on the Postgres side and watch latency rise (the fair fight):
uv run python -m search.demo_psql_search --mode vector --ef-search 200

# 3b. ...or the dashboard (note the port: the basic dashboard uses 8000).
uv run uvicorn search.app:app --reload --port 8001     # open http://localhost:8001
```

Tear down with `docker compose -f search/docker-compose.yml down`.

> **Bound to this stack on purpose.** Like the basic angle, the search benchmark
> drops and recreates its table and collection on every run, so it only ever
> talks to the bundled search stack. Its host ports (Postgres `55433`, MongoDB
> `57018`) are deliberately non-standard *and* differ from the basic stack
> (`55432` / `57017`) so the two angles can run side by side and neither can grab
> a real database. Endpoints live as constants in `search/bench/common.py` and
> must match `search/docker-compose.yml`.

Develop and test:

```bash
uv run pytest -m "not integration"          # fast, no databases
uv run pytest search/tests -m integration   # end-to-end (needs the search stack)
uv run ruff check
```

---

## Methodology and caveats

- **Deterministic, model-free corpus.** Text is sampled from fixed per-category
  vocabularies; embeddings are synthetic, cluster-structured vectors seeded from
  one integer. No embedding model, no download, fully reproducible. Real-world
  recall depends on your embedding distribution; this isolates the index
  behavior, not a model's quality.
- **Ground truth is computed independently.** Exact brute-force cosine top-k for
  vectors; a token-containment check for full-text precision. Neither database
  scores itself.
- **ANN is approximate; recall is mandatory.** Compare latency only after putting
  both engines on a comparable recall via `ef_search` / `numCandidates`.
- **Consistency models differ.** Postgres `tsvector`/`pgvector` are
  transactionally consistent; Atlas Search/Vector are eventually consistent with
  `mongod` via `mongot`. The benchmark warms up and measures steady-state, so it
  does not capture indexing lag — a real operational difference in MongoDB's
  favor for Postgres on read-your-writes.
- **Index size is one-sided.** Atlas Local does not expose the `mongot` index
  byte-size; only Postgres's index size is real.
- **Scope.** One workload, one machine, via Docker. It says nothing about
  billion-vector indexes, sharded/multi-cloud deployments, or relevance quality
  on labeled data — the regimes where the platform story (or a dedicated engine)
  actually decides things. Numbers come from real runs and are never hand-edited.

---

## References

- [PostgreSQL: Full Text Search (`tsvector`/`tsquery`)](https://www.postgresql.org/docs/current/textsearch.html)
- [pgvector](https://github.com/pgvector/pgvector) — HNSW / IVFFlat vector indexes for Postgres
- [MongoDB: Atlas Search (`$search`)](https://www.mongodb.com/docs/atlas/atlas-search/)
- [MongoDB: Atlas Vector Search (`$vectorSearch`)](https://www.mongodb.com/docs/atlas/atlas-vector-search/vector-search-overview/)
- [MongoDB: `mongot` and the Atlas Search architecture](https://www.mongodb.com/docs/atlas/atlas-search/manage-indexes/)
- [MongoDB Atlas Local (Docker image)](https://hub.docker.com/r/mongodb/mongodb-atlas-local)
