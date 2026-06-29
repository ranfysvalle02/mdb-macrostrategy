# Winning the Document-Database Race: An Honest Assessment

*An independent analysis of MongoDB's strategic position, disciplined by the small,
reproducible benchmark included in this repository. Every performance claim is backed
by measurable results; every strategic claim is labeled **fact** or **opinion** so it
can be argued with. The aim is analysis, not advocacy.*

---

## Executive summary

MongoDB is not dying, and it is not the inevitable future of databases. It is being
**normalized** — in the market's eyes — into what it actually is: a strong managed
document platform with an excellent developer experience and a mature
horizontal-scaling story.

The core strategic problem is not a single competitor. It is that **the document
model won**, and a model that has won is, by definition, no longer a moat. `jsonb`
in PostgreSQL, JSON in MySQL and SQLite, DocumentDB, Cosmos DB, Firestore — everyone
ships documents now. FerretDB even serves the MongoDB wire protocol on top of
Postgres.

So the race is no longer "is the document model good?" (that is settled). The race
is: **can MongoDB keep the easy path aligned with the efficient path, and can Atlas
stay enough better operationally to beat a managed Postgres for the workloads it
should own?** That is winnable — but it is an execution question, not a destiny. This
piece lays out the evidence, the threats, and a concrete plan.

---

## The uncomfortable evidence (standalone)

No other reading is required to follow this. The benchmark is deliberately narrow and
falsifiable: seed 500 documents of ~32 KB each, then apply 18,000 measured partial
updates (increment a counter, set a timestamp on two fields buried *inside* each
document) across 4 concurrent workers, on identical logical data. Mean of 5 trials,
local Docker, PostgreSQL 16.14 vs MongoDB 8.3.4 (Atlas Local).

| Metric | PostgreSQL (`jsonb`) | PostgreSQL (`normalized`) | MongoDB |
| --- | ---: | ---: | ---: |
| Throughput (ops/s) | 4,229 ± 265 | **10,417 ± 198** | 3,561 ± 128 |
| Latency p99 (ms) | 2.596 ± 0.913 | **0.702 ± 0.134** | 3.173 ± 0.767 |
| Storage growth (MB) | +335.96 ± 0.02 | **+0.02** | +9.51 ± 0.12 |

Three findings worth internalizing rather than spinning:

1. **The "MongoDB edits bytes in place" talking point is wrong** (fact). WiredTiger
   is also an MVCC / copy-on-write engine. What MongoDB actually avoids is Postgres's
   full-document-rewrite-plus-vacuum cycle on a large, out-of-line (`TOAST`ed)
   `jsonb` value. The accurate phrasing is "it avoids the rewrite-and-vacuum tax,"
   not "in place."
2. **On raw speed, naive `jsonb` edged MongoDB here** (fact): ~4,229 vs ~3,561 ops/s,
   with a lower p99. MongoDB's real, large, defensible win was **storage**: `jsonb`
   bloated the table ~336 MB; MongoDB grew ~9.5 MB for the same work.
3. **Idiomatic Postgres wins this micro-benchmark outright** (fact). Promote the hot
   field to a real column (`normalized`) and Postgres is fastest, tightest-variance,
   and barely grows. The catch: *you only get that if you already know to do it.*

That third point is the entire strategic tension in miniature. Postgres can match or
beat MongoDB on this workload — **but only with schema expertise MongoDB does not
require.** MongoDB's advantage is not speed. It is that **the natural modeling choice
is also the efficient one.** That is the asset to protect, and most of this analysis
is about protecting and widening it.

---

## Where MongoDB is genuinely winning

- **Ergonomic alignment** (opinion, supported by the benchmark). Embed the document,
  mutate fields with operators, and the engine does the right thing. No TOAST, no
  pre-emptive normalization, no GIN-index gymnastics. Developers move fast and don't
  step on a landmine they didn't know was there.
- **Atlas is the product, not `mongod`** (opinion). Managed multi-cloud operations,
  with Search, Vector Search, Triggers, Change Streams, Stream Processing, Charts,
  and Online Archive in one coherent surface. This is the real, sticky value.
- **Native horizontal scale-out** (fact). Sharding is first-class. Postgres's
  equivalent (Citus and friends) is an add-on with more operational surface area.
- **Consolidation reduces integration tax** (opinion). For teams who would otherwise
  run and sync several specialized stores, one engine that absorbs new paradigms is a
  genuine operational saving — *as long as the edges are not oversold.*
