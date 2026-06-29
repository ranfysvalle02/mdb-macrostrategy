# Don't Argue About MongoDB vs Postgres JSONB. Measure It.

*Or: how a confidently wrong AI answer turned into a reproducible benchmark, and
what the numbers actually said — including the parts I didn't want to hear.*

---

## Where this started

I was polishing an opinion piece arguing that MongoDB's real value isn't any single
feature — it's the *macrostrategy* of absorbing new data paradigms into one engine
instead of making you bolt on a new database every time the industry shifts. A fun
argument to make. Also, as written, an argument with no numbers in it.

Then I asked an AI to sanity-check the "PostgreSQL `jsonb` vs MongoDB" angle, and it
handed me a crisp, confident comparison. Two things in it set off alarms:

1. A claim that **"for 80–90% of applications PostgreSQL JSONB is the superior
   choice."** That 90% is invented. There's no study behind it. (Ironically, my own
   essay leaned on the *same* fabricated "90%" pointed the other way.)
2. A mechanism claim that **"MongoDB mutates only those specific bytes on disk"**
   while Postgres rewrites the whole row. The first half is just wrong.

Both halves of that are the kind of thing everyone repeats and nobody runs. So I
ran it.

---

## The claim worth testing

Strip away the slogans and there's a real, testable assertion underneath:

> Frequent partial updates to a field buried inside a large document are
> dramatically more expensive on PostgreSQL `jsonb` than on MongoDB.

That's falsifiable. But first, the mechanism — stated correctly, because the popular
version is misleading.

**PostgreSQL `jsonb` + MVCC + TOAST.** Any `jsonb` value past a couple of KB is
stored out-of-line in a TOAST table, and TOAST chunks are immutable. When you update
one key inside a 32 KB document, Postgres can't patch it in place. MVCC writes a new
row version *and a brand-new TOAST value*, orphaning the old one as dead data for
autovacuum to mop up later. Hammer that path and you get write amplification and
storage bloat.

**MongoDB field-level operators + WiredTiger.** `$inc` / `$set` send only the changed
fields, and WiredTiger applies the change through its own update structures. But —
and this is the part the "edits bytes in place" crowd gets wrong — **WiredTiger is
also an MVCC / copy-on-write engine. It does not surgically edit bytes on disk
either.** What it avoids is the full-document-rewrite-plus-vacuum cycle that a TOASTed
`jsonb` update triggers.

So the honest framing isn't "in-place vs rewrite." It's **write amplification on
large documents**. Which is a number you can measure.

---

## The benchmark (and the one design decision that matters)

The workload, identical on both engines:

- Seed 500 documents of ~32 KB each (big enough to force Postgres into TOAST).
- Apply 18,000 measured partial updates across 4 concurrent workers: increment a
  counter and set a timestamp on two fields *buried inside* each document.
- Record throughput, latency percentiles, and on-disk growth.

The Postgres update is the naive, document-store-style move:

```sql
UPDATE sessions
SET doc = jsonb_set(
            jsonb_set(doc, '{stats,score}',
                      to_jsonb(((doc #>> '{stats,score}')::bigint + 1))),
            '{stats,last_seen}', to_jsonb(:ts::bigint))
WHERE id = :id;
```

The MongoDB update is the natural one:

```js
db.sessions.updateOne(
  { _id: id },
  { $inc: { "stats.score": 1 }, $set: { "stats.last_seen": ts } }
);
```

Here's the decision that makes the benchmark trustworthy instead of a hit piece: I
gave Postgres a **second strategy that's designed to win.** In `normalized` mode the
hot counters are promoted to real columns and the big document stays in a `jsonb`
the workload never touches:

```sql
UPDATE sessions SET score = score + 1, last_seen = :ts WHERE id = :id;
```

If a benchmark can't argue against itself, it's marketing. This one can.

---

## The results

Mean of 5 trials (± sample standard deviation), Apple Silicon macOS via Docker,
PostgreSQL 16.14 and MongoDB 8.3.4 (Atlas Local). Your numbers will differ —
that's the point; the harness is in the repo so you can generate your own (the seed
is fixed across trials, so the spread is measurement noise, not different data).

