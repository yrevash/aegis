# Data — the concept, from zero

No code. Why a shared persistence foundation exists, what a dialect trap is, and why
"the schema is created on startup" is a sentence that eventually loses your data.

---

## The problem: two modules, one database

You have a memory module that stores conversation turns and distilled facts. You have a
governance module that stores tenants, users, budgets and a usage ledger. Both need
durable relational storage. Both use the same ORM.

An ORM needs a **declarative base** — one class that all your table classes inherit from,
which collects them into a shared *metadata* object. That metadata is what a schema
creator walks to emit `CREATE TABLE` statements.

So: whose base is it?

If memory defines it and governance inherits, then governance imports memory — a
dependency edge between two modules that have nothing to do with each other. If each
defines its own, you now have two metadata objects, the host has to create both, and any
foreign key between them is awkward at best.

The answer is the same move as everywhere else in a module contract: **hoist the shared
thing into a package both sides already depend on.** A tiny data package that owns the
base and nothing else.

And "nothing else" is the discipline. It contributes no engine, no session factory, no
migrations and no tables of its own. It defines the *shape vocabulary* other modules build
their tables with. The host still owns the engine, the sessionmaker, and the moment
schema creation happens.

---

## Why the ORM cannot live in the core

This is worth being precise about, because it looks like an inconsistency.

The rule is that the core is imported by everything and must therefore carry no heavy
dependency. An ORM is heavy — it is one of the largest dependencies in a typical Python
service, it pulls a driver, and it has strong opinions about async.

If the core carried it, the guardrails would carry it. The forecaster would carry it. A
module that never touches a database would drag in a database library, and a version
conflict in the ORM would become a conflict for every module in the system.

So there is a second, **optional** foundation: a data package that depends on the ORM and
that only the data-backed modules depend on. Modules that persist things pay for it;
modules that do not, do not.

The layering is: core (free) → data (an extra) → the durable modules.

---

## Dialect traps

Here is the constraint that shapes everything else.

Production runs PostgreSQL. Unit tests must run on a laptop with no Docker and no live
Postgres — so they run on SQLite. **The same schema definition has to materialise on
both.**

That is not a matter of being tidy. If your tests run on a different schema from
production, they are testing a different system, and every difference is a place a bug can
hide.

Three specific traps come up, and each has a different flavour.

### Trap 1 — a type that only exists in one dialect

Postgres has `jsonb`: binary JSON, indexable, with operators for containment and path
extraction. SQLite has none of that; it has `JSON` stored as text.

Write your column as `JSONB` and the schema simply **fails to create** on SQLite. Write it
as generic `JSON` and you lose the native type in production.

The answer is a **variant**: one declaration that compiles to `jsonb` on Postgres and
plain `JSON` everywhere else. The dialect chooses, at DDL time, automatically. No
`if postgres:` branch scattered through calling code — which is the part that matters,
because those branches are where behaviour actually diverges between environments.

The general shape: **express the difference once, at the type, not at every call site.**

### Trap 2 — naive versus aware datetimes

This one is nastier, because it does not fail loudly. It fails *quietly and wrongly*.

A Python datetime is either **aware** (it knows its timezone offset) or **naive** (it does
not). They are not interchangeable — subtracting one from the other raises a `TypeError`.

Postgres has two matching types: `TIMESTAMP WITH TIME ZONE` (`timestamptz`), which stores
a real instant, and `TIMESTAMP WITHOUT TIME ZONE`, which stores a wall-clock reading with
no offset.

A plain "datetime column" in most ORMs maps to the **naive** one by default. And that
default is a trap with two separate mouths:

**Writing.** Modern async Postgres drivers refuse to encode an aware datetime into a naive
column. So an application that is uniformly timezone-aware — as any correct application
should be — crashes on **every write** and on every comparison against `now()`.

**Server defaults.** A `server_default` of `now()` on a naive column stores the **database
server's local wall clock**, not UTC. On a machine configured for a non-UTC timezone,
every timestamp is silently offset — and if your API then labels those values `+00:00` on
the way out, you are reporting a wrong instant with a confident timezone suffix.

The second is the dangerous one. Nothing errors. The data is simply wrong by a fixed
offset, forever, and only someone who cross-checks against a known event will notice.

The fix is to make timestamps **one thing, enforced in one place**: a column type that
maps to `timestamptz` on Postgres and naive-UTC elsewhere, normalises whatever it is given
on the way in, and always returns aware UTC on the way out. Then downstream arithmetic
cannot raise, and there is no per-column decision to get wrong.

Better still: bind it to the *declarative base*, so **every** datetime column on every
table gets it automatically. A future column added by someone who has never heard of this
problem is correct by default. That is the difference between fixing a bug and closing a
bug class.

### Trap 3 — vector columns

If you do similarity search in the database, Postgres needs an extension (`pgvector`)
providing a `vector(n)` type and distance operators. SQLite has nothing comparable.

