# Phase 1 — Tenant isolation

**3 days. Do this before anything else.**

There is a live cross-tenant leak in the retrieval path. We make tenant-isolation claims on
stage and in the risk map; today one of them is false.

---

## What is actually wrong

Three separate holes, verified in source.

### 1. The retrieval contract has no tenant

```python
# aegis/src/aegis/retrieval/pipeline.py:141
async def retrieve(self, query: str, *, persona: str | None = None) -> RetrievalResult:
```

There is no tenant parameter anywhere in the retrieval path. The agent *has* the tenant —
`graph.py:370` calls `deps.current_tenant_id()` and uses it to scope the **answer** cache —
but the two retrieval call sites don't pass it:

```python
# aegis/src/aegis/agent/graph.py:565, 570
result = await deps.retrieve(rewritten_query, persona=state.get("persona"))
result = await deps.retrieve(state["query"], persona=state.get("persona"))
```

So the isolation that exists one line above is dropped on the way into retrieval.

### 2. The retrieval cache key ignores the tenant

```python
# aegis/src/aegis/retrieval/cache.py:98
digest = hashlib.sha256(f"{persona or ''}\x00{_normalise(query)}".encode()).hexdigest()
```

**Two tenants, same persona, same question → the second one is served the first one's
cached result, including its retrieved passages.** This is the sharpest edge of the three:
it leaks document content, not just search behaviour.

The semantic (cosine) tier has the same problem — a near-match from another tenant is a hit.

### 3. The retriever is a process-wide singleton

```python
# backend/src/app/retrieval/pipeline.py:74
def _get_retriever() -> Retriever:
```

One instance, one LightRAG working directory, one Neo4j graph, for every tenant in the
process.

---

## What we are fixing now, and what waits

| | |
|---|---|
| **Now** | The contract carries tenant; the caches key on it; the backend filters on it; a live-Postgres test proves it. |
| **Now** | RLS policies extended to the remaining tenant-scoped tables. |
| **Waits** | Per-tenant LightRAG instances and per-tenant graph namespaces (Phase 4 of `plans/03`). |
| **Waits** | RLS failing closed on an *unset* scope — needs the `SECURITY DEFINER` login path. |

The split is deliberate: scoping the query, the cache and the filter closes the leak. Giving
every tenant its own graph store is a bigger change that we do not need before 30 August.

---

## Tasks

### 1.1 — Put tenant in the retrieval contract (0.75d)

Introduce a small value object rather than adding a bare `tenant_id: int | None` to eleven
signatures. Scope should be one thing you pass, not three things you remember.

```python
@dataclass(frozen=True, slots=True)
class RetrievalScope:
    tenant_id: int | None
    persona: str | None = None
```

- `aegis/src/aegis/retrieval/pipeline.py` — `retrieve(query, *, scope: RetrievalScope)`.
- `aegis/src/aegis/agent/deps.py` — update the `RetrieveFn` protocol.
- `aegis/src/aegis/agent/graph.py:565,570` — build the scope from `deps.current_tenant_id()`
  and the persona already in state.
- `backend/src/app/retrieval/pipeline.py:94` — pass the request's governance tenant through.
- `aegis/src/aegis/retrieval/stream.py:127`, evals and harness call sites — update.

**Make `scope` required, not optional with a `None` default.** A default is how this
happened. A required parameter makes the compiler find every call site for you.

### 1.2 — Key every cache on the tenant (0.5d)

- `aegis/src/aegis/retrieval/cache.py:98` — fold `tenant_id` into the exact-match digest.
- The semantic tier must **partition**, not just tag — a cosine search that can return
  another tenant's vector is still a leak even if you filter afterwards. Use a per-tenant
  namespace prefix so the search space itself is scoped.
- Check `_cache_scope` in `graph.py:363` — the answer cache already folds tenant + persona +
  role. Confirm it, and make the two caches consistent.

**Also fold in a corpus version.** Ingesting a document (Phase 3) must invalidate a tenant's
cached answers, or "upload a PDF then ask about it" returns the pre-upload answer. A
`corpus_version` counter per tenant in the key is cheaper and safer than eviction.

### 1.3 — Filter the backend by tenant (0.5d)

- Chroma: add the tenant to the `where` filter. Remember the earlier finding — **Chroma
  silently drops `None` metadata keys**, so a null tenant needs a sentinel, not `None`.
- LightRAG: pass the tenant as a filter on the returned rows until per-tenant instances land.
- Postgres-backed keyword recall: a `WHERE tenant_id = :t` on the same row.

### 1.4 — Prove it with a live database (0.75d)

This is the part that makes it real. The existing `test_rls.py` asserts DDL **strings**
against a hand-written fake engine — it has never talked to Postgres, so it cannot fail when
the policy is wrong.

Write one honest test against the local Postgres:

- Seed tenant A and tenant B, each with a distinct document containing a distinctive phrase.
- As tenant B, ask the question whose answer is only in tenant A's document.
- Assert the phrase does not appear in the result, in the citations, or in the cache entry.
- Run it **twice** — the second run is the one that catches a cache leak.

Then extend it: for every table with a `tenant_id` column, assert a bound scope cannot read
the other tenant's row. That test is what turns "we have RLS" into something you can say.

### 1.5b — Stop connecting as a superuser (0.5d) — **without this, 1.5 is decoration**

Verified on a scratch database, 2026-08-16:

```
superuser     scoped to tenant 1 sees 2 of 2 rows   ← policy bypassed
non-superuser scoped to tenant 1 sees 1 of 2 rows   ← policy enforced
```