| Metric | PostgreSQL (`jsonb`) | PostgreSQL (`normalized`) | MongoDB |
| --- | ---: | ---: | ---: |
| Throughput (ops/s) | 4,189 ± 180 | **10,264 ± 403** | 3,005 ± 581 |
| Latency p50 (ms) | 0.834 | **0.356** | 0.990 |
| Latency p99 (ms) | 2.813 ± 0.666 | **0.781 ± 0.151** | 4.642 ± 1.106 |
| Storage before (MB) | 8.55 | 8.55 | 9.13 |
| Storage after (MB) | 328.00 | 8.57 | 18.67 |
| **Storage growth (MB)** | **+319.45 ± 36.93** | **+0.01** | **+9.53 ± 0.10** |

Three findings — and I'll lead with the one that's least flattering to where I
started.

### 1. Idiomatic Postgres wins this micro-benchmark outright

Promote the hot field to a column and the penalty doesn't shrink — it *vanishes*
(+0.01 MB), and Postgres becomes the **fastest** option at ~10,300 ops/s with the
lowest p99 *and* the tightest run-to-run variance. The lesson is emphatically not
"MongoDB beats Postgres." It's: don't bury a hot field inside a large TOASTed
`jsonb`.

### 2. The JSONB penalty is real, and it's about storage, not speed

Naive `jsonb` ballooned the TOAST table from ~8.5 MB to ~328 MB over 18k updates —
roughly **38x write amplification**. The absolute figure wobbles run to run as
autovacuum reclaims on its own schedule (±37 MB here), but MongoDB grew only ~9.5 MB
doing the same work. That is a real, large, defensible gap that never gets close.

### 3. On raw speed, naive `jsonb` actually edged MongoDB

Postgres `jsonb` posted *higher* throughput (4,189 vs 3,005 ops/s) and a lower p99
than MongoDB here — though the tail latency is noisy, so don't over-read it.
MongoDB's advantage on this workload is overwhelmingly about avoiding bloat, not raw
latency. Anyone selling you "MongoDB is faster" on this kind of workload is, at best,
rounding.

---

## So what *is* the honest takeaway?

Not "use MongoDB." Not "use Postgres." Something more useful:

**With MongoDB, the natural modeling choice and the efficient one are the same
choice.** You embed the document, you update fields with operators, and the storage
engine does the right thing. You didn't have to know about TOAST.

**With Postgres `jsonb`, you can match or beat MongoDB — but only if you know to
normalize the hot path.** The ergonomic move (shove everything in a `jsonb` and
`jsonb_set` it) is the slow, bloaty one. The fast move requires schema knowledge.

That's the defensible core of the "macrostrategy" argument, minus the triumphalism:
MongoDB's value here is *ergonomic alignment*, not a benchmark trophy. And a
well-modeled relational schema remains a formidable competitor — often the winner.

It's also worth noting which way the industry is drifting: Postgres added `jsonb`,
Oracle shipped JSON Relational Duality. Relational engines are moving *toward*
documents. That validates the shape of the data — it doesn't settle the operational
trade-offs, which is exactly why the numbers matter more than the narrative.

---

## Then I did it again, for search

The other slogan I kept hearing was: *doing full-text and vector search "on the
document" is a huge MongoDB advantage.* So I built a second angle (`search/`) and
pointed the same discipline at it: a deterministic corpus of 5,000 documents, each
carrying text **and** an embedding on the same record, indexed both ways on both
engines — Postgres with `tsvector` + GIN and the `pgvector` HNSW extension, MongoDB
with Atlas Search and Atlas Vector Search. Recall and precision scored against a
ground truth computed in NumPy so neither database grades its own homework.

The first finding deflates the slogan: **Postgres does this on the same row too.**
`tsvector` and `pgvector` are columns next to your data, queried in one `SELECT`,
inside one transaction. "On the document" is table stakes, not a moat.

