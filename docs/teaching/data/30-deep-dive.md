# Data — deep dive

Two real production bugs — one that lost every ledger row and disabled every budget cap,
one that crashed a sweeper on every cycle — plus the pattern they share.

---

## Story 1 — the usage ledger silently lost every row

This is the sharpest bug in the codebase, and it is the composition of three individually
reasonable decisions.

### The setup

Voice bills per audio-minute. Vision bills per image. Neither fits a token-counting ledger,
so two columns were added to the model
(`aegis/src/aegis/governance/models.py:190-193`):

```python
audio_seconds: Mapped[float] = mapped_column(default=0.0, server_default="0")
images: Mapped[int] = mapped_column(default=0, server_default="0")
```

Both are additive and defaulted, so existing rows, existing construction and the SQLite
test schema are unaffected (`:164-166`). The change is correct.

The `ALTER TABLE` needed on a live database was written **in a docstring**. That is the
whole defect.

### What happens next

From the audit-sweep commit (`7d3c436`):

> `audio_seconds`/`images` were added to the ORM with the ALTER TABLE written only in a
> docstring. There is no migration mechanism here and `create_all` never alters an
> existing table, so on any database created before that commit **every `record_usage`
> INSERT raised UndefinedColumn** — and `_record_usage` swallows exceptions by design.
> **Rows vanished, and the USD budget caps that read the ledger stopped binding.**

Trace it step by step:

1. **`create_all` sees `usage_ledger` exists.** It is `CREATE TABLE IF NOT EXISTS`. It
   does nothing — not "nothing much"; nothing at all.
2. **The live table still has the old columns.**
3. **Every `INSERT` naming `audio_seconds` raises `UndefinedColumn`.**
4. **The gateway swallows it**, because usage recording is best-effort by design — and
   that design is *correct*: a logging failure must not break a live customer request.
5. **The row is lost.** The call succeeded, the customer got their answer, and nothing
   recorded what it cost.
6. **The USD caps are computed by summing those rows.** No rows means no spend. No spend
   means the cap never binds.

### Why nothing anywhere noticed

This is what makes it worth telling.

- **No exception surfaces.** The swallow is deliberate.
- **No test fails.** The test database is created from scratch every run, so it has all
  the columns. The drift exists *only* on a long-lived database — which is, definitionally,
  only production.
- **No request fails.** Every API call succeeds normally.
- **The dashboard is green.** It shows $0.00 spend, which is a perfectly valid number and
  looks exactly like a quiet tenant.

The system had silently stopped attributing cost and stopped enforcing budgets, and the
**only** symptom was a number that stayed at zero.

### The composition

Take the three decisions apart:

| Decision | Defensible? |
|---|---|
| No migration framework, `create_all` at startup | Yes, for a project of this size |
| Usage recording is best-effort (swallowed) | Yes — logging must not break a request |
| Additive columns with server defaults | Yes — that is the safe way to add columns |

Every one is fine. **Together they produce an invisible, security-relevant failure.**

That is the pattern to be able to recognise in a design review: *a swallowed exception plus
a schema that silently drifts*. Neither alone is a bug. The combination removes both the
error and the symptom.

### The fix

`aegis/src/aegis/governance/schema.py` — `reconcile_additive_columns`, called from
`bootstrap()` immediately after `create_all`
(`backend/src/app/data/session.py:276-278`).

Four properties, each argued in the module docstring (`:18-36`):

**Additive only.** Adds columns the metadata has and the database lacks. *"It never drops,
renames, retypes or reorders anything, so it cannot destroy data and is safe to run on
every boot."*

**Idempotent.** The plan is computed from `information_schema`, so a second run finds
nothing. The DDL also carries `IF NOT EXISTS`, *"so two processes racing at startup cannot
collide."*

**Postgres-only.** SQLite returns immediately (`:196-197`) — the test suite recreates its
schema every run, so there is no drift to reconcile, and SQLite's `ALTER TABLE`
restrictions are deliberately not worked around.

**Loud.** Every added column logged at INFO. And a column that *cannot* be added safely
raises.

