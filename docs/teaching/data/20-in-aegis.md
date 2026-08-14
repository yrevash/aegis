# Data — in Aegis

`aegis.data` is **one file, 139 lines**, plus a 15-line `__init__` that re-exports it. It
contributes no engine, no session, no migrations and no tables of its own — only the
*shape vocabulary* other modules build their tables with
(`aegis/src/aegis/data/base.py:1-8`).

> The host owns the engine/sessionmaker and drives `AegisBase.metadata.create_all` (plus
> any RLS bootstrap); this module owns only the *shape* of the store.

---

## How you import it

```python
from aegis.data import AegisBase, JsonB, UtcDateTime, VectorColumn, EMBED_DIM

class MemoryFact(AegisBase):
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(255), index=True)
    embedding: Mapped[list[float] | None] = mapped_column(VectorColumn(EMBED_DIM), default=None)
    source_turn_ids: Mapped[list[int]] = mapped_column(JsonB, default=list)
    created_at: Mapped[datetime]          # ← becomes UtcDateTime automatically
```

Available under the `aegis[data]` extra, which is one line
(`aegis/pyproject.toml`):

```toml
data = ["sqlalchemy[asyncio]>=2.0"]
```

`pgvector` was **removed** once vector search moved to Qdrant
(`aegis/src/aegis/data/base.py:17-19`).

---

## 1. What the package holds

`aegis/src/aegis/data/__init__.py:13-15`:

```python
from aegis.data.base import EMBED_DIM, AegisBase, JsonB, UtcDateTime, VectorColumn

__all__ = ["EMBED_DIM", "AegisBase", "JsonB", "UtcDateTime", "VectorColumn"]
```

Five names. That is the whole public surface.

**`EMBED_DIM = 3072`** (`base.py:34`) — the embedding width of
`genailab-maas-text-embedding-3-large`, fixed as one shared constant so every module
declaring a vector column agrees without re-deriving it.

---

## 2. `JsonB` — the variant (`base.py:36-38`)

```python
# A JSON column that uses native ``jsonb`` on PostgreSQL and portable ``JSON``
# elsewhere (keeps ``create_all`` working on the SQLite test database).
JsonB = JSON().with_variant(JSONB, "postgresql")
```

One line, no custom class — because JSON needs a different *type* per dialect, not a
different *value transformation*. That is the `with_variant` case.

Used across all three durable modules:
`aegis/src/aegis/memory/stores.py:152`, `:168`, `:193-194`;
`aegis/src/aegis/governance/models.py:219`;
`aegis/src/aegis/ops/models.py:56`, `:86`.

---

## 3. `VectorColumn` — the source of record, not an index (`base.py:41-66`)

```python
class VectorColumn(TypeDecorator[list[float]]):
    impl = JSON
    cache_ok = True

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim
        super().__init__()

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())
```

The docstring (`:42-52`) is careful about what this column **is**:

> The vector is persisted as a JSON array — native `jsonb` on PostgreSQL, portable `JSON`
> on every other dialect. It is the durable *source-of-record* embedding that the memory
> index lazily mirrors into Qdrant; it is **not** a search index and carries no pgvector
> distance operators. ANN search runs on
> `aegis.retrieval.vector_store.QdrantVectorStore`.

And the honesty note about `dim` (`:50-52`):

> `dim` is retained for documentation/parity with the embedding dimensionality; **JSON
> storage does not enforce it** (the mirror skips off-dim rows at query time).

That is the right way to keep a parameter you cannot enforce: say it is documentation, and
say what actually handles the mismatch.

The test pins the whole migration (`aegis/tests/data/test_vector_column.py:23-36`):

```python
assert not hasattr(aegis.data, "VectorType"), "VectorType must be deleted"
assert "VectorColumn" in aegis.data.__all__
assert EMBED_DIM == 3072
...
pg_impl = col.load_dialect_impl(pg_dialect())
assert isinstance(pg_impl, JSONB)          # NOT a pgvector vector(dim)
```

Note the first assertion: it asserts the **absence** of the old name. A migration that
leaves the old symbol importable is a migration people keep using.

---

## 4. `UtcDateTime` — the trap, closed (`base.py:69-120`)

The longest docstring in the file, and it is a bug report
(`:70-94`). Two failure modes, named:

> - asyncpg refuses to encode an *aware* datetime for a naive column and raises
>   `DataError: can't subtract offset-naive and offset-aware datetimes` — so every write
>   **and** every `WHERE ts < :now` comparison from aware application code blows up at
>   runtime (**this is what killed the SLA sweeper**).
> - `server_default=func.now()` on a naive column stores the *server's local wall clock*,
>   not UTC. On a box with `TimeZone = Asia/Kolkata` every `created_at` is silently +05:30
>   off, and the API then re-labels it `+00:00`.

Four behaviours close both holes:

**`load_dialect_impl`** (`:99-103`) — `TIMESTAMP(timezone=True)` on Postgres, plain
`DateTime()` elsewhere.

