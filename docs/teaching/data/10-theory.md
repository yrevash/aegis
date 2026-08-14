# Data — the theory

SQLAlchemy 2.0's typing model, type decorators and variants, the Postgres timestamp
semantics that cause the trouble, schema-evolution theory, and what row-level security
actually enforces.

---

## 1. SQLAlchemy 2.0's declarative model

**`DeclarativeBase`** replaced the old `declarative_base()` factory. You subclass it, and
every mapped class inheriting from your subclass registers on **one shared `MetaData`
object** — a registry of tables, columns, constraints and indexes.

`MetaData.create_all(engine)` walks it and emits DDL. That is the only thing it does, and
§4 is about what it does not do.

**`Mapped[T]` and `mapped_column()`** are the 2.0 annotation-driven style: the Python type
annotation drives the SQL type.

```python
class Budget(AegisBase):
    id: Mapped[int] = mapped_column(primary_key=True)
    limit_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime]
```

**`type_annotation_map`** is the hook that makes this interesting. Set it on the base and
you override which SQL type a given Python annotation resolves to — for **every** column
on every table that inherits from it:

```python
class AegisBase(DeclarativeBase):
    type_annotation_map = {datetime: MyTimestampType}
```

This is a **bug-class-closing** mechanism, not a bug-fixing one. Every `Mapped[datetime]`
now gets the correct type, including columns written years later by people who never heard
of the problem. Compare with fixing each column by hand: correct today, wrong on the next
one someone adds.

---

## 2. Type decorators and variants

Two mechanisms for cross-dialect portability, and they solve different problems.

### `TypeDecorator`

A wrapper around an existing type that can override:

- **`load_dialect_impl(dialect)`** — pick a different underlying type per dialect. Called
  at DDL and compile time.
- **`process_bind_param(value, dialect)`** — transform Python → database on the way in.
- **`process_result_value(value, dialect)`** — transform database → Python on the way out.

The bind/result pair is what makes a decorator more than a type alias: it can *normalise*.
A timestamp decorator that coerces every input to UTC and returns every output as aware
UTC gives you an invariant the type system enforces, at the one place every read and write
passes through.

**`cache_ok = True`** matters for performance. SQLAlchemy caches compiled statements, and
a custom type must declare that it is safe to cache — meaning its behaviour depends only
on its constructor arguments, not on mutable external state. Omit it and you get a warning
plus a cache miss on every statement using the type.

### `.with_variant()`

Simpler: one type, with a per-dialect substitution.

```python
JsonB = JSON().with_variant(JSONB, "postgresql")
```

Native `jsonb` on Postgres, generic `JSON` elsewhere. No custom class needed.

**When to use which.** `with_variant` when you only need a different *type*.
`TypeDecorator` when you also need to *transform values* — which is exactly why a
timestamp needs a decorator and JSON does not.

The important property either way: the dialect branch lives **at the type**, once, rather
than as `if dialect == "postgresql"` scattered through the application. Scattered branches
are where behaviour actually diverges between test and production.

---

## 3. Postgres timestamps, precisely

This is the trap that generates the most real incidents, so it is worth being exact.

### The two types

**`TIMESTAMP WITHOUT TIME ZONE`** — a date and a time, with **no offset**. Postgres stores
what you give it and returns it unchanged. It represents a *wall-clock reading*, not a
moment.

**`TIMESTAMP WITH TIME ZONE`** (`timestamptz`) — despite the name, Postgres **does not
store a timezone**. It converts the input to UTC, stores a UTC instant, and converts back
to the session's `TimeZone` on output. It represents a *moment*.

For anything that happened — a request, a payment, a log line — you want `timestamptz`,
because you want a moment.

### Failure A: the driver refuses the write

`asyncpg` maps Python's aware/naive distinction onto the two column types strictly. Hand
it an aware datetime for a naive column and it raises. Handle it in the other direction
and it raises too.

So an application that is uniformly `datetime.now(UTC)` — which is what every style guide
recommends — fails on **every write** to a naive column, and on every
`WHERE ts < :now` comparison built from an aware value.

Loud, at least. It fails immediately and obviously.

