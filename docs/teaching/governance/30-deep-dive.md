# Governance — deep dive: five bugs, all verified against a live Postgres

These are the strongest stories in the whole codebase, because every one of them is a
control that **looked** enforced, passed code review, showed green in the test suite,
and enforced nothing.

Common thread: the tests run on **SQLite**. Every Postgres-specific behaviour — RLS,
session GUCs, `ALTER TABLE` semantics, the extended query protocol — is invisible to
them. A green suite was not evidence.

---

## Bug 1 — `SET app.tenant_id = :tid` is not executable

### What was happening

`set_tenant_scope` bound the tenant for RLS like this:

```python
await session.execute(text("SET app.tenant_id = :tid"), {"tid": str(tenant_id)})
```

It reads perfectly. It is parameterised, so no injection. It is exactly what the RLS
documentation implies.

**Postgres' `SET` takes a literal, not a bind parameter.** Sent over the extended query
protocol with a placeholder, the server raises:

```
PostgresSyntaxError: syntax error at or near "$1"
```

### The blast radius

`set_tenant_scope` is called at the top of **every governed data-layer call**: the usage
ledger write, the budget reads, the user listings, the usage rollups, the approvals
inbox. On Postgres, every one of those raised.

Not "returned wrong rows" — **raised**. The tenant-scoped call paths were broken
outright on the only database that has RLS at all.

### Why nobody caught it

`set_tenant_scope` returns at the dialect check three lines in:

```python
bind = session.get_bind()
if bind.dialect.name != "postgresql":
    return
```

The test suite runs SQLite. Every test hit that early return and passed. The function was
covered, exercised, and never once executed its actual body.

**This is the most valuable general lesson in this document.** A test suite on a
different database engine gives you *zero* coverage of engine-specific code, and
coverage tooling will happily report the function as covered because the first two lines
ran.

### The fix

`aegis/src/aegis/governance/rls.py:70-76`:

```python
if tenant_id is None:
    await session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
else:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )
```

`set_config` is an ordinary SQL function, so it parameterises correctly. **And
parameterising matters** — the alternative, interpolating the value into the statement
string, would be SQL injection in the one place you least want it.

### The second fix hidden inside the first

The third argument, `is_local`, is `true`. That scopes the GUC to the **current
transaction**, so it is discarded on commit or rollback.

A session-level `SET` persists for the life of the **connection**. Under a connection
pool, that connection is handed to the next request — still carrying the previous
tenant's scope. Tenant A's setting silently governs tenant B's query.

So the correct call fixes two independent bugs: one that made every tenant-scoped path
raise, and one that would have leaked scope across pooled connections the moment the
first was fixed. The comment at `rls.py:55-69` records both, numbered.

---

## Bug 2 — RLS was ENABLEd but never FORCEd, so the app bypassed every policy

### What was happening

`bootstrap_rls` did the textbook thing:

```sql
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON users USING (...);
```

Correct SQL. Visible in `pg_policies`. Reviewable. Enforcing nothing.

### The rule that makes it useless

**Postgres exempts a table's owner from that table's RLS policies unless
`FORCE ROW LEVEL SECURITY` is also set.**

Now look at how this application connects. `create_all` and `bootstrap_rls` both run on
the **serving engine** — one engine creates the tables and is then the engine every
request uses (`backend/src/app/data/session.py`, `bootstrap()`). So the application
connects as the table **owner**.

Every policy was therefore decorative for the only role that ever queried the tables.

### Why this is such a good interview answer

It is a bug you cannot see in the code. The code says `ENABLE ROW LEVEL SECURITY`. A
reviewer reads that and ticks the box. `pg_policies` shows a healthy row. A security
questionnaire asking "do you use row-level security?" gets a truthful yes.

The only way to find it is to *test the negative*: connect as the application, bind
tenant A, and assert that tenant B's row is **not** returned.

### The fix

`rls.py:143-145`:

```python
await conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
```

with the comment `# Without this the owner — i.e. the application's own role — bypasses
every policy below.`

The alternative — connect as a non-owning role with DML-only rights — is more classically
correct and requires provisioning a second role and separating migration from serving.
`FORCE` is one statement and no infrastructure change.

### And the honest part of the fix

Turning FORCE on means the policy now actually applies, which immediately exposes the
predicate's unbound branch. Two paths legitimately run with no tenant bound: the **login
lookup** (reads `users` by username before any tenant is known) and the **platform-admin
listings** (span every tenant). Under a strictly fail-closed unset branch, both would
return zero rows — including login.

So the predicate at `rls.py:101-105` says: *a bound numeric scope is strictly enforced;
no bound scope does not restrict.* The docstring at `rls.py:79-100` documents this as a
deliberate choice with the follow-up named — the host must bind a scope on those paths to
close it.

**Say it that way in an interview.** "Strictly more enforcement than before, with a named
remaining gap and the work required to close it" is a much stronger answer than claiming
airtight isolation. (Note the one place the codebase overstates it:
`config.py:119` reports `fail_closed=True`, which the predicate does not support.)

---

## Bug 3 — one tenant could take over another tenant's spending cap

### What was happening

`upsert_budget` is idempotent on the natural key so the admin UI can re-post the same
scope+window to adjust caps. It looked up:

```python
select(Budget).where(
    Budget.scope_type == scope,
    Budget.scope_id  == scope_id,
    Budget.window    == win,
)
```

and then assigned `existing.tenant_id = tenant_id` unconditionally.

The triple `(scope_type, scope_id, window)` is **global**. It contains no tenant.

### The attack, step by step

1. Tenant A owns budget row `("user", 42, "day")` with a `usd_cap` of `$50`.
2. Tenant B's admin posts a budget for *their* user 42, same window.
3. The lookup, having no tenant predicate, finds **tenant A's row**.
4. The caps are overwritten with tenant B's values.
5. `existing.tenant_id = tenant_id` **re-stamps the row as tenant B's**.

Tenant A's spending limit is now gone, and the row no longer appears in tenant A's
listing. A silent takeover of another tenant's spend control, through an ordinary admin
endpoint.

### Why "the API layer authorises this" is not a defence

It does — but the data layer must not depend on it. Defence in depth means the layer that
performs the write refuses a write it can prove is wrong. An authorisation check one
layer up is one refactor away from not covering this path.

### Why the obvious fix is worse

The instinct is to add `Budget.tenant_id == tenant_id` to the lookup.

Now, when a conflicting row exists, the narrowed lookup finds **nothing** — so the
`existing is None` branch runs and **inserts a second row** for the same
`(scope_type, scope_id, window)`. The enforcement reader `_budgets_for`
(`enforcement.py:159`) then picks between duplicates arbitrarily.

You have converted a deterministic takeover into non-deterministic enforcement, which is
strictly harder to diagnose.

### The actual fix

`enforcement.py:669-687`. Keep the **full** natural-key lookup, then check ownership and
refuse:

```python
if (
    existing is not None
    and tenant_id is not None
    and existing.tenant_id is not None
    and existing.tenant_id != tenant_id
):
    raise CrossTenantBudgetError(...)
```

Only two **different, non-null** tenants collide. A platform-admin caller
(`tenant_id=None`) may write any row; an unowned row may be claimed.

And a matching subtlety at `:691-694`: a platform-admin write must **not** erase an
existing owner stamp, or the row is orphaned out of its tenant's listing.

`CrossTenantBudgetError` (`:530`) is re-exported through both shims and surfaced by the
API as **403**, not escaping as a 500.

The docstring at `:640-656` explains all of this in place — including why the lookup was
deliberately *not* narrowed.

### The related smaller one