### The two subtle bits

**"Safely addable" has a precise definition** (`:57-70`): nullable, or carrying a
`server_default` the database can apply to every existing row. A `NOT NULL` column with no
server default has **no correct value** for the rows already there — only a human can
decide one. So it raises rather than being skipped.

**The DDL is rendered by SQLAlchemy's own compiler** (`:129-144`):

> Compiling from the declarative metadata (rather than hand-writing SQL) keeps the added
> column's type, nullability and server default **identical to what `create_all` would
> have produced on a fresh database.**

Without that, a reconciled database and a fresh one diverge — same column name, subtly
different type or default — and you have introduced a second schema that only exists in
production. Which is the original bug wearing a different hat.

### Why refusing to boot is right, and how that refusal is protected

Refusing to start on unreconcilable drift sounds extreme until you price the alternative:
running with a table whose writes are failing *right now*, which for the ledger means
uncapped, unattributed spend indefinitely.

But a refusal is only worth anything if it survives. Two defences:

**Logged at CRITICAL as well as raised** (`schema.py:212-217`):

> a host that wraps its bootstrap in a broad "the database is optional" handler would
> otherwise reduce this to a traceback nobody reads, and for the usage ledger the
> consequence of missing it is uncapped, unattributed spend.

**Re-raised ahead of the blanket startup handler**
(`backend/src/app/main.py:153-160`):

```python
except SchemaDriftError:
    logger.critical(
        "DB bootstrap FAILED: irreconcilable schema drift. Refusing to serve "
        "— the usage ledger may be unwritable, which disables every USD budget cap.",
        exc_info=True,
    )
    raise
```

The commit notes this explicitly — the fix *"also required re-raising ahead of `main.py`'s
blanket startup except."* A loud failure caught by a broad handler is a quiet failure
again.

**And it was verified against a live database**, per the commit: *"columns added, second
run a clean no-op, and a real `record_usage()` insert confirmed."* Three checks: it works,
it is idempotent, and the thing it was supposed to unblock is unblocked.

---

## Story 2 — the SLA sweeper that crashed every cycle

Same root cause, different symptom, and this one **is** loud — which makes for a useful
contrast.

From `aegis/src/aegis/data/base.py:75-78`:

> asyncpg refuses to encode an *aware* datetime for a naive column and raises
> `DataError: can't subtract offset-naive and offset-aware datetimes` — so every write
> **and** every `WHERE ts < :now` comparison from aware application code blows up at
> runtime (**this is what killed the SLA sweeper**).

A background sweeper wakes up, computes `datetime.now(UTC)`, and queries for rows older
than that. The bind fails. The sweep dies. It wakes again a minute later and dies again.

Loud, and therefore fixable. But the *second* failure mode in the same docstring is the
one to worry about (`:79-82`):

> `server_default=func.now()` on a naive column stores the *server's local wall clock*,
> not UTC. On a box with `TimeZone = Asia/Kolkata` every `created_at` is silently +05:30
> off, and the API then re-labels it `+00:00`.

Nothing errors. Every row looks plausible. You are publishing a wrong instant with a
confident timezone suffix, and the symptom surfaces months later as *"the audit log
timestamps do not line up with the access log."*

### The fix is a bug **class**, not a bug

`UtcDateTime` (`base.py:69-120`) does four things: `timestamptz` on Postgres, plain
`DateTime` elsewhere; normalise either input form on bind; strip the offset for SQLite;
always return aware UTC.

The SQLite line has its own reasoning (`:112-114`):

```python
# SQLite (and friends) store the naive UTC wall clock; the offset would
# otherwise be dropped silently and corrupt lexical ordering.
return value.replace(tzinfo=None)
```

SQLite compares ISO strings **lexically**, so `…T00:00:00+05:30` sorts after
`…T00:00:00+00:00` despite being an earlier instant.

But the move that matters is where it is applied (`base.py:136`):

```python
class AegisBase(DeclarativeBase):
    type_annotation_map = {datetime: UtcDateTime}
```