### Failure B: `server_default=func.now()` on a naive column

This one is silent, and it is the dangerous one.

`now()` in Postgres returns `timestamptz`. Assigning it to a **naive** column casts it,
and the cast uses the session's `TimeZone` setting. So the stored value is the server's
**local wall clock**.

On a server configured `TimeZone = 'Asia/Kolkata'`, every `created_at` is **+05:30** off
UTC. Nothing errors. The rows look plausible. And if your API layer then serialises those
naive values with a `+00:00` suffix — a very common shortcut — you are publishing a wrong
instant with a confident timezone label.

The symptom surfaces months later as "the audit log timestamps do not line up with the
access log."

On `timestamptz`, `now()` is correct regardless of the server's `TimeZone`, because the
stored value is a UTC instant either way.

### SQLite, for contrast

SQLite has no timezone-aware type at all. Datetimes are stored as ISO strings (or numbers)
and come back naive.

So a portable decorator has to do something specific: on SQLite, store **naive UTC**, and
strip the offset explicitly on bind. If you let an aware datetime serialise with its
offset into a text column, you break **lexical ordering** — `2026-01-01T00:00:00+05:30`
sorts after `2026-01-01T00:00:00+00:00` as text even though it is an earlier instant — and
SQLite comparisons on ISO strings are lexical.

And on the way out, return aware UTC on both dialects, so
`datetime.now(UTC) - row.created_at` never raises regardless of where the row came from.

---

## 4. Schema evolution

### What `create_all` is

`CREATE TABLE IF NOT EXISTS`, for every table in the metadata. That is the whole contract.

It does not diff. It does not alter. It does not know or care that the live table has six
columns while the model declares eight. It checks existence and moves on.

For a fresh database it produces exactly the right schema, which is why it is the default
in tutorials and why so many projects ship it as their only mechanism.

### What a migration tool adds

Alembic (or equivalent) provides:

- **Versioned scripts** with an explicit up/down path
- **Autogeneration** by diffing metadata against the live schema
- **A version table**, so a database knows which migrations it has applied
- **Ordering**, so a chain applies deterministically

That is the right answer for a long-lived production database. The costs are real too:
another dependency, a migrations directory, a review burden on every schema change, and
autogenerated diffs that need human checking (Alembic reliably misses server defaults and
type changes).

### The additive reconciler

If you deliberately have no migration tool, the honest minimum is a startup step that
closes the *most common* drift: a column the models declare and the database lacks.

Its contract has four parts, and each is a deliberate limit:

**Additive only.** Add columns; never drop, rename, retype or reorder. It cannot destroy
data.

**Idempotent.** Compute the plan from `information_schema` — the live truth — so a second
run finds nothing. Emit `ADD COLUMN IF NOT EXISTS` as well, so two processes racing at
startup cannot collide.

**Safe or fatal.** A column is safely addable if a plain `ALTER TABLE ... ADD COLUMN` has
a correct value for the rows already present — i.e. it is **nullable**, or it carries a
**server default** the database can apply. A `NOT NULL` column with no server default has
no correct value for existing rows, and only a human can decide one. That must **raise**,
not be skipped.

**Scoped.** Constraints and indexes on pre-existing columns are out of scope. But an index
declared *entirely on newly added columns* should be created alongside them, or the column
is half-installed.

One implementation detail worth stealing: **render the DDL with the ORM's own compiler**
rather than hand-writing SQL strings. Then the added column's type, nullability and server
default are byte-identical to what `create_all` would have produced on a fresh database —
which means a reconciled database and a fresh one converge instead of quietly differing.

### Why "refuse to boot" is correct

Refusing to start on unreconcilable drift sounds extreme. Consider the alternative.

Booting means running with a table whose writes are failing. If that table is the usage
ledger, and usage recording is best-effort (swallowed), then the system runs with **no cost
attribution and no binding budget caps**, indefinitely, with every dashboard green.

A refusal is loud, immediate, and gets fixed in an hour. A silent degradation runs until
an audit.

