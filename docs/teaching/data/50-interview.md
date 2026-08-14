# Data — interview questions and answers

Claim, reason, concrete detail.

---

### "Why is there a separate data package? Why not put the ORM base in the core?"

Because the core is imported by **everything**, so every dependency it carries is a
dependency everything carries.

An ORM is heavy — one of the largest dependencies in a typical Python service, it pulls a
driver, and it has strong opinions about async. If the core carried SQLAlchemy, the
guardrails would carry an ORM, the forecaster would carry an ORM, and a version conflict
in SQLAlchemy would become a conflict for every module in the system.

So there is a second, **optional** foundation. `aegis.core` is pydantic and the standard
library; `aegis.data` depends on SQLAlchemy and is only depended on by the modules that
actually persist things — memory, governance, ops. Guardrails, vision, voice and forecast
never touch it, and their isolation tests ban `sqlalchemy` by name.

The core's dependency-free guard test names SQLAlchemy in its banned list explicitly, with
a comment saying it belongs in `aegis.data` instead.

---

### "What does that data package actually contain?"

One file, 139 lines, five exported names. It contributes **no engine, no session factory,
no migrations and no tables of its own** — only the shape vocabulary other modules build
their tables with. The host still owns the engine, the sessionmaker and the moment schema
creation happens.

The five: a declarative base, a JSON column type, a vector column type, a timestamp column
type, and one constant for the embedding width.

The reason it exists at all is narrow: two durable modules both need a declarative base,
and if either owned it, the other would have to import it. Hoisting it into a package both
already depend on is the same move as everywhere else in the module contract.

---

### "Why do you need custom column types?"

Because production runs Postgres and unit tests run on SQLite with no Docker, and **the
same schema definition has to materialise on both**. If your tests run on a different
schema from production, they are testing a different system.

Two mechanisms, for two different problems.

`with_variant` when you only need a different **type**: `JSON().with_variant(JSONB,
"postgresql")` gives native `jsonb` in production and portable `JSON` in tests, chosen by
SQLAlchemy's dialect detection.

A `TypeDecorator` when you also need to **transform values** — which is why timestamps
need one and JSON does not.

The property that matters either way: the dialect branch lives **at the type**, once,
rather than as `if postgres:` scattered through calling code. Scattered branches are where
behaviour actually diverges between environments.

---

### "Tell me about the timestamp type."

It exists because of two failure modes, and the second is the dangerous one.

**The loud one.** A plain `Mapped[datetime]` maps to `TIMESTAMP WITHOUT TIME ZONE`.
asyncpg refuses to encode an *aware* datetime for a naive column, so an application that
is uniformly `datetime.now(UTC)` — as any correct application is — fails on **every write**
and on every `WHERE ts < :now` comparison. That is what killed our SLA sweeper: it woke
up, computed now, and died, once a minute, forever.

**The silent one.** `server_default=func.now()` on a naive column stores the **database
server's local wall clock**, not UTC. On a box configured `TimeZone = Asia/Kolkata`, every
`created_at` is +05:30 off — and if the API then serialises those values with a `+00:00`
suffix, you are publishing a wrong instant with a confident timezone label. Nothing errors.
The rows look plausible. It surfaces months later as "the audit log does not line up with
the access log."

The type does four things: `timestamptz` on Postgres, plain `DateTime` elsewhere;
normalise either input form on bind; strip the offset for SQLite, because SQLite compares
ISO strings **lexically** and an offset suffix would sort wrongly; and always return aware
UTC, so `datetime.now(UTC) - row.created_at` can never raise.

**But the move I would actually highlight** is where it is applied. Not per column — on
the **base**, via `type_annotation_map = {datetime: UtcDateTime}`. So every
`Mapped[datetime]` on every table across three modules gets it, including one written next
year by someone who has never heard of this problem.

Fixing each column is correct today and wrong on the next one someone adds. Fixing the
base closes the bug **class**.

---

### "You store embeddings in the database. Why not pgvector?"

Because we asked what the column is **for**.

If it is a search index, you need pgvector — a `vector(n)` type, distance operators, an
ANN index. That is a Postgres extension, and it does not exist on SQLite at all, so the
test schema simply cannot be created.

But ANN search runs in Qdrant. So the SQL column is not an index; it is the **durable
source of record** that the Qdrant index mirrors. And a source of record is a list of
floats, which is portable JSON — `jsonb` on Postgres, `JSON` on SQLite, no extension, and
an identical schema in both places.

That reframing removed a whole extension from the deployment. Nothing about the storage
changed; what changed was what we understood the column to be *for*.

One honesty caveat I would volunteer: **JSON enforces no dimensionality**. A `vector(3072)`
column rejects a 1536-wide row; a JSON array does not. So the dimension parameter is
documented as documentation — not a constraint — and the mirror skips off-dim rows at
query time. Keeping a parameter you cannot enforce is fine; letting a reader assume it is
a constraint is not.