**`process_bind_param`** (`:105-114`) — normalise either input form:

```python
value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
if dialect.name == "postgresql":
    return value
# SQLite (and friends) store the naive UTC wall clock; the offset would
# otherwise be dropped silently and corrupt lexical ordering.
return value.replace(tzinfo=None)
```

That comment is the SQLite half of the trap: ISO-string comparison in SQLite is
**lexical**, so an offset suffix would sort wrongly.

**`process_result_value`** (`:116-120`) — always return aware UTC, so
`datetime.now(UTC) - row.created_at` can never raise regardless of dialect.

**And the enforcement point** — `AegisBase` (`:123-136`):

```python
class AegisBase(DeclarativeBase):
    type_annotation_map = {datetime: UtcDateTime}
```

The docstring (`:130-135`) says why this is on the *base*:

> Every `Mapped[datetime]` on this base materialises as `UtcDateTime` — the single
> enforcement point that keeps timestamps true UTC instants on PostgreSQL and stops the
> naive/aware mismatch class of bug **from being reintroduced by a future column**.

A column added next year by someone who has never heard of this problem is correct by
default. That is a bug **class** closed, not a bug fixed.

---

## 5. Who registers on this base

| Module | File | Tables |
|---|---|---|
| `aegis.memory` | `stores.py:65`, `:82`, `:123`, `:160`, `:178`, `:201` | sessions, messages, facts, profiles, write log, consolidation jobs |
| `aegis.governance` | `models.py:73`, `:90`, `:124`, `:154`, `:198` | tenants, users, budgets, usage ledger, audit log |
| `aegis.ops` | `models.py:40`, `:68` | eval results, prompt versions |

All three docstrings say the same thing (e.g. `memory/stores.py:3-6`): they register on
the shared metadata *"so a host's `AegisBase.metadata.create_all` materialises them — on
PostgreSQL with native `jsonb`… and on the SQLite test database via the cross-dialect
`VectorColumn`/`JsonB` decorators."*

Three modules, one base, and none imports another.

---

## 6. Why SQLAlchemy is banned from `aegis.core`

The guard test (`aegis/tests/core/test_core_is_dep_free.py:34-35`) names it explicitly,
and the docstring (`:19-24`) says where it belongs instead:

> `sqlalchemy`/`jwt`/`argon2` live in `aegis.data` / `aegis.governance` (the
> `aegis[data]` / `aegis[governance]` extras)… **never in `aegis.core`**, which stays
> pydantic-only.

The layering:

```
aegis.core   (pydantic + stdlib, free)
    ↑
aegis.data   (sqlalchemy[asyncio], the aegis[data] extra)
    ↑
aegis.memory / aegis.governance / aegis.ops
```

`aegis.guardrails`, `aegis.vision`, `aegis.voice` and `aegis.forecast` depend on
`aegis.core` and **not** on `aegis.data` — so none of them carries an ORM. The vision
isolation test bans `sqlalchemy` by name
(`aegis/tests/vision/test_isolation.py:22-25`).

---

## 7. The migration gap and the reconciler

### The bug, in the model's own docstring

`aegis/src/aegis/governance/models.py:167-174`:

> A LIVE Postgres created before these columns existed does **not** grow them from
> `create_all` (which is CREATE TABLE IF NOT EXISTS and never alters a table), and until
> it does, **every ledger INSERT raises `UndefinedColumn`** — swallowed, because usage
> recording is best-effort at the gateway, so **the row is simply lost and the USD caps
> computed from these rows stop binding.**

The two columns are `audio_seconds` and `images` (`:190-193`), added because voice bills
per audio-minute and vision per image.

### `reconcile_additive_columns` — `aegis/src/aegis/governance/schema.py`

The module docstring (`:1-38`) is the full argument. Four properties, each named:

**Additive only** (`:20-22`) — *"It never drops, renames, retypes or reorders anything, so
it cannot destroy data and is safe to run on every boot."*

**Idempotent** (`:23-26`) — the plan is computed from `information_schema`; the DDL also
carries `IF NOT EXISTS`, *"so two processes racing at startup cannot collide."*

**Postgres-only** (`:27-31`) — SQLite returns immediately, *"the test suite recreates its
schema from scratch on every run, so it has no drift to reconcile."*

**Loud** (`:32-36`) — every added column logged at INFO; a column that cannot be added
safely raises `SchemaDriftError`:

> Refusing to boot is the correct outcome for the ledger: the alternative is a running
> system whose spend caps silently do not bind.

The pieces:

**`SchemaDriftError`** (`:57-70`) defines "safely":

> addable by a plain `ALTER TABLE ... ADD COLUMN` with no back-fill decision to make —
> i.e. the column is nullable, or it carries a `server_default` the database can apply to
> every existing row. A `NOT NULL` column with no server default has no correct value for
> the rows already present, so **only a human can decide what it should be.**

**`plan_additive_columns`** (`:89-124`) is **pure and database-free** (`:92-93`), *"so the
decision this module makes is testable without a live PostgreSQL."* A table that does not
exist at all is skipped (`:115-116`) — `create_all` owns brand-new tables.