The corollary: **the refusal must not be swallowed either.** A host that wraps its
bootstrap in a broad "the database is optional" handler will reduce a `SchemaDriftError` to
a traceback nobody reads. Logging at CRITICAL *as well as* raising is belt and braces for
exactly that.

---

## 5. Best-effort writes, and where they are wrong

"Best effort" — wrap a non-essential write in a broad `except` so a failure cannot break
the request — is a legitimate pattern. Telemetry, analytics, a cache warm.

It has a precondition that is rarely stated: **nothing important may be computed from the
data you are dropping.**

The usage ledger violates it. It looks like telemetry — it is a log of what happened. But
budget enforcement *sums those rows*. So a dropped row is not a lost log line; it is a
**silently relaxed spending limit**.

Two mitigations, both worth knowing:

- **Alert on the swallow.** A best-effort write that starts failing 100% of the time is an
  incident. Counting and alerting on the exception rate turns a silent failure into a
  visible one.
- **Distinguish the failure modes.** A transient network error and a schema error are
  different: the first is genuinely best-effort, the second is permanent and will affect
  every subsequent call. Catching them identically is how a permanent failure hides inside
  a pattern designed for transient ones.

---

## 6. Row-level security, briefly

Because the tables this base carries are tenant-scoped, and this is where the enforcement
lives.

**RLS** attaches a policy to a table; every query is implicitly filtered by it. You set a
per-connection variable identifying the tenant, and the policy admits only matching rows.
Even a query that forgets its `WHERE` clause returns nothing.

Two subtleties that both bite in practice:

**Postgres exempts the table owner from policies** unless you also issue
`ALTER TABLE ... FORCE ROW LEVEL SECURITY`. Applications commonly connect as the owner —
so RLS can be "enabled" and enforced against nobody. `FORCE` is the load-bearing statement.

**Scope must be transaction-local, not session-local.** With a connection pool, a variable
set at session scope survives into the next request that borrows that connection. That is
one tenant's scope applied to another tenant's query — the worst kind of leak, and it only
manifests under concurrency.

RLS is defence in depth, not a replacement for application-level filtering. Both, always.

---

## 7. Vector storage: index or record?

A vector column in SQL can play two roles, and the distinction decides your dependencies.

**As a search index** you need `pgvector`: a `vector(n)` type, distance operators
(`<->`, `<=>`), and an ANN index (IVFFlat or HNSW). That is a Postgres extension —
installable on managed services, but a real deployment dependency, and it does not exist
on SQLite at all.

**As a source of record** the column is just the durable copy. Search happens in a
purpose-built vector database, which the SQL row is mirrored into. Then the column is a
list of floats, and JSON stores that portably on every dialect.

Choosing the second removes an extension from the deployment and makes the schema
materialise on SQLite unchanged. It costs you the ability to run similarity search in SQL
— which is only a cost if you were going to.

One honesty point: JSON cannot enforce dimensionality. `vector(3072)` rejects a 1536-wide
row; a JSON array does not. So either validate at the application layer, or accept that
mismatched rows exist and make the reader skip them — and **say which**, because a
dimension parameter kept "for documentation" will be assumed to be a constraint by the
next reader.

---

## What you should now be able to explain

- `DeclarativeBase`, shared `MetaData`, and what `create_all` actually walks
- Why `type_annotation_map` closes a bug class rather than fixing a bug
- `TypeDecorator` vs `with_variant`, and why a timestamp needs the former
- What `cache_ok` declares and what omitting it costs
- `timestamptz` stores an instant and no timezone; naive stores a wall-clock reading
- The two datetime failure modes — the loud driver refusal and the silent server-clock offset
- Why SQLite must store naive UTC, and how an offset breaks lexical ordering
- Exactly what `create_all` does and does not do
- The four properties of an additive reconciler, and why unsafe drift must raise
- Why rendering DDL with the ORM's compiler keeps reconciled and fresh databases converged
- The precondition best-effort writes have, and why the usage ledger violates it
- `FORCE ROW LEVEL SECURITY`, and why session-scoped tenant variables leak across a pool
- When a vector column is an index and when it is a record

**Next:** [`20-in-aegis.md`](20-in-aegis.md).