The vector numbers were the interesting part, and they're a trap if you read only
one column. At default knobs Postgres did ~1,170 queries/sec to MongoDB's ~355 — but
Postgres was only returning **57% of the true nearest neighbors** while Atlas
returned **99%**. Approximate nearest neighbor is *approximate*; a fast config is
usually just a low-recall config. Quote the latency without the recall and you've
proved nothing. (Turn the Postgres `ef_search` knob up to match recall and its
latency climbs toward Atlas's — that's the honest comparison, and it's a knob in the
CLI.) Worth saying plainly: Atlas landing at 99% recall *by default* is a real
ergonomic point in MongoDB's favor — the same "the default is the right path" theme
as the update probe.

Full-text? Both engines returned only genuinely-matching documents (precision 1.0);
they just rank differently (BM25 vs `ts_rank`), which isn't something a benchmark
without labeled relevance judgments gets to crown. The differences were latency and
build time, in both directions.

And one fact that reframes the whole "on the document" pitch: MongoDB's search isn't
literally in the storage engine. Atlas Search and Vector Search run on a separate
Lucene process (`mongot`) fed by change streams, so they're **eventually consistent**
with your writes — while Postgres's `tsvector`/`pgvector` indexes are transactionally
in-line. So the honest MongoDB edge for search is the *unified, managed, scale-out
platform* (one query language over operational + text + vector data, embeddings
co-located, multi-cloud), not an exclusive capability and not stronger consistency.
Same shape of conclusion as the update probe, one layer up.

---

## Things I had to get right to not fool myself

- **A 32 KB document, on purpose.** Smaller values stay inline and never hit TOAST;
  the penalty would quietly disappear and I'd "prove" the wrong thing.
- **Five trials, with the stdev reported.** A single run hides run-to-run noise. Tail
  latency (p99) turned out to be the noisiest metric; the `normalized` and MongoDB
  storage numbers were rock-steady, while the `jsonb` bloat itself wobbled with
  autovacuum timing — but never close to the others. Exactly the kind of thing a
  one-shot number would have let me overclaim.
- **A fairness control (`normalized`).** Without it, this is propaganda.
- **autovacuum left on.** The realistic default. It reclaims dead tuples lazily, but
  the TOAST table had already grown — only `VACUUM FULL` returns space to the OS.
- **Compression on both sides.** Postgres TOAST and WiredTiger snappy both compress,
  so the size comparison is apples-to-apples on compressed bytes.
- **One workload, one machine.** This says nothing about reads, analytics, joins, or
  production tuning. It answers one narrow question honestly.

---

## Run it yourself

The repo is split into two self-contained angles — `basic/` (this update probe) and
`search/` (the search probe) — each with its own docker-compose stack, CLIs, and a
FastAPI dashboard that runs both engines with a live progress stream.

```bash
# Angle 1: the update penalty
docker compose -f basic/docker-compose.yml up -d            # Postgres 16 + Atlas Local
uv run python -m basic.demo_psql --trials 5                 # jsonb
uv run python -m basic.demo_psql --strategy normalized --trials 5  # the fair fight
uv run python -m basic.demo_mdb --trials 5
uv run python -m basic.compare
uv run uvicorn basic.app:app --reload                       # http://localhost:8000

# Angle 2: search on the document
docker compose -f search/docker-compose.yml up -d           # pgvector + Atlas Local
uv run python -m search.demo_psql_search --mode vector --trials 3
uv run python -m search.demo_mdb_search  --mode vector --trials 3
uv run python -m search.compare_search
uv run uvicorn search.app:app --reload --port 8001          # http://localhost:8001
```

Each angle is deliberately bound to its own docker-compose stack on non-standard,
*distinct* ports (basic `55432`/`57017`, search `55433`/`57018`) because both drop
and recreate their table and collection on every run — a benchmark with `DROP` in it
has no business connecting to whatever database happens to be on `5432`. The distinct
ports also mean you can run both angles at once.

---

## The actual moral

The point was never to crown a database. It was to replace two repeated slogans — an
invented "90%" and a wrong "edits bytes in place" — with a number anyone can
regenerate. The benchmark even talked me out of my own opening argument's swagger,
which is exactly what a good benchmark is supposed to do.

If you take one thing: when someone (human or model) hands you a confident database
comparison, ask where the `DROP TABLE` is. If there isn't one, it's an opinion
wearing a lab coat.