`user_tenant_id` (`enforcement.py:498`) authorises a user-scoped budget write or a role
change by resolving the target user's tenant. It was **the only governed read in the
module that never called `set_tenant_scope`** — so on Postgres the RLS policy never
engaged for it. It now binds the scope like every sibling read (`:521`), and a caller
passing `tenant_scope` additionally gets the app-level check: a user outside that tenant
reads back as unknown (`:525-526`), which the API treats as a 404 rather than silently
allowing a cross-tenant write.

---

## Bug 4 — the usage ledger silently lost every row, and the USD caps stopped binding

This is the best story in the module because it is a **four-layer failure** where each
layer is individually defensible.

### The chain

**Layer 1.** `audio_seconds` and `images` were added to the `UsageLedger` model, so a
non-token call could ledger real spend. The `ALTER TABLE` needed for existing databases
was written **only in a docstring**.

**Layer 2.** There is no migration mechanism in this project — a deliberate no-Alembic
choice. `create_all` is `CREATE TABLE IF NOT EXISTS`: it materialises a new table and
never touches an existing one. So on any database created before that commit, the columns
did not exist.

**Layer 3.** Every `record_usage` INSERT names those columns. Every one raised
`UndefinedColumn`.

**Layer 4.** The gateway records usage **best-effort** — `_record_usage`
(`aegis/src/aegis/gateway/llm.py:771`) swallows every exception and logs at WARNING,
because a model call that already succeeded must not be failed by an accounting write.

### The result

Ledger rows vanished. Silently.

And the USD budget caps are computed by **summing the ledger** (`_usage_sums`,
`enforcement.py:212`). Zero rows means the sum stays flat, which means the cap never
binds. The system kept serving paid model calls with **no spend ceiling and no record of
what was spent**, and the only trace was a WARNING line in a log nobody reads.

Read that chain again and notice: every layer is individually correct. Best-effort
ledgering is right. `create_all` behaving as `IF NOT EXISTS` is right. Documenting a
migration is better than not documenting it. The failure is entirely in the **seam**.

### The fix

A new module — `aegis/src/aegis/governance/schema.py` —
`reconcile_additive_columns(conn, metadatas)` (`:174`), called from bootstrap right after
`create_all` (`backend/src/app/data/session.py:277`). Four properties, each deliberate:

- **Additive only.** Adds columns the metadata has and the database lacks. Never drops,
  renames, retypes or reorders — so it cannot destroy data and is safe on every boot.
- **Idempotent.** The plan comes from `information_schema`; a second run finds nothing.
  The DDL carries `IF NOT EXISTS`, so two processes racing at startup cannot collide.
- **Postgres-only.** SQLite returns immediately — the tests recreate their schema every
  run and have no drift.
- **Loud.** Every added column is logged at INFO. Anything that cannot be added safely
  raises `SchemaDriftError`.

The DDL is rendered by SQLAlchemy's own `CreateColumn` compiler (`_column_ddl`, `:129`)
rather than hand-written SQL, so an added column's type, nullability and server default
are **identical to what `create_all` would have produced** on a fresh database. That is
what makes "add it later" equivalent to "had it from the start".

### The design decision inside the fix: refusing to boot

`plan_additive_columns` (`schema.py:89`) splits drift into **addable** and **unsafe**. A
`NOT NULL` column with no server default is unsafe — there is no correct value for the
rows already present, and only a human can decide one.

The unsafe branch does not log-and-skip. It raises (`:203-219`), logged at CRITICAL as
well, because — as the comment says — a host that wraps its bootstrap in a broad "the
database is optional" handler would otherwise reduce it to a traceback nobody reads.

And that required a change in the host: `backend/src/app/main.py:153-160` catches
`SchemaDriftError` **before** the blanket handler at `:161` and re-raises it. The
lifespan docstring (`:136-144`) states the reasoning: *"Booting anyway would be the silent
failure this exception exists to prevent."*

**Refusing to serve is the correct outcome when the table that cannot be written is the
one a spend cap is computed from.** That is the sentence to have ready.

