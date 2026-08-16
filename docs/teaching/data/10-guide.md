# The data layer

The small package that says what an Aegis table looks like.

---

## 1. What it is

Aegis has a memory module. It stores conversation turns and facts about a user. Aegis also
has a governance module. It stores tenants, users, budgets, and a row for every model call.
Two different modules, both needing tables in the same database.

An ORM — the library that maps Python classes to database tables — needs one base class that
every table class inherits from. Inheriting from it is what registers the table. Only tables
registered on the same base get created together.

So whose base is it? If memory owns it, governance has to import memory to get it — two
modules that have nothing to say to each other now depend on one another. If each owns its
own, the application has two separate registries to create, and a link between them is
awkward.

Neither. Put the base in a tiny package both sides already depend on. That package is
`aegis.data`:

```python
class MemoryFact(AegisBase):        # in aegis.memory
    __tablename__ = "memory_facts"

class UsageLedger(AegisBase):       # in aegis.governance
    __tablename__ = "usage_ledger"
```

`aegis.data` is about 140 lines. It is defined as much by what it refuses to hold: no
database engine, no sessions, no migrations, no tables of its own. It owns the **shape** of
the store. The application that uses Aegis owns the engine, the connections, and the moment
the tables get created.

---

## 2. How it works in Aegis

The whole public surface is five names, all from `aegis/src/aegis/data/base.py`.

| Name | What it is |
|---|---|
| `AegisBase` | The base class every durable module's tables inherit from |
| `JsonB` | A JSON column. Native `jsonb` on Postgres, plain JSON elsewhere |
| `VectorColumn` | An embedding column — a `list[float]` stored as JSON |
| `UtcDateTime` | A timestamp that is always a real moment in UTC |
| `EMBED_DIM` | `3072`, the width of the embedding model we use |

### Why it is not part of the core

`aegis.core` is imported by every module. Anything the core depends on, everything depends
on. An ORM is heavy — it pulls a database driver and has strong opinions about async code.
Put SQLAlchemy in the core and the guardrails module carries a database driver it will never
open.

So the data layer is a second, optional foundation. It ships behind an extra:

```toml
data = ["sqlalchemy[asyncio]>=2.0"]
```

Modules that store things pay for the ORM. Modules that do not, do not. A test asserts the
core never loads SQLAlchemy.

### One schema, two databases

Production runs PostgreSQL. Unit tests run on a laptop with no Docker, so they run on SQLite.
The same table definitions have to work on both. If tests run against a different schema from
production, they are testing a different system.

That is hard because the two databases do not have the same types. Declare a column with
Postgres's native binary-JSON type and creating the schema on SQLite fails outright — the
test suite cannot even get a database. Declare it as generic JSON and it works everywhere,
but production loses the fast `jsonb` type.

The answer is to express the difference once, in the type, so no calling code has to know:

```python
JsonB = JSON().with_variant(JSONB, "postgresql")
```

SQLAlchemy picks the right one from whichever database is connected. No `if postgres:`
branches scattered through the modules.

### Timestamps

`with_variant` swaps the *type*. Sometimes you also need to change the *value* on the way in
and out. Timestamps are that case, and they are the reason `UtcDateTime` exists.

A Python `datetime` is either **aware** — it knows its offset from UTC — or **naive**, which
is just a wall-clock reading with no offset. Postgres has two matching column types. A plain
`Mapped[datetime]` maps to the naive one, and that is a trap in two ways. The driver refuses
to write an aware value into a naive column, so every write from normal application code
raises. And a `server_default` on a naive column stores the database server's *local* clock,
so on a server set to Asia/Kolkata every row is five and a half hours off, with nothing to
show it.

`UtcDateTime` closes both. It renders as `timestamptz` on Postgres and plain `DATETIME`
elsewhere. On the way in it converts whatever you give it to UTC. On the way out it always
returns an aware UTC value, so `datetime.now(UTC) - row.created_at` can never blow up.

The move that matters is where it is applied. Not on each column — on the base:

```python
class AegisBase(DeclarativeBase):
    type_annotation_map = {datetime: UtcDateTime}
```

Every `Mapped[datetime]` on every table inheriting this base becomes a `UtcDateTime`,
including the ones nobody has written yet. Fixing each column is right today and wrong on the
next column someone adds.

### The vector column

Each memory fact carries an embedding — a list of numbers representing its meaning. The
obvious move is a pgvector column with distance operators and a nearest-neighbour index. That
would add a Postgres extension to every deployment, and it does not exist on SQLite at all.

Before paying for it, ask what the column is *for*. In Aegis, similarity search does not run
in Postgres. It runs in an embedded vector store, and the SQL row is mirrored into it. So the
column is not a search index. It is the durable record the index is built from — and a record
of a list of floats is portable JSON. `VectorColumn` stores it as `jsonb` on Postgres and
JSON elsewhere. No extension anywhere, same schema in both places.

One honest note: JSON does not enforce width. `VectorColumn(3072)` documents the
dimensionality, it does not police it. Rows of the wrong width are skipped when the mirror
reads them.

### Creating and changing the schema