I'd also point at the test: it asserts the **absence** of the old `VectorType` symbol. A
migration that leaves the old name importable "for compatibility" is a migration people
keep using — and then you are maintaining two column types, one needing an extension you
removed.

---

### "How do you handle schema migrations?"

This is the best question you can ask me about this module, because the honest answer is
**there is no migration framework, and that gap caused a real production bug.**

`create_all` is `CREATE TABLE IF NOT EXISTS`. On an empty database it produces exactly the
right schema, which is why it feels sufficient. On a database that already has the table it
does **nothing** — not "nothing much", nothing at all.

We added two columns to the usage ledger — `audio_seconds` and `images`, because voice
bills per audio-minute and vision per image. Additive, defaulted, correct. The `ALTER
TABLE` needed on a live database was written **in a docstring**.

So on any pre-existing database: `create_all` did nothing, the live table kept its old
columns, and every ledger `INSERT` raised `UndefinedColumn`.

---

### "And what did that do?"

This is the part worth sitting with.

Usage recording is **best effort** — the write is wrapped in a broad handler so a logging
failure cannot break a live customer request. That is the right design in isolation.

So: the INSERT raised, the handler swallowed it, the request succeeded, and the row was
lost.

Now, what is the ledger *for*? It is the source of truth for per-tenant cost attribution —
and the **USD budget caps are computed by summing those rows**.

No rows means no spend. No spend means the cap never binds. The system had silently stopped
attributing cost and stopped enforcing budgets.

**And nothing anywhere noticed.** No exception surfaced — the swallow is deliberate. No
test failed — the test database is built from scratch every run, so it has all the columns;
the drift exists only on a long-lived database, which is definitionally only production. No
request failed. The dashboard showed $0.00, which is a perfectly valid number and looks
exactly like a quiet tenant.

**Three defensible decisions** — no migration framework, best-effort usage recording,
additive defaulted columns — **composed into an invisible, security-relevant failure.**
That is the pattern I would flag in a design review: a swallowed exception plus a schema
that silently drifts. Neither alone is a bug. Together they remove both the error and the
symptom.

---

### "How did you fix it?"

An **additive reconciler** that runs at bootstrap, right after `create_all`. Four
properties:

**Additive only** — it adds columns the models declare and the database lacks, and never
drops, renames, retypes or reorders. It cannot destroy data, so it is safe on every boot.

**Idempotent** — the plan is computed from `information_schema`, so a second run finds
nothing to do, and the DDL carries `IF NOT EXISTS` so two workers racing at startup cannot
collide.

**Postgres-only** — SQLite returns immediately, because the test suite rebuilds its schema
every run and has no drift to reconcile.

**Loud** — every added column logged at INFO, and a column that cannot be added safely
**raises**.

Two details I would offer.

**"Safely addable" has a precise definition**: nullable, or carrying a server default the
database can apply to every existing row. A `NOT NULL` column with no server default has no
correct value for the rows already there — only a human can decide one — so it raises
rather than being skipped.

**The DDL is rendered by SQLAlchemy's own compiler**, not hand-written. That keeps the
added column's type, nullability and default byte-identical to what `create_all` would have
produced on a fresh database. Otherwise a reconciled database and a fresh one diverge, and
you have a second schema that exists only in production — which is the original bug wearing
a different hat.

Proper answer for a long-lived database is Alembic. This is the honest minimum for a
project that deliberately does not have it.

---

### "Refusing to boot seems drastic."

It is the correct trade, and I would price both sides.

Booting means running with a table whose writes are failing *right now*. For the usage
ledger that means uncapped, unattributed spend — indefinitely, with every dashboard green.
A refusal is loud, immediate, and gets fixed in an hour.

But a refusal is only worth anything if it **survives**, and there are two defences.

It is logged at **CRITICAL as well as raised**, because a host that wraps its bootstrap in
a broad "the database is optional" handler would otherwise reduce it to a traceback nobody
reads.

And it is **re-raised ahead of the blanket startup handler** in `main.py`, with a message
naming the consequence: *"Refusing to serve — the usage ledger may be unwritable, which
disables every USD budget cap."*

A loud failure caught by a broad handler is a quiet failure again. That re-raise was part
of the fix, not an afterthought.

---

### "Was there a second reconciliation?"

Yes, and it has the same root cause.

A database bootstrapped before timestamps became the UTC type still carries naive columns,
because — again — `create_all` never alters an existing table. So every aware-UTC bind
keeps failing.

So bootstrap has a second step: find every column the metadata declares as the UTC type
that `information_schema` still reports as `timestamp without time zone`, and run
`ALTER COLUMN ... TYPE timestamptz USING c AT TIME ZONE 'UTC'`.

