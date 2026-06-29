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
| Throughput (ops/s) | 4,229 ± 265 | **10,417 ± 198** | 3,561 ± 128 |
| Latency p50 (ms) | 0.832 | **0.360** | 0.872 |
| Latency p99 (ms) | 2.596 ± 0.913 | **0.702 ± 0.134** | 3.173 ± 0.767 |
| Storage before (MB) | 8.55 | 8.55 | 9.13 |
| Storage after (MB) | 344.51 | 8.57 | 18.65 |
| **Storage growth (MB)** | **+335.96 ± 0.02** | **+0.02** | **+9.51 ± 0.12** |

Three findings — and I'll lead with the one that's least flattering to where I
started.

### 1. Idiomatic Postgres wins this micro-benchmark outright

Promote the hot field to a column and the penalty doesn't shrink — it *vanishes*
(+0.02 MB), and Postgres becomes the **fastest** option at ~10,400 ops/s with the
lowest p99 *and* the tightest run-to-run variance. The lesson is emphatically not
"MongoDB beats Postgres." It's: don't bury a hot field inside a large TOASTed
`jsonb`.

### 2. The JSONB penalty is real, and it's about storage, not speed

Naive `jsonb` ballooned the TOAST table from ~8 MB to ~344 MB over 18k updates —
roughly **40x write amplification**, and it was the single most reproducible number
in the whole run (±0.02 MB across five trials). MongoDB grew ~9.5 MB doing the same
work. That is a real, large, defensible gap.

### 3. On raw speed, naive `jsonb` actually edged MongoDB

Postgres `jsonb` posted *higher* throughput (4,229 vs 3,561 ops/s) and a lower p99
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

## Things I had to get right to not fool myself

- **A 32 KB document, on purpose.** Smaller values stay inline and never hit TOAST;
  the penalty would quietly disappear and I'd "prove" the wrong thing.
- **Five trials, with the stdev reported.** A single run hides run-to-run noise. The
  tail latency (p99) turned out to be the noisiest metric and storage growth the most
  stable — exactly the kind of thing a one-shot number would have let me overclaim.
- **A fairness control (`normalized`).** Without it, this is propaganda.
- **autovacuum left on.** The realistic default. It reclaims dead tuples lazily, but
  the TOAST table had already grown — only `VACUUM FULL` returns space to the OS.
- **Compression on both sides.** Postgres TOAST and WiredTiger snappy both compress,
  so the size comparison is apples-to-apples on compressed bytes.
- **One workload, one machine.** This says nothing about reads, analytics, joins, or
  production tuning. It answers one narrow question honestly.

---

## Run it yourself

The whole thing is a small repo: two importable benchmark cores, thin CLIs, and a
FastAPI dashboard that runs both engines with a live progress stream and charts the
throughput / p99 / storage-growth comparison.

```bash
docker compose up -d          # Postgres 16 + MongoDB 8.3 (Atlas Local)

# CLI (--trials N reports mean +/- stdev)
uv run python demo-psql.py --trials 5                       # jsonb
uv run python demo-psql.py --strategy normalized --trials 5 # the fair fight
uv run python demo-mdb.py --trials 5
uv run python compare.py

# ...or the dashboard
uv run uvicorn app:app --reload                            # http://localhost:8000
```

It's deliberately bound to its own docker-compose stack on non-standard ports
(`55432` / `57017`) because it drops and recreates its table and collection on every
run — a benchmark with `DROP` in it has no business connecting to whatever database
happens to be on `5432`.

---

## The actual moral

The point was never to crown a database. It was to replace two repeated slogans — an
invented "90%" and a wrong "edits bytes in place" — with a number anyone can
regenerate. The benchmark even talked me out of my own opening argument's swagger,
which is exactly what a good benchmark is supposed to do.

If you take one thing: when someone (human or model) hands you a confident database
comparison, ask where the `DROP TABLE` is. If there isn't one, it's an opinion
wearing a lab coat.