Not on each column. On the **base**. So every `Mapped[datetime]` on every table across
three modules gets it — including a column written next year by someone who has never
heard of this problem. The docstring says exactly that (`:133-135`): *"the single
enforcement point… stops the naive/aware mismatch class of bug from being reintroduced by
a future column."*

**Fixing each column is correct today and wrong on the next one someone adds. Fixing the
base is correct forever.**

### And the same `create_all` gap, again

A database bootstrapped *before* the fix still has naive columns, because — say it with
me — **`create_all` never alters an existing table**.

So `_align_timestamp_columns` (`backend/src/app/data/session.py:176-227`) is the second
reconciler: find every column the metadata declares as `UtcDateTime` that
`information_schema` still reports as `timestamp without time zone`, and convert it:

```sql
ALTER TABLE "t" ALTER COLUMN "c" TYPE timestamptz USING "c" AT TIME ZONE 'UTC'
```

`AT TIME ZONE 'UTC'` reinterprets the stored naive value as UTC — *"the meaning the
application already assigned to a stored naive timestamp everywhere it read one"*
(`:191-192`). It is idempotent by construction: it only touches columns still reported
naive.

**Two reconcilers, one root cause.** Columns that should exist and do not; a type that
should have changed and did not. Both are the same sentence: `create_all` only ever
creates.

---

## Story 3 — deleting a symbol as part of a migration

`aegis.data` used to expose `VectorType`, compiling to pgvector's `vector(dim)` on
Postgres. Vector search moved to Qdrant, so the SQL column stopped being an index and
became a source of record — and `pgvector` left the dependency list entirely
(`base.py:17-19`).

The interesting part is the test
(`aegis/tests/data/test_vector_column.py:23-26`):

```python
def test_vectortype_is_gone_and_vectorcolumn_exported() -> None:
    assert not hasattr(aegis.data, "VectorType"), "VectorType must be deleted"
    assert "VectorColumn" in aegis.data.__all__
    assert EMBED_DIM == 3072
```

It asserts the **absence** of the old name.

That is a deliberate and underused move. A migration that leaves the old symbol importable
"for compatibility" is a migration people keep using — and now you maintain two column
types, one of which needs an extension you removed. The test makes the old path impossible
rather than discouraged.

The module also states the honesty caveat about what the new column cannot do
(`base.py:50-52`):

> `dim` is retained for documentation/parity with the embedding dimensionality; **JSON
> storage does not enforce it** (the mirror skips off-dim rows at query time).

A JSON array will happily store 1536 floats in a column documented as 3072. So the
parameter is labelled documentation and the actual handling — skip off-dim rows at query
time — is named. Keeping a parameter you cannot enforce is fine; letting a reader assume
it is a constraint is not.

**One documentation drift worth knowing:** `docs/module/aegis-data.md` still describes
`VectorType` compiling to `vector(dim)` on Postgres. The code and the test say otherwise.
The reference doc is stale relative to the source.

---

## Story 4 — the FORCE that made a policy decorative

Adjacent to this module, and the same shape of bug: a control that was enabled and
enforced against nobody.

`aegis/src/aegis/governance/rls.py:112-119`:

> `FORCE ROW LEVEL SECURITY` — **the load-bearing statement**. Postgres exempts a table's
> *owner* from its own RLS policies unless FORCE is issued, and this application connects
> with the same role that ran `create_all`… Without FORCE the `tenant_isolation` policy
> was therefore **decorative — enabled, visible in `pg_policies`, and enforced against
> nobody.**

Note *how* you would have checked. Query `pg_policies` and the policy is there. Read the
migration and RLS is enabled. Read the code and the scope is bound per request. Every
inspection says "tenant isolation is on."

And every query returns every tenant's rows, because the application connects as the table
owner.

There is a second detail worth carrying (`:126-128`): the policy is created **without an
explicit `WITH CHECK`**, so Postgres reuses the `USING` predicate for writes — *"under a
bound tenant scope an INSERT/UPDATE that would stamp a different tenant is rejected by the
database, not merely hidden."* A read-only policy would let a cross-tenant write through.