- **The model debate is over** (fact). Relational engines moved *toward* documents
  (`jsonb`, Oracle's JSON Relational Duality). The shape of modern data validated the
  original bet.

---

## The five threats to out-execute

1. **Commoditization of the model.** "Having documents" is now table stakes.
   Differentiation must move up-stack — platform, operations, scale, AI — or pricing
   power erodes.
2. **"Just use Postgres" as the default.** Neon, Supabase, Aurora, AlloyDB, Crunchy,
   plus `pgvector` / PostGIS / full-text, make Postgres a credible everything-store
   with an ACID core. This — not any one benchmark — is the real pressure on the
   addressable market. MongoDB needs a crisp, honest answer to "why not Postgres?"
   that is *not* about the model.
3. **The "throw it all in one document" failure mode.** This benchmark shows the
   document-everything reflex can bloat storage badly (unbounded arrays, hot fields
   in huge docs). MongoDB wins when the easy path is the right path; it loses
   mindshare every time a naive schema melts down in production.
4. **Over-claiming on secondary capabilities.** Vector and Search are "good, and
   already here" — but against a dedicated engine at extreme scale they lose, and the
   honest position says so. The aggregation framework is expressive but is **not** a
   moat versus SQL with CTEs and window functions. Leading with these invites an
   unflattering benchmark and erodes trust.
5. **Licensing and ecosystem perception (SSPL).** The license keeps the hyperscalers
   at arm's length and nudges some teams toward "truly open" defaults. It is a chosen
   cost; the managed-platform value has to keep clearly outweighing it.

---

## A concrete plan to win

Grouped by where the leverage is. Several of these *extend tools MongoDB already
ships* (Relational Migrator, Performance Advisor, Schema Suggestions, Atlas
Search/Vector), which is what makes them credible rather than aspirational.

### A. Make the efficient path the default path (protect the core asset)

- **Ship a "hot-path advisor."** Extend Atlas Performance Advisor and the existing
  Schema Anti-Patterns work to detect the specific failure this benchmark
  demonstrates: frequently-updated subfields inside large documents, unbounded
  arrays, and oversized documents. Surface it inline in Compass and the Atlas UI with
  a one-click suggested remodel (e.g., "split this hot counter into its own
  document/collection" or "bucket this array").
- **Teach `$inc`/`$set` discipline by default.** Lint and docs that nudge developers
  toward field-level operators over whole-document replacement (`replaceOne` /
  read-modify-write), with examples. The win is making "the fast way" the obvious way
  for newcomers.
- **Close the small-but-real speed gap.** This benchmark showed naive `jsonb` edging
  MongoDB on a tight update loop. Investing in the single-document update fast path
  and driver round-trip overhead would turn the ergonomic win into speed parity (or
  better) when someone runs a head-to-head.
- **Publish the anti-pattern playbook loudly.** A short, canonical "Modeling Hot
  Fields in Large Documents" guide, with measured before/after numbers. Owning the
  failure mode publicly builds more trust than pretending it doesn't exist.

### B. Win the platform, not the model (Atlas as the differentiator)

- **Answer "why not Postgres?" head-on, in writing.** A first-party, honest
  comparison that concedes where Postgres wins (single-node relational + `jsonb` for
  read-mostly, simple apps) and is specific about where Atlas wins (managed sharding
  at scale, unified search + vector + operational data, multi-cloud, change streams).
  Specificity reads as confidence; vagueness reads as fear.
- **Make multi-cloud and zero-downtime operations the headline.** These are genuinely
  hard for a self-managed Postgres and for most managed competitors. The operational
  story is the durable moat.
- **Unify the "search + vector + operational data in one query path" story.** For app
  developers this is the consolidation pitch that actually lands today: one database,
  one index, one query — no syncing embeddings to a separate vector store.

### C. Own the AI-native workload (the next "document model" bet)

- **Co-located vectors as the default for RAG and agents.** The strongest modern form
  of the consolidation argument: keep embeddings *in the same document* as the
  operational data they describe, so retrieval and updates share one consistency
  model. Make this the paved road in AI quickstarts and SDKs.
- **First-class agent/memory primitives.** Operational data + vector + change streams
  as the substrate for AI memory and event-driven agents — a category where
  "specialized store + sync pipeline" is genuinely painful, so the consolidation
  pitch is at its strongest.
- **Be honest about scale ceilings.** Where a billion-vector, sub-millisecond
  workload truly needs a dedicated engine, say so and provide a clean integration
  path. Credibility compounds.

### D. Lower the switching cost *toward* MongoDB

- **Make Relational Migrator a one-command, schema-aware experience.** Postgres →
  Atlas with automatic, sensible denormalization suggestions (and a dry-run that
  *shows the modeling decisions* rather than hiding them). The easier it is to leave
  Postgres, the more the DX advantage compounds.
- **Interop, not just migration.** Federation/connectors that let teams adopt Atlas
  for the document-shaped, AI, and search workloads *without* a big-bang migration.
  Meet the "just use Postgres" crowd where they are and earn the next workload.

### E. Narrative and trust (the cheapest, highest-leverage move)

- **Compete on transparency.** Publish reproducible benchmarks like the one in this
  repository — *including the cases MongoDB loses* — with the harness attached. A
  vendor that shows its `DROP TABLE` is more believable than one that ships a glossy
  bar chart.
- **Retire the wrong talking points.** Drop "edits bytes in place." Replace it with
  the accurate, still-compelling "it avoids the rewrite-and-vacuum tax and keeps the
  natural model efficient."
- **Position the aggregation framework honestly.** Sell it as "operational queries
  and analytics in one language over nested/array data," not as a SQL-killer. Honest
  framing ages well; overclaiming invites a teardown.

---

## The verdict

MongoDB's advantage is **real but narrowing**, and it is centered on *ergonomics and
the managed platform* — not raw performance, and not exclusive ownership of the
document model. The macrostrategy (one engine that absorbs new paradigms) is worth
real money to the right team, but it is **no longer unique**: Postgres is running the
same absorption play from a stronger relational base.

The bet to make, then, is not "the model is the future." Everyone has the model. The
bet is whether MongoDB keeps the **easy path and the efficient path aligned**, makes
**Atlas** unmistakably better operationally, and **owns the AI-native workload** while
it is still up for grabs. Win those three and the race is winnable. Coast on having
invented the model and the outcome is commoditization. The difference is entirely in
execution.

---

## Appendix

### A. Benchmark methodology (so it can be reproduced or attacked)

- **Workload:** 500 documents of ~32 KB; 18,000 measured partial updates across 4
  concurrent workers; increment a counter and set a timestamp on two fields buried
  inside each document. Warmup ops are excluded from measurement.
- **Why ~32 KB:** large enough to push the value out-of-line into Postgres `TOAST`,
  which is exactly the condition that makes a partial `jsonb` update expensive.
  Smaller values stay inline and the penalty quietly disappears.
- **Fairness control:** a `normalized` Postgres mode promotes the hot counters to
  real columns. It is included precisely so the benchmark can argue against itself —
  and it is the headline winner. A benchmark that can't lose is marketing.
- **Rigor:** each figure is the mean of 5 trials with the sample standard deviation
  reported; the seed is fixed across trials, so the spread is measurement noise, not
  different data. Both engines compress on disk (Postgres `TOAST` / WiredTiger
  snappy), so the storage comparison is apples-to-apples on compressed bytes.
- **Scope/limits:** one workload, one machine, via Docker. It says nothing about
  reads, analytics, joins, or production tuning. It answers one narrow question
  honestly. Tail latency (p99) was the noisiest metric; storage growth the most
  stable.

Full harness, CLI, and a FastAPI dashboard live in this repository — see
[README.md](README.md) and the narrative writeup in [blog.md](blog.md).

### B. Glossary

- **MVCC (Multi-Version Concurrency Control):** readers and writers don't block each
  other because updates create new *versions* of data rather than overwriting in
  place. Both Postgres and MongoDB/WiredTiger use it.
- **TOAST (The Oversized-Attribute Storage Technique):** how Postgres stores values
  too large for a row inline (past ~2 KB), out-of-line in a side table. TOAST chunks
  are immutable, so updating one key in a large `jsonb` rewrites the whole value.
- **WiredTiger:** MongoDB's default storage engine — also copy-on-write/MVCC, but it
  applies field-level updates without the full-document-rewrite-plus-vacuum cycle.
- **`jsonb`:** Postgres's binary JSON column type. Excellent for read-mostly nested
  data with GIN indexes; penalized on frequent partial updates of large values.
- **Sharding:** horizontal partitioning of data across nodes for scale-out. Native in
  MongoDB; an add-on (e.g., Citus) for Postgres.
- **SSPL (Server Side Public License):** MongoDB's source-available license, designed
  to require would-be cloud re-sellers to open-source their service stack; a reason
  the hyperscalers offer compatible-but-separate services.

### C. What this assessment is *not*

- Not a prediction with a date on it. Databases ossify into installed bases; outcomes
  play out over decades, not quarters.
- Not a general performance ranking. The benchmark measured one workload on one
  machine.
- Not financial guidance or a market forecast. For exact figures, consult primary
  sources, not this file.

### D. References

- Engine internals and the primary sources behind the claims above are listed in the
  repository's [README references section](README.md#references) (Postgres TOAST,
  MVCC, and vacuuming; MongoDB WiredTiger and update operators).
- Tools referenced in the plan that MongoDB already ships: Atlas Performance Advisor,
  Atlas Schema Suggestions / Anti-Patterns, Relational Migrator, Atlas Search, and
  Atlas Vector Search (see the MongoDB documentation for each).