`backend/.env` has `POSTGRES_DSN=postgresql://postgres:...`, and `postgres` is a superuser.
**Superusers skip row security entirely.** `FORCE ROW LEVEL SECURITY` removes the *owner*
exemption, not the superuser one — so every policy this platform installs is currently inert,
and has been since the day it was written.

The work:

- Create a non-superuser application role (`aegis_app`) that owns nothing and has
  `NOBYPASSRLS`, with only the DML grants it needs.
- Point `POSTGRES_DSN` at it. Update `.env.example`, the Windows install scripts and the
  runbook.
- Keep a separate owner/DDL connection for `create_all`, the RLS bootstrap and the schema
  reconciler — those legitimately need to bypass, and the split is what makes bypass a
  property of the *connection* rather than something application code can forge.
- Add a startup check that logs loudly if the serving role is a superuser or has
  `BYPASSRLS`. A control that is silently off is worse than one that is absent.

**This blocks 1.4.** An isolation test run as a superuser passes while proving nothing —
the same failure shape as asserting DDL strings against a fake engine.

### 1.5 — Extend RLS coverage (0.5d)

Today: `users`, `usage_ledger`, `approvals`. Roughly nine more tables carry `tenant_id` and
have no policy — `audit_log`, `budgets`, `chunks`, the six memory tables, the ops tables.

Add the same policy to each. This is mechanical; the test in 1.4 is what makes it trustworthy.

---

### 1.6 — Security tests run on real Postgres (1.5d)

**Production is already Postgres-only.** There is no SQLite import, no SQLite code path, and
`aiosqlite` is a **dev-extra** in both packages — it is never installed in production. So
there is nothing to remove from the runtime.

The problem is elsewhere, and it is worse than a fallback. Production carries eight guards
shaped like this:

```python
if bind.dialect.name != "postgresql":
    return        # set_tenant_scope silently does nothing
```

They exist so the SQLite test suite can run. The consequence is that **on SQLite, tenant
scoping is a no-op** — so every test that looked like it proved tenant isolation was only
ever exercising the app-level `WHERE` filter. That is exactly how ten tables sat with no RLS
policy without anyone noticing, and why `test_rls.py` could assert DDL strings against a fake
engine and still look green.

A test that cannot fail is worse than no test, because it stops anyone writing the real one.

**The work:**

- Move every test whose subject is isolation, tenancy, RLS or governed persistence onto real
  Postgres — `aegis/tests/governance/**`, the memory store tests, `aegis/tests/ops/conftest.py`,
  and any backend test asserting cross-tenant behaviour.
- Reuse the fixture pattern already proven in
  `backend/tests/integration/test_tenant_isolation_live.py`: a uuid-named scratch database, a
  `NOSUPERUSER NOBYPASSRLS` role, teardown in a `finally`, and a loud skip naming exactly what
  went unverified when no cluster is reachable.
- **SQLite is removed entirely — decided 2026-08-16.** Every one of the 34 test files moves to
  real Postgres. `aiosqlite` is deleted from the `dev` extra in both `aegis/pyproject.toml` and
  `backend/pyproject.toml`. No test may create a SQLite engine, in-memory or on disk.
- Where a test moves, delete the SQLite-shaped compromises it forced: fake engines,
  DDL-string assertions, `if dialect == ...` branches inside tests, and any assertion written
  around a behaviour SQLite could not express.
- `aegis/tests/ops/conftest.py` declares `FakeApproval.__tablename__ = "approvals"` on the
  shared metadata. On one shared Postgres database that **collides** with the real table. Fix
  it as part of the move.
- Speed matters at 34 files: use one template database created once per session and cloned
  per test, or a transactional rollback per test. Do not create a fresh schema per test.

**What the migration found immediately.** The first run of the migrated `aegis` suite failed
38 tests, and every single failure had one root cause — `ForeignKeyViolationError`, 186
occurrences. Example:

```
insert or update on table "memory_message"
violates foreign key constraint "memory_message_session_id_fkey"
```

**SQLite does not enforce foreign keys unless `PRAGMA foreign_keys=ON`.** So these tests had
been inserting orphaned rows for their entire life — a `memory_message` whose `session_id`
pointed at a `memory_session` that was never created — and passing every time. Postgres
refuses.

That is the migration paying for itself on day one, and it is the same lesson as the RLS gap:
a test running against an engine that cannot enforce the constraint is not testing the
constraint. The fix is to seed the parent rows the test data always implied — never to drop
the FK or defer it.

**Two consequences, stated honestly:**

1. **Nothing runs without a Postgres cluster** — including CI and a fresh clone. That is the
   deliberate trade: a test that cannot enforce what it claims is worse than one that will not
   start.
2. The eight production `if dialect.name != "postgresql": return` guards become unexercised
   once no test runs on another dialect. They are left in place for now because deleting them
   is a separate decision about whether `aegis` stays importable by a host without Postgres —
   it changes the library contract, not the test story. Flagged for after the hackathon.

## Definition of done

- [ ] `retrieve` cannot be called without a scope — it is a required parameter.
- [ ] Both cache tiers are partitioned by tenant, and a corpus version is in the key.
- [ ] Every table with a `tenant_id` column has an RLS policy.
- [ ] A live-Postgres test proves tenant B cannot read tenant A's document, through the
      pipeline, twice in a row.
- [ ] `pytest` green: 668 backend / 1217 aegis, plus the new tests.

## Demo at the end of this phase

Two tenants. Same question. Provably different answers — with the isolation test running in
front of them as the evidence. That is a stronger opening than any diagram.

## Risks

**The scope parameter will touch more call sites than expected** — evals, harness, stream and
the demo route all call `retrieve`. That is the point of making it required, but budget for
it.

**A tenant-partitioned cache has a colder start.** Rehearsed demo queries will miss the first
time per tenant. Warm the cache before presenting.