---

## Concurrency, transactions and pooling

**Bootstrap runs in one transaction.** `bootstrap()` (`session.py:275-279`) wraps
`create_all`, both reconcilers in a single `engine.begin()`. DDL is transactional in
Postgres, so a failure rolls the whole thing back rather than leaving a half-migrated
schema.

**Two processes racing at startup.** In a multi-worker deployment every worker runs
`bootstrap()`. `create_all` is `IF NOT EXISTS`; the reconciler emits `ADD COLUMN IF NOT
EXISTS`; the timestamp alignment only touches columns still reported naive; RLS uses
`DROP POLICY IF EXISTS` then `CREATE POLICY`. Every step is idempotent by construction
(`schema.py:23-26`).

**Two metadata objects, created in order.** `Base.metadata` (the host's own tables) then
`AegisBase.metadata` (the modules'). And note `session.py:263-272`: the memory, governance
and ops model modules are imported **for their registration side effect** before
`create_all`. A mapped class in an unimported module is not in the metadata and its table
is simply never created — the decorator-registry trap in its ORM form.

**Tenant scope must be transaction-local.** With a connection pool, a session-scoped
variable survives into whatever request borrows that connection next — one tenant's scope
applied to another tenant's query. That is why the reader binds scope per session
(`backend/src/app/forecast/ledger.py:74`) alongside an explicit `WHERE tenant_id = …`
(`:76-77`), which is *"the same belt-and-suspenders isolation, reused rather than
reinvented"* (`ledger.py:16-19`).

**The column types are stateless.** `VectorColumn` and `UtcDateTime` hold only constructor
arguments and declare `cache_ok = True`, so SQLAlchemy may cache compiled statements using
them.

---

## Honest limits

**There is no migration framework.** The reconcilers cover exactly two drifts: a missing
additive column, and a naive timestamp that should be `timestamptz`. Everything else — a
renamed column, a retyped column, a dropped column, a new constraint, an index on a
pre-existing column, a data back-fill — needs a human, and there is no mechanism for one.
The docstring is explicit that those are out of scope (`schema.py:33-37`).

**A `NOT NULL` column with no server default halts the deployment.** That is the correct
behaviour and it is also a deployment hazard: adding such a column and shipping it means
the application refuses to boot until someone writes the migration by hand.

**JSON vector columns enforce no dimensionality.** Labelled, with the actual mitigation
named — but a 1536-wide row can be written and will simply be skipped later.

**The module reference doc is stale**, still describing the removed `VectorType`.

**One docstring inaccuracy worth knowing.** `backend/src/app/forecast/ledger.py:46-56`
says the ledger's `ts` is `TIMESTAMP WITHOUT TIME ZONE`. It is not: `UsageLedger.ts` is a
`Mapped[datetime]` on `AegisBase`, so it materialises as `UtcDateTime` → `timestamptz` on
Postgres. The *code* is fine — `UtcDateTime.process_bind_param` normalises a naive bind to
UTC, so passing naive bounds works correctly — but the comment describes a column type
that alignment has already converted.

---

## What you should now be able to tell as a story

- **The ledger that lost every row**: `create_all` never alters + a best-effort swallow +
  budget caps computed by summing those rows
- **Why nothing noticed** — no exception, no failing test, no failing request, and a $0.00
  that looks like a quiet tenant
- **The three defensible decisions** that compose into an invisible failure
- **The additive reconciler's four properties**, and why "safely addable" has a precise
  definition
- **Why the DDL is rendered by the ORM's compiler**, and the divergence it prevents
- **Why refusing to boot is right**, and the two things that stop the refusal being
  swallowed
- **The SLA sweeper crash**, and the *silent* server-clock sibling that is worse
- **Why `type_annotation_map` on the base closes a bug class**
- **Deleting the old symbol** as part of a migration, asserted by a test
- **`FORCE ROW LEVEL SECURITY`** — a policy that inspected as "on" and enforced against
  nobody

**Next:** [`40-diagrams.md`](40-diagrams.md).