### Verified against a live database

Per the commit body: columns added, a second run a clean no-op, and a real `record_usage()`
insert confirmed. Not "the code looks right" — actually run.

---

## Bug 5 — two paid model calls with no governance context at all

### What was happening

`set_governance_context` was bound only on the query and voice routes. The
`/vision/analyse` route never bound it.

Both `enforce` and `record` are gated on the context existing — `_governed`
(`backend/src/app/core/llm.py:117`) returns `None` when no tenant is bound, and
`complete` (`aegis/src/aegis/gateway/llm.py:871-874`) skips enforcement *and* ledgering
entirely for a `None` context.

`/vision/analyse` makes **two** paid model calls: the prompt-injection screen and the
analyst call.

So both were: **uncapped** (no budget check), **unattributed** (no ledger row), and
**invisible** (nothing in the cost dashboard).

### Why this is a different bug from the others

The other four are bugs in the control. This one is a bug in the control's **coverage**.
The machinery worked perfectly; a route simply did not opt into it.

That is the failure mode of any opt-in control, and it is worth naming as a class: *a
control you must remember to attach is a control that will eventually not be attached.*

The structural answers are middleware (bind on every request) or making the context
mandatory at the chokepoint. Aegis fixed the route and added tests driving the **real**
route through the **real** governance hook to the **real** ledger — an integration test,
because a unit test of the hook would have kept passing throughout.

---

## Consistency and concurrency notes worth having ready

**Enforcement is not atomic with spend.** `enforce_governance` awaits a database read;
the provider call happens afterwards. Concurrent calls can each pass before any records
usage, so the cap can be overshot by up to the in-flight concurrency. Deliberate — see
[`10-theory.md`](10-theory.md#4-budgets-resolution-and-window-semantics). Say it plainly;
do not claim a hard cap.

**Ledger writes commit independently.** `record_usage` (`enforcement.py:296`) opens its
own short-lived session and commits. It is not in the request's transaction, which is
correct — a request rollback must not un-record real spend.

**The dashboard and the enforcer share one summation.** `budget_status`
(`dashboard.py:53`) runs the same ledger sum `enforce_governance` runs. Two independent
implementations of the same number will eventually disagree, and then nobody trusts
either.

**`_now_naive()`** (`enforcement.py:126`) exists because the ledger's `ts` is
`TIMESTAMP WITHOUT TIME ZONE` — naive UTC on both engines — so window comparisons use a
naive UTC bound to match. Mixing aware and naive datetimes across dialects is its own
family of bug; `backend/src/app/data/session.py::_align_timestamp_columns` is the
migration that converts legacy naive columns to `timestamptz`.

**The last-platform-admin guard.** `update_user_role` (`enforcement.py:549`) refuses to
demote the only `ADMIN` with no tenant (`:580-594`), because the platform would be left
with no global operator and no way back in. This is a *lockout* guard, not a security
control — but a system that can lock its operators out has an availability problem.

**Role rank fails closed.** `role_rank` (`config.py:84`) returns **0** for an unknown
fine tier — below every real tier — so an ordering comparison against an unrecognised
role never grants privilege.

---

## The invariants worth naming

1. **Two layers, always.** Application `WHERE tenant_id = ?` *and* a database policy.
   The first is the only one that works on SQLite; the second catches the query nobody
   reviewed.
2. **The GUC is transaction-scoped.** `is_local=true`, so no pooled connection carries a
   scope forward.
3. **`ENABLE` is not enough.** `FORCE` or a non-owning role.
4. **Cross-tenant writes are refused in the data layer**, not only authorised upstream.
5. **The schema is a control surface.** An unwritable ledger is an unbound cap, so the
   reconciler is fatal on drift it cannot fix.
6. **Absent credentials fail closed.** No password → `False`. Unknown role → rank 0.
   Unparseable token → rejected.

**Next:** [`40-diagrams.md`](40-diagrams.md).