The `AT TIME ZONE 'UTC'` clause reinterprets the stored naive value as UTC — which is the
meaning the application already assigned to it everywhere it read one. And it is idempotent
by construction: it only touches columns still reported naive.

**Two reconcilers, one root cause.** Columns that should exist and do not; a type that
should have changed and did not. Both are the same sentence: `create_all` only ever
creates.

---

### "How is tenant isolation enforced at the database?"

Row-level security, and there is a bug in that area worth telling because it has the same
shape as the others.

A policy filters every query by a per-connection tenant variable, so even a query that
forgets its `WHERE` clause returns nothing.

**But Postgres exempts a table's owner from its own policies** unless you also issue
`ALTER TABLE ... FORCE ROW LEVEL SECURITY`. Our application connects with the same role
that ran `create_all` — the owner. So without FORCE the policy was **decorative: enabled,
visible in `pg_policies`, and enforced against nobody**.

Notice how you would have "verified" it. Query `pg_policies` — the policy is there. Read
the bootstrap — RLS is enabled. Read the request path — the scope is bound. Every
inspection says isolation is on. And every query returns every tenant's rows.

A second detail: the policy is created **without an explicit `WITH CHECK`**, so Postgres
reuses the `USING` predicate for writes. Under a bound scope, an INSERT that would stamp a
different tenant is **rejected by the database**, not merely hidden. A read-only policy
would let cross-tenant writes through.

And RLS is defence in depth, not a replacement: every query still filters `tenant_id`
explicitly in the application as well.

---

### "Any concurrency concerns?"

Three.

**Multi-worker startup.** Every worker runs bootstrap. `create_all` is `IF NOT EXISTS`,
the reconciler emits `ADD COLUMN IF NOT EXISTS`, the timestamp alignment only touches
columns still reported naive, and RLS uses `DROP POLICY IF EXISTS` then `CREATE POLICY`.
Every step is idempotent by construction, and the whole thing runs in one transaction — DDL
is transactional in Postgres, so a failure rolls back rather than leaving a half-migrated
schema.

**Pooled connections and tenant scope.** The tenant variable must be **transaction-local**,
not session-local. With a pool, a session-scoped variable survives into whatever request
borrows that connection next — one tenant's scope applied to another tenant's query. That
only manifests under concurrency, which is the worst way to find it.

**Registration side effects.** Bootstrap imports the memory, governance and ops model
modules purely for the side effect of registering their mapped classes. A class in an
unimported module is not in the metadata and its table is **simply never created** — no
error, no warning, and the first query against it fails much later somewhere else. It is
the decorator-registry trap in its ORM form.

---

### "What would you improve?"

**Adopt Alembic.** The reconcilers cover exactly two drifts — a missing additive column and
a naive timestamp. Everything else needs a human and there is no mechanism for one: a
rename, a retype, a drop, a new constraint, an index on a pre-existing column, a
back-fill.

**The `NOT NULL` hazard is a deployment trap.** Adding such a column and shipping it means
the application refuses to boot until someone hand-writes the migration. Correct behaviour,
and it should be caught in review rather than at deploy — a CI check that plans the
reconciliation against a snapshot of the production schema would do it.

**Alert on the best-effort swallow.** A usage write failing 100% of the time is an
incident. Counting and alerting on that exception rate would have caught the ledger bug in
minutes rather than at audit — and it generalises: distinguish a transient network failure
from a schema failure, because the second is permanent and will affect every subsequent
call.

**The reference doc is stale.** `docs/module/aegis-data.md` still describes the removed
`VectorType` compiling to `vector(dim)`. The code and the test say otherwise.

---

### "How would you test a data layer like this?"

Four things, and the first two are the ones people skip.

**Dialect compilation, both ways.** Assert `VectorColumn.load_dialect_impl(pg_dialect())`
is `JSONB` and **not** a pgvector type; assert the SQLite impl is JSON; assert a
round-trip of a float list on SQLite. That is exactly what the existing test does, and it
also asserts the **absence** of the removed symbol.

**Timestamp normalisation, in both directions and on both dialects.** Bind naive, bind
aware, assert what is stored; read back and assert it is always aware UTC. Then assert
that `datetime.now(UTC) - loaded` does not raise — the property that motivated the type.

**The reconciler's decision, without a database.** The planning function is pure and takes
the existing `(table, column)` set as an argument, so you can test the split into "addable"
and "needs a human" with no Postgres at all. Feed it a `NOT NULL` column with no server
default and assert it lands in the unsafe list.

**The drift end to end, against a real Postgres.** Create the table with the old shape,
run the reconciler, assert the column exists; run it again and assert nothing changed; then
**do the insert that was failing**. That last step is what makes it a fix rather than a
plausible one — the commit records exactly that verification: columns added, second run a
clean no-op, and a real `record_usage()` insert confirmed.