Aegis has no migration tool. The application creates tables at startup with `create_all`.
There is one rule to carry away from this:

> `create_all` is "create table if it does not exist". It never alters a table that already
> exists.

So a column added to a model appears in tests, which build the database from scratch, and
never appears in a long-lived production database. Writes naming it fail silently wherever
the caller catches errors. That is why bootstrap runs two repair steps after `create_all`,
both in `aegis/src/aegis/governance/schema.py` and `backend/src/app/data/session.py`:

| Step | What it does |
|---|---|
| `reconcile_additive_columns` | Adds columns the models declare and the live tables lack |
| `_align_timestamp_columns` | Converts old naive timestamp columns to `timestamptz` |

The reconciler holds to four limits, on purpose. It is **additive only** — it never drops,
renames or retypes, so it cannot destroy data. It is **idempotent**, so every worker can run
it at boot and a second run finds nothing to do. It is **Postgres-only**; SQLite returns
immediately. And it is **loud** — every added column is logged, and a column it cannot add
safely raises `SchemaDriftError` and the application refuses to start.

"Safely addable" has a precise meaning: a plain `ADD COLUMN` must have a correct value for
the rows already in the table. So the column must be nullable, or carry a server default. A
`NOT NULL` column with no default has no correct value for last March's rows, and only a
human can decide one.

Refusing to boot sounds harsh. The alternative is running with a table whose writes are
failing right now — and for the usage ledger, that means budget caps that quietly stop
binding while every dashboard stays green.

### Row-level security

The tables here are tenant-scoped, so Postgres row-level security is switched on: a policy
attached to a table filters every query, so a query that forgets its `WHERE` clause still
cannot see another tenant's rows. `aegis/src/aegis/governance/rls.py` sets it up.

Two rules worth knowing. **Postgres exempts a table's owner from its own policies** unless
you also issue `ALTER TABLE ... FORCE ROW LEVEL SECURITY` — and Aegis connects with the same
role that created the tables, so without `FORCE` the policy is visible and enforced against
nobody. And **the tenant scope must be transaction-local**, set with
`set_config(name, value, true)`, because with a connection pool a session-level setting
leaks into whichever request borrows that connection next.

RLS is a second layer, not the layer. Every governed query also filters `tenant_id` in the
application, which is the only protection that exists on SQLite.

---

## 3. How you use it in code

```python
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from aegis.data import EMBED_DIM, AegisBase, JsonB, UtcDateTime, VectorColumn


class MemoryFact(AegisBase):
    __tablename__ = "memory_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[str] = mapped_column(index=True)
    payload: Mapped[dict] = mapped_column(JsonB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(VectorColumn(EMBED_DIM))
    created_at: Mapped[datetime] = mapped_column(index=True)   # becomes UtcDateTime
```

Note `created_at`. You write a plain annotation and get the correct timestamp type, because
the base decides.

### Creating the schema

The host owns this. The order matters:

```python
import aegis.governance.models   # noqa: F401 - registers its tables
import aegis.memory.stores       # noqa: F401 - registers its tables

async with engine.begin() as conn:
    await conn.run_sync(AegisBase.metadata.create_all)
    await reconcile_additive_columns(conn, [AegisBase.metadata])
await bootstrap_rls(engine)
```

Those two imports look pointless and are not. A table class in a module nobody imported is
not registered, so `create_all` never sees it and the table is simply never created. No error,
no warning — the first query against it fails much later, somewhere else.

### The functions a caller touches

| Function | From | What it does |
|---|---|---|
| `reconcile_additive_columns(conn, metadatas)` | `aegis.governance.schema` | Adds missing columns. Returns the names it added. Raises `SchemaDriftError` if it cannot |
| `plan_additive_columns(existing, metadatas)` | `aegis.governance.schema` | The same decision as a pure function, no database. Useful in tests and CI |
| `bootstrap_rls(engine)` | `aegis.governance.rls` | Enables and forces the tenant policy on every scoped table |
| `set_tenant_scope(session, tenant_id)` | `aegis.governance.rls` | Binds the current tenant for this transaction |

There are no settings in this module. The one constant you might care about is `EMBED_DIM`,
which is tied to the embedding model, not to a preference.

---

## 4. Why it helps us

**Two modules share a database without knowing about each other.** Memory and governance each
depend on a 140-line package, not on one another. Either can be installed alone.

**Tests run the real schema.** The same table definitions materialise on SQLite, so the whole
suite runs on a laptop with no Docker, no Postgres, and no cloud.

**Timestamps cannot go wrong by omission.** A new column gets the right type from the base.
Nobody has to remember.

**No vector database extension anywhere.** Embeddings are ordinary JSON, so Aegis installs on
a locked-down machine where you cannot add Postgres extensions.

**A drifted schema stops the deploy instead of quietly draining the budget.** Without the
reconciler and its refusal, a missing column means lost ledger rows, which means spending caps
that never bind — with no error anywhere.

Without this module, every durable module would carry its own base, its own dialect branches,
and its own timestamp bug.

**Next:** [`40-diagrams.md`](40-diagrams.md)