But there is a prior question worth asking: **does the SQL database need to be the search
index at all?**

If approximate-nearest-neighbour search runs in a purpose-built vector database, then the
SQL column is not an index — it is the **durable source of record** that the search index
mirrors. And a source of record is just a list of floats, which is portable JSON.

That reframing removes an entire extension from the deployment, and it is worth noticing
what changed: not the storage, but *what the column is for*. Getting that answer wrong in
either direction costs you — an unnecessary extension, or a search that does not scale.

The residual honesty point: a column that stores a list of floats as JSON cannot enforce
its dimensionality. If a dimension parameter is kept for documentation, it must be
documented as documentation, not as a constraint someone will assume is enforced.

---

## Migrations, and the sentence that loses your data

Here is the most important idea in this module, and it is one line:

> **`create_all` never alters an existing table.**

Every ORM has a convenience function that walks the metadata and creates the tables. It is
effectively `CREATE TABLE IF NOT EXISTS`. On an empty database it produces exactly the
right schema, which is why it feels sufficient and why so many projects ship with it as
their only schema mechanism.

On a database that already has the table, it does **nothing**. Not "nothing much" —
nothing at all. It sees the table exists and moves on.

Now watch the failure develop:

1. Day one: `create_all` builds `usage_ledger` with six columns. Correct.
2. Month three: someone adds two columns to the model, because voice bills per audio
   second and vision bills per image. Tests pass — the test database is created from
   scratch every run, so it gets all eight columns.
3. Deploy. The **production** table still has six. `create_all` sees it exists and does
   nothing.
4. Every `INSERT` naming the new columns now fails: *undefined column*.

Step 5 is where it becomes a disaster rather than an outage.

---

## Why "best effort" and "no migrations" is a catastrophic pair

Usage recording is usually written as **best effort**, and for a good reason: a logging
problem must not break a live customer request. So the write is wrapped in a broad
exception handler and failures are swallowed.

Individually, each decision is defensible. Together:

- The `INSERT` raises.
- The handler swallows it.
- The request succeeds.
- **The ledger row is lost.**

And now think about what the ledger is *for*. It is the source of truth for per-tenant
cost attribution — and the USD budget caps are computed by **summing those rows**.

No rows means no spend. No spend means the cap never binds.

So the system has, silently:

- stopped attributing cost
- stopped enforcing budgets
- kept serving requests perfectly
- kept every dashboard green

There is **no error anywhere**. The only symptom is a number that stays at zero, which
looks exactly like a quiet tenant.

**Two safe-looking decisions compose into an invisible, security-relevant failure.** That
is the pattern to be able to recognise: a swallowed exception plus a schema that silently
drifts.

---

## Closing the gap without adopting a migration framework

The proper fix is a migration tool — versioned scripts, an upgrade path, a history table.
Real projects should use one.

If you have deliberately not adopted one, then something must still own the schema, and
the honest minimum is an **additive reconciler** that runs at startup:

- Compare what the models declare against what the database physically has.
- **Add** columns that are missing.
- **Never** drop, rename, retype or reorder anything.

Four properties make that safe enough to run on every boot:

**Additive only.** It cannot destroy data, because it only adds.

**Idempotent.** The plan is computed from the live schema, so a second run finds nothing
to do.

**Loud.** Every added column is logged. And crucially, a column that *cannot* be added
safely — one declared `NOT NULL` with no default, where there is no correct value for the
rows already present — is not skipped. It **raises**, and the application refuses to
serve.

That last one deserves defending, because refusing to boot sounds extreme. It is not.
Booting means running with a table whose writes are failing *right now*, and for the usage
ledger that means uncapped, unattributed spend. A refusal is loud, immediate, and gets
fixed. A silent degradation runs for months.

**Scoped.** Constraints, indexes on pre-existing columns, and type changes are explicitly
out of scope — those need human decisions. But an index declared *on a newly added column*
should be created alongside it, so an added column is never left half-installed.

And the same reasoning covers a *type* change that was applied in code but never in the
database: a startup step that converts any timestamp column left naive by an earlier
schema creation, idempotently, on the one dialect where it matters.

---

## What you should now be able to explain

- Why two durable modules need a shared declarative base, and why neither should own it
- Why the ORM cannot live in the dependency-free core, and what the second layer is for
- Why the test and production schemas must be the same definition
- The variant-type trick, and why the branch belongs at the type rather than the call site
- Naive vs aware datetimes, the write failure and the silent server-local-clock failure
- Why binding the timestamp type to the *base* closes a bug class rather than a bug
- Why a vector column may be a source of record rather than an index — and what that changes
- **Why `create_all` never alters an existing table**
- How swallowed-exception writes plus schema drift compose into invisible budget failure
- What an additive reconciler is, and why refusing to boot is the correct loud failure

**Next:** [`10-theory.md`](10-theory.md).