**`_column_ddl`** (`:129-144`) renders via SQLAlchemy's own `CreateColumn` compiler
(`:131-134`):

> Compiling from the declarative metadata (rather than hand-writing SQL) keeps the added
> column's type, nullability and server default **identical to what `create_all` would
> have produced on a fresh database.**

**`_indexes_for`** (`:148-169`) creates an index only when **all** its columns are being
added in this pass — so an added column is never left half-installed, and index drift on
pre-existing columns stays out of scope.

**`reconcile_additive_columns`** (`:174-254`):

- Non-Postgres → return `[]` immediately (`:196-197`)
- Unsafe drift → log at **CRITICAL** *and* raise (`:203-217`). The comment at `:212-215`:
  *"a host that wraps its bootstrap in a broad 'the database is optional' handler would
  otherwise reduce this to a traceback nobody reads."*
- Each `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` is executed, and a failure is **never
  swallowed** (`:225-238`) — logged at CRITICAL and re-raised.

### `_align_timestamp_columns` — the second reconciliation

`backend/src/app/data/session.py:176-227` does the same job for the *type* change.

`create_all` never altered an existing table, so a database bootstrapped before timestamps
became `UtcDateTime` still carries naive columns — *"and every aware-UTC bind from the
application keeps failing (`asyncpg.exceptions.DataError`) — **the SLA sweeper's
crash-per-cycle**"* (`:182-186`).

The conversion (`:220-226`):

```sql
ALTER TABLE "t" ALTER COLUMN "c" TYPE timestamptz USING "c" AT TIME ZONE 'UTC'
```

`AT TIME ZONE 'UTC'` reinterprets the stored naive value as UTC — *"the meaning the
application already assigned to a stored naive timestamp everywhere it read one"*
(`:191-192`).

It is idempotent (`:186-188`): it only touches columns still reported naive by
`information_schema`, and it is a no-op on SQLite and on an already-converted database.

### Where both run — `bootstrap()` (`session.py:232-280`)

```python
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)        # the host's own tables
    await conn.run_sync(AegisBase.metadata.create_all)   # the aegis modules' tables
    await reconcile_additive_columns(conn, metadatas)
    await _align_timestamp_columns(conn, metadatas)
await bootstrap_rls(engine)
```

The docstring (`:238-260`) states the ownership: *"With no Alembic in this project, **this
function is the schema owner**."*

Note `:263-272` — the memory and governance models are imported for their **registration
side effect** before `create_all`, because a mapped class in an unimported module is not
in the metadata and its table will not be created.

And `backend/src/app/main.py:155` re-raises `SchemaDriftError` ahead of the blanket
startup handler, so the refusal survives: *"DB bootstrap FAILED: irreconcilable schema
drift. Refusing to serve…"*

---

## 8. Row-level security on top

`aegis/src/aegis/governance/rls.py:108-144` enables **and forces** RLS on the
tenant-scoped tables, with `FORCE ROW LEVEL SECURITY` called out at `:114` as *"**the
load-bearing statement**"* — because Postgres exempts the table owner from policies
otherwise, and applications commonly connect as the owner.

`set_tenant_scope` binds the per-connection scope; the forecast ledger reader uses it
alongside an explicit `WHERE tenant_id = …`
(`backend/src/app/forecast/ledger.py:73-77`) — *"the same belt-and-suspenders isolation,
reused rather than reinvented"* (`:16-19`).

---

## Where to look

| Claim | File:line |
|---|---|
| The package owns shape, not lifecycle | `aegis/src/aegis/data/base.py:1-8` |
| `JsonB` variant | `aegis/src/aegis/data/base.py:36-38` |
| Vector column is a record, not an index | `aegis/src/aegis/data/base.py:42-52` |
| `dim` is documentation, not a constraint | `aegis/src/aegis/data/base.py:50-52` |
| The two datetime failure modes | `aegis/src/aegis/data/base.py:70-94` |
| SQLite must store naive UTC | `aegis/src/aegis/data/base.py:112-114` |
| One enforcement point on the base | `aegis/src/aegis/data/base.py:130-136` |
| `create_all` never alters | `aegis/src/aegis/governance/models.py:167-174` |
| Ledger rows lost, caps stop binding | `aegis/src/aegis/governance/schema.py:9-14` |
| Unsafe drift raises, logged CRITICAL | `aegis/src/aegis/governance/schema.py:203-217` |
| DDL rendered by SQLAlchemy's compiler | `aegis/src/aegis/governance/schema.py:131-134` |
| The SLA sweeper's crash-per-cycle | `backend/src/app/data/session.py:182-186` |
| Bootstrap is the schema owner | `backend/src/app/data/session.py:238-241` |
| `FORCE ROW LEVEL SECURITY` | `aegis/src/aegis/governance/rls.py:114` |

**Next:** [`30-deep-dive.md`](30-deep-dive.md).
