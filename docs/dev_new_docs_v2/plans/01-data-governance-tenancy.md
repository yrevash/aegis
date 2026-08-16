# Plan 01 — Data, Governance, Tenancy, Identity

> **Scope of this document.** The data/persistence layer, Postgres posture, Row-Level
> Security, the tenant→role→user hierarchy, audit and reporting, schema migrations, and
> the two removals in this domain (ML out of the agent graph; the fake demo domain out of
> the adapter). The console, agent-graph, RAG, memory-UX and multi-agent work are planned
> in sibling documents; this one calls out only the seams those depend on.
>
> **Source of truth for intent:** `docs/dev_new_docs_v2/en_1_v2.0.md`.
> **Constraints:** `docs/hackathon/brief.md` — 16 GB Windows laptop, **no Docker**, native
> Postgres + Neo4j Desktop + Memurai, API-only models.
>
> **Sequencing directive (updated).** The finals are a *checkpoint*, not the optimisation
> target. This plan is sequenced by **architectural correctness and dependency order**.
> Each phase ends with an explicit "what is demoable at this boundary" note so a
> presentation can be given from any checkpoint.

---

## 0. Ground truth — what the code actually does today

Everything below was read in the source, not inferred from docs. Several items contradict
the framing in the v2 note and in this repo's own ADRs; correcting them changes the plan.

### 0.1 There is **no SQLite in production source. Anywhere.**

```
grep -rn "sqlite" backend/src aegis/src   →   0 matches
```

SQLite appears in exactly two places:

1. **The test suites** — `aiosqlite` engines in 5 conftests and 19 individual test files.
2. **The narrative** — docstrings, `docs/teaching/**`, `docs/architecture/memory-spec.md`,
   `docs/adr/0008`, and the "behaves identically on SQLite and Postgres" sentence the
   user quoted verbatim in the v2 note (it lives in
   `docs/teaching/memory/50-interview.md:131` and its HTML twin).

The `STORES=off` "lite" mode is **not** a SQLite fallback — it is a *no-database* mode
that skips every store (`backend/src/app/config.py:135`, `stores_enabled`). So the fix for
"no SQLite fallbacks" is two separable things: **(a) migrate the test suites**, and
**(b) delete the SQLite-parity claim from the story**. (b) is a day's work and removes the
exact sentence the user objected to. (a) is the real engineering.

### 0.2 The RLS hole is **much bigger than the fail-open predicate**

`aegis/src/aegis/governance/rls.py` installs `tenant_isolation` on exactly three tables:

```python
_RLS_TABLES = ("users", "usage_ledger", "approvals")
```

The database has **15 tables**, of which **13 carry a `tenant_id` column**:

| Table | `tenant_id`? | RLS policy? | Owner module |
|---|---|---|---|
| `tenants` | (is the tenant) | ❌ | `aegis/governance/models.py` |
| `users` | ✅ | ✅ | `aegis/governance/models.py` |
| `usage_ledger` | ✅ | ✅ | `aegis/governance/models.py` |
| `budgets` | ✅ | ❌ | `aegis/governance/models.py` |
| `audit_log` | ✅ | ❌ | `aegis/governance/models.py` |
| `approvals` | ✅ | ✅ | `backend/app/data/models.py` |
| `chunks` | ✅ | ❌ | `backend/app/data/models.py` |
| `memory_session` | ✅ | ❌ | `aegis/memory/stores.py` |
| `memory_message` | ✅ | ❌ | `aegis/memory/stores.py` |
| `memory_fact` | ✅ | ❌ | `aegis/memory/stores.py` |
| `memory_profile` | ✅ | ❌ | `aegis/memory/stores.py` |
| `memory_write_log` | ✅ | ❌ | `aegis/memory/stores.py` |
| `memory_consolidation_job` | ✅ | ❌ | `aegis/memory/stores.py` |
| `eval_results` | ✅ | ❌ | `aegis/ops/models.py` |
| `prompt_versions` | ✅ | ❌ | `aegis/ops/models.py` |

**10 of 13 tenant-scoped tables have no database-enforced isolation at all** — including
the entire memory subsystem, which is the thing the v2 note says must be "real … across
tenant and user, not some gimmick", and including `audit_log`, which the note wants
tenant-filterable. Fixing the predicate without fixing the table coverage fixes ~20 % of
the actual problem.

### 0.3 RLS has **zero live-database verification**

`aegis/tests/governance/test_rls.py` has 5 tests. Two assert that the helpers no-op on
SQLite. Three assert the *DDL strings* against a hand-written `_FakePostgresEngine` that
records SQL text and executes nothing. No test has ever run a policy against a real
Postgres.

`docs/adr/0008-multi-tenant-rls-governance.md` claims RLS is "validated by
`tests/integration/test_cross_tenant_isolation.py`". That file's own docstring says the
opposite: *"on the SQLite test database these tests exercise the belt-and-suspenders
app-level scoping."* The ADR asserts a control that no test exercises. Fix the ADR.

### 0.4 Free/paid tiers **do not exist** — item 4 is already done

Exhaustive grep across `backend/src`, `aegis/src`, `web/src`: there is no plan, no
subscription, no free/paid tier, no billing tier. The only `tier`-shaped things are:

- `RoleTier` — the RBAC ladder DTO (`aegis/governance/types.py`). Keep.
- `approval_default_tier = "tier-1"` — the approver escalation tier
  (`backend/app/config.py:80`). Keep; unrelated.
- `CustomerTier` in `backend/app/adapter/schema.py` — a *synthetic customer's* commercial
  tier inside the demo domain. Domain fiction; dies with the domain swap (Phase 7).

`Tenant` already carries only `{id, name, status, created_at}`, and budgets are already a
separate `budgets` table keyed on `(scope_type, scope_id, window)`. **Tenant + budget is
already the model.** Nothing to remove. Report this back rather than inventing work.

### 0.5 The demo domain is **not** a refund domain

`backend/src/app/adapter/` is a **service-request / case-management** world:
`ServiceRequest`, `SupportAgent`, `Customer`, `Document`, with three tools
(`update_request_status` HIGH, `assign_request` MEDIUM, `add_case_note` LOW). It is
already close to domain-neutral, and `SWAP.md` documents the retarget path.

"Refund" matches 100 files, but the distribution matters:

| Where | Count | What it actually is |
|---|---|---|
| `web/src/mock/**` + `web/src/config/personas.ts` | 8 files | Hard-coded mock fixtures — "$4,200 refund on account A-771", `policy-refund` graph nodes, `prior_refunds_90d` SHAP features |
| `docs/teaching/**`, `docs/learn/**` | ~25 files | Worked examples in prose |
| `aegis/tests/**`, `backend/tests/**` | ~45 files | String literals inside assertions |
| Real corpus/skill assets | 2 files | `adapter/corpus/kb_refund_process.md`, `adapter/skills/handling_refunds.md` (+ an aegis test copy) |
| Production Python | 3 files | `adapter/generator.py:548` (a category hint), `adapter/memory_spec.py:158-160` (skill routing), `api/schemas.py:1218` + `platform/risk_map.py:13` (a docstring analogy) |

So the "refund fiction" is overwhelmingly **web mock data and documentation prose**, not
architecture. The HITL gate itself is entirely domain-neutral already: it is driven by
`ToolSpec.risk`, not by any refund concept.

### 0.6 Admin CRUD already works end-to-end at the API — the gap is the **UI**

`POST /admin/tenants`, `POST /admin/users` (Argon2-hashed password, role, tenant),
`POST /admin/users/{id}/role` all exist in `backend/src/app/api/routes.py:1224-1354`, with
tenant-admin scoping, duplicate→409, last-platform-admin lockout protection, and audit
rows. `_authenticate` (routes.py:225) reads the `users` table first and the demo table
only in dev for usernames that have no real row.

**A user created through `POST /admin/users` really can log in today.** What is missing:
`web/src/lib/api/client.ts` has `getTenants`, `getUsers`, `getBudgets`, `upsertBudget`,
`setUserRole` — and **no `createUser` and no `createTenant`**. There is no form anywhere in
`web/src/components/admin/`. That is why it feels fake.

### 0.7 Approvals are owned by **either** admin tier — platform admin can decide tenant gates

`GET /approvals` and both decision endpoints use `require_admin` (routes.py:1116, 1160,
1190), which admits `platform_admin` and `tenant_admin`. `_scope_tenant` gives a
platform-admin **every tenant's** pending gates, and `_enforce_approval_tenant` explicitly
returns early for `PLATFORM_ADMIN`. This is precisely the conflation the v2 note objects
to.

### 0.8 No migration tool, no export, no sub-roles

- **Migrations:** none. `AegisBase.metadata.create_all` + `reconcile_additive_columns`
  (additive-only, Postgres-only, raises on anything non-additive) +
  `_align_timestamp_columns` (a one-off `timestamptz` retype). Alembic was declared and
  is now removed. This handles *added nullable columns* and nothing else — no renames,
  no type changes, no enum widening, no data backfill, no down-migrations.
- **Exports:** zero. No `csv`, no `Content-Disposition`, no report endpoint anywhere.
- **Sub-roles:** the `Role` enum is a closed 4-value `StrEnum`
  (`ADMIN`/`AI_TEAM`/`DEVOPS`/`CLIENT`) persisted as a **Postgres native enum**
  (`SAEnum(Role, name="user_role")`). The fine tier (`platform_admin`/`tenant_admin`) is
  *derived* from `role is ADMIN and tenant_id is None`, not stored. There is no
  tenant-defined role, no permission table, no grant model.

### 0.9 Test surface, measured

- Backend: **666** tests collected. Aegis: **633** tests collected. ≈1300 total.
- DB-touching entry points: **5 conftest `db` fixtures** + **19 test files** with inline
  `create_async_engine` = **24 files to change**, covering ~165 directly SQLite-bound
  tests plus everything downstream of `backend/tests/conftest.py`'s `db`/`client`
  fixtures (which the whole 169-test `backend/tests/api` suite rides on).
- The DB seam is already injectable everywhere: `configure_engine`,
  `configure_governance(session_factory=...)`, `configure_ops(session_factory=...)`,
  `configure_audit(session_factory=...)`. **This is the single biggest reason the
  migration is tractable** — no test reaches for a global engine directly.

---

## 1. The target model

### 1.1 Postgres roles — isolation by *connection identity*, not by a magic string

Three Postgres roles in one local cluster, no Docker, created by a setup script:

| Role | Attributes | Used by |
|---|---|---|
| `aegis_owner` | owns schema; **`BYPASSRLS`** | migrations, `bootstrap`, the login lookup function, the platform-admin engine |
| `aegis_app` | no `BYPASSRLS`; only `SELECT/INSERT/UPDATE/DELETE` grants | **every tenant-scoped request** |
| `aegis_migrate` | owns schema; **no** `LOGIN` for the app | (optional split of `aegis_owner`; see §6) |

The application runs **two engines**:

- `get_engine()` → `aegis_app`. Every request, every agent node, every memory read.
- `get_admin_engine()` → `aegis_owner`. Bootstrap, migrations, and the *explicitly
  enumerated* platform-admin cross-tenant read paths.

**Why this shape.** The alternative — a `app.bypass_tenant = 'on'` GUC — makes
cross-tenant access a *string an application bug can set*. A distinct Postgres role makes
it a property of the connection, which no application-layer mistake can forge. When a
jury asks "what stops a forgotten WHERE clause leaking data", the answer becomes: *"the
request connection is a Postgres role that physically cannot see another tenant's rows —
there is no flag to get wrong."* That is a materially stronger claim than today's, and it
costs one extra engine plus a routing decision at the FastAPI dependency.

### 1.2 The fail-closed predicate

Today (`rls.py:107-111`):

```sql
(substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL
 OR tenant_id = substring(current_setting('app.tenant_id', true) from '^[0-9]+$')::int)
```

Target — **delete the `IS NULL OR` branch**:

```sql
tenant_id = substring(current_setting('app.tenant_id', true) from '^[0-9]+$')::int
```

This is genuinely fail-closed and still **cannot raise**: an unset/empty/non-numeric GUC
makes `substring` yield SQL `NULL`; `NULL::int` is `NULL`; `tenant_id = NULL` is `NULL`,
which is not `TRUE`, so no row is visible. Keep the `substring` guard — a bare
`NULLIF(...)::int` errors on a non-numeric GUC and Postgres gives no evaluation-order
guarantee that an `OR` would shield it. Keep the policy without an explicit `WITH CHECK`
so the same predicate governs writes.

**Consequence to plan for:** rows with `tenant_id IS NULL` (platform-scoped records)
become invisible to `aegis_app` under *any* scope. That is correct — platform-scoped rows
belong on the `aegis_owner` connection — but it means the "platform user with
`tenant_id=null`" that `POST /admin/users` can create today needs an explicit read path.

### 1.3 The three holes the fail-open branch was covering, and how each closes

| Hole | Today | Target |
|---|---|---|
| **Login reads `users` by username before a tenant is known** | predicate fails open | `SECURITY DEFINER` function `aegis_auth_lookup(p_username text)` owned by `aegis_owner` (which has `BYPASSRLS`), returning exactly `(id, username, role, tenant_id, password_hash, is_active)` for one username. `_authenticate` calls it instead of `select(User)`. A single, auditable, minimal-surface hole instead of a table-wide one. |
| **Platform-admin listings span tenants** | predicate fails open | the platform-admin engine (`aegis_owner`, `BYPASSRLS`), reached only through `require_platform_admin`-guarded handlers. |
| **`tenant_id IS NULL` platform records** | invisible under scope, visible unscoped | served from the platform-admin engine; tenant-facing code never needs them. |

### 1.4 The tenancy / RBAC model

```
Aegis platform
 └── platform_admin ............ global operator; owns Aegis's own governance,
                                 forecasting, red-team, stack, and MCP surfaces.
                                 CANNOT decide a tenant's business approvals.
     └── Tenant (client)
          ├── tenant_owner ..... the "main client id". Exactly one per tenant,
          │                      created with the tenant. Appoints tenant_admins.
          ├── tenant_admin ..... adds users, assigns roles, defines sub-roles,
          │                      OWNS APPROVALS for this tenant, reads this
          │                      tenant's audit + budget.
          └── TenantRole ....... tenant-defined sub-role (name + permission set),
               └── User          created by platform_admin OR tenant_admin.
```

**Schema change** (the first change in this repo that `create_all` +
`reconcile_additive_columns` cannot handle, which is why Phase 1 is the migration tool):

```
tenant_roles(id, tenant_id → tenants.id, key, label, description,
             permissions jsonb, is_builtin bool, created_at)
             UNIQUE (tenant_id, key)

users  + tenant_role_id → tenant_roles.id  (nullable)
       + is_tenant_owner bool NOT NULL DEFAULT false
```

**Keep the coarse `Role` enum.** It stays the *portal selector* — which dashboard you get
(`admin` / `ai_team` / `devops` / `client`) — and it stays the thing every existing
`require_roles` guard reads. Do **not** widen the `user_role` Postgres enum; widening a
native enum is a migration with no clean rollback, and the sub-role requirement is not a
5th portal, it is *permissions within a tenant*. Layering `tenant_roles` on top is
additive, reversible, and lets a tenant define as many sub-roles as it likes without a
schema change per role. This is a genuine architectural fork; I am recommending
`tenant_roles` and stating why rather than listing options.

**Permission model.** `permissions` is a `jsonb` array of stable string keys
(`"approvals.decide"`, `"users.create"`, `"memory.upload"`, `"guardrails.edit"`,
`"budget.read"`, …), with the catalogue defined once in
`aegis/src/aegis/governance/permissions.py`. Two built-in, non-deletable rows per tenant
(`tenant_owner`, `tenant_admin`) are seeded at tenant creation. A new
`require_permission("approvals.decide")` FastAPI dependency resolves: coarse role →
built-in grant, else `tenant_role_id` → `permissions` array. `require_roles` stays for the
platform-side surfaces.

### 1.5 Approvals ownership

- `GET /approvals` and `POST /approvals/{id}/decision` and `POST /approval` move from
  `require_admin` to `require_permission("approvals.decide")`.
- `platform_admin` gets `approvals.decide` **only for gates whose `tenant_id IS NULL`**
  (Aegis's own internal actions). For tenant-owned gates it gets a read-only
  `approvals.observe` grant — visible in a platform oversight view, decision buttons
  disabled with an honest reason string.
- `_enforce_approval_tenant`'s `if auth.fine_role == PLATFORM_ADMIN: return` early-exit is
  deleted and replaced with the permission check.

This is a **user-visible behaviour change in the money-shot demo**: today the demo logs in
as `admin` and approves. After this, the demo must log in as the *tenant's* admin. Flag
this to whoever owns the demo script.

---

## 2. Phases

Effort is in **focused engineer-days**, one engineer with AI assistance, including tests
and docs. They are deliberately not optimistic; the largest single risk to this plan is
under-estimating Phase 2.

---

### Phase 0 — Truth-in-documentation (unblocks the story immediately)

**Goal.** Stop the repo asserting things the code does not do. This is prerequisite to
everything else because sibling audits keep finding docs that contradict source, and the
jury reads the docs.

**Work items**

1. Delete the "behaves identically on SQLite and Postgres" / "RLS is an additive belt"
   narrative — the exact text the user quoted. Sites:
   `docs/teaching/memory/50-interview.md:131-134` (+ `.html`),
   `docs/teaching/memory/10-guide.md:323` (+ `.html`),
   `docs/architecture/memory-spec.md:175`, `docs/learn/40-pipelines.md:430`,
   `aegis/src/aegis/memory/recall.py:9`.
2. Correct `docs/adr/0008` §Consequences: RLS is **not** validated by
   `test_cross_tenant_isolation.py`; that test exercises the app filter on SQLite.
3. Correct `aegis/governance/rls.py` and `backend/app/data/session.py:163-181` docstrings
   to state the *table coverage* honestly (3 of 13), not just the predicate semantics.
4. Write **ADR 0010 — Postgres-only, RLS fail-closed, role-separated connections**
   capturing §1.1–§1.3 as a decision with its trade-offs. This is the artifact that scores
   under "Production Roadmap".

**Files:** ~8 markdown + 2 docstrings. **Effort: 1 day.**

**Demoable at boundary:** the same system, but every claim in the docs survives being
checked against the code — which is exactly what the AI reader in the jury does.

---

### Phase 1 — A real migration story

**Goal.** Be able to make a non-additive schema change. Phase 3 (`tenant_roles`,
`users.tenant_role_id`) is blocked on this; so is every later phase that touches schema.

**Recommendation: bring Alembic back, properly this time.**

Justified, not defaulted. The alternatives were considered and rejected:

- *Keep `create_all` + `reconcile_additive_columns`.* It cannot add a table with a foreign
  key into an existing table, cannot backfill, cannot rename, cannot widen an enum, and
  raises `SchemaDriftError` on any NOT-NULL addition. Phase 3 needs three of those five.
- *Hand-written numbered SQL scripts + a `schema_version` table.* Cheaper to start
  (~0.5 day) but you rebuild autogenerate, ordering, and offline SQL by hand, and you get
  no `downgrade`. For a platform that pitches itself on production-readiness, "we wrote
  our own migration runner" is a weaker answer than "Alembic".
- *Alembic.* Pure Python, no server binary, no Docker, works identically on Windows.
  `alembic revision --autogenerate` reads the same declarative metadata this repo already
  maintains carefully. It was previously declared-but-unused, which is the bad state; the
  fix is to use it, not to avoid it.

**Work items**

1. `alembic.ini` + `backend/migrations/` at the *host*, not in the `aegis` package —
   `aegis` is an importable library and must not own a host's migration tree. `env.py`
   registers **all four** metadata objects (`aegis.data.AegisBase.metadata`,
   `backend.app.data.models.Base.metadata`, and the memory/ops tables that register on
   `AegisBase`), and runs as `aegis_owner`.
2. **Baseline revision** = today's schema exactly, so an existing dev database can be
   `alembic stamp head`ed without a rebuild.
3. Fold the two bespoke reconcilers in as first-class migrations and then **retire them**:
   `reconcile_additive_columns` and `_align_timestamp_columns` become historical
   revisions. `bootstrap()` stops mutating schema and becomes: connect, verify
   `alembic_version == head`, refuse to serve on mismatch. Keep `SchemaDriftError` as the
   exception type so host catch-sites are unchanged.
4. `scripts/db-upgrade.ps1` / `.sh` wrapping `alembic upgrade head`, wired into
   `scripts/start.ps1` and `preflight.ps1`.
5. Keep `plan_additive_columns` and its 11 tests — they become a *drift detector* the
   preflight runs, not a mutator.

**Files:** new `backend/migrations/**`, `backend/alembic.ini`,
`backend/src/app/data/session.py` (bootstrap rewrite), `aegis/src/aegis/governance/schema.py`
(demoted to detector), `backend/pyproject.toml` (+`alembic>=1.13`), 2 scripts.
**Effort: 2.5 days** (0.5 setup, 1 baseline + verifying it stamps a live DB cleanly,
0.5 bootstrap rewrite, 0.5 scripts/preflight/docs).

**Demoable at boundary:** `alembic upgrade head` on a fresh Windows box and on a
long-lived dev database; `alembic history` as a production-readiness artifact.

---

### Phase 2 — Postgres everywhere, including the test suite

**Goal.** Delete `aiosqlite`. Every test that touches a database touches **Postgres**.

This is the largest phase and the one most likely to overrun. Plan it as three
sub-phases so it can land incrementally and never leaves the suite red overnight.

#### 2a — The test-database harness (no new heavy dependency)

**Recommendation: do NOT use `pytest-postgresql`.** Reasons, in order of weight:

1. It is built around **psycopg (sync)**. This stack is `asyncpg` + SQLAlchemy async
   throughout. You would be installing a second driver, and on Windows `psycopg2` means a
   binary wheel or a build toolchain — exactly the class of dependency the brief forbids.
2. Its `postgresql_proc` fixture **spawns its own cluster** via `initdb`/`pg_ctl`. Those
   binaries exist under a native Windows install, but they are not on `PATH` by default,
   and the fixture's temp-dir/permissions behaviour on Windows is a known source of
   flakiness. Adding a flaky test-infra dependency to fix a correctness problem is a bad
   trade.
3. The genuinely useful part — template-database cloning and a janitor — is **~60 lines**
   written directly against `asyncpg`, which is already a dependency.

**Build instead:** a shared `tests/_pg.py` in each of `aegis/` and `backend/`, exporting
one fixture set. Two isolation strategies, chosen per test:

- **Default: transactional rollback.** Session-scoped: one engine against
  `AEGIS_TEST_DSN` (default `postgresql://aegis_test:...@localhost:5432/aegis_test`);
  create the schema once via `alembic upgrade head`. Function-scoped: open a connection,
  `begin()` an outer transaction, build an `async_sessionmaker(bind=connection,
  join_transaction_mode="create_savepoint")`, inject it through the existing
  `configure_governance` / `configure_ops` / `configure_audit` / `configure_engine`
  seams, yield, then `rollback()`. Cost per test: **single-digit milliseconds**.
  The injection seams already exist and are used by every current conftest — this is why
  the swap is mostly mechanical.
- **Escape hatch: template-database clone.** A `@pytest.mark.pg_fresh` marker gets its own
  database created `TEMPLATE aegis_test_template`. Needed for tests that run DDL, that
  assert on `alembic` itself, or that need real `COMMIT` semantics (the schema-reconcile
  tests, `test_schema_reconcile_bootstrap.py`, and — critically — every RLS test, because
  `set_config(..., is_local => true)` interacts with savepoints). Cost: ~100-200 ms each;
  keep this set under ~30 tests.

**Windows/no-Docker mechanics** (all in `scripts/`):
- `scripts/test-db-setup.ps1`: `CREATE ROLE aegis_test LOGIN …; CREATE DATABASE aegis_test
  OWNER aegis_test; CREATE ROLE aegis_test_app …` — run once per machine, idempotent.
- `preflight.ps1` gains a "test database" row alongside the existing Postgres/Neo4j/Redis
  rows.
- CI/local parity: a single `AEGIS_TEST_DSN` env var; if unset, the DB-touching suites
  **skip with a loud reason** rather than silently falling back. No fallback is the point.

**Effort: 2 days.**

#### 2b — Migrate the 24 entry points

Ordered by how much real semantics they carry, so the highest-value suites move first:

| Order | Target | Tests | Notes |
|---|---|---|---|
| 1 | `aegis/tests/governance/conftest.py` + 4 files | 77 | The tenancy semantics themselves. Native `user_role` / `budget_scope` / `budget_window` enums become real Postgres enums here for the first time in a test. |
| 2 | `aegis/tests/memory/conftest.py` + `backend/tests/memory/*` (8 files) | 128 | `JsonB` becomes real `jsonb`; `Index(..., unique=True)` on `(subject_id, tenant_id)` gets real NULL semantics — **expect failures here**, see §4. |
| 3 | `aegis/tests/ops/conftest.py` + `backend/tests/ops/*` (6 files) | 120 | `FakeApproval` in the ops conftest declares `__tablename__ = "approvals"` on the shared metadata — on Postgres this will **collide** with the host's real `approvals` table if both metadatas are ever created in one database. Must be resolved (rename to `approvals_test` + inject via `approval_model=`, which `configure_ops` already supports). |
| 4 | `backend/tests/conftest.py` (`db` + `client` + `admin_headers`) | ~169 downstream | The whole API suite rides this. Do it after 1-3 so the failure modes are already understood. |
| 5 | `backend/tests/data/*`, `backend/tests/core/*`, `aegis/tests/agent/test_memory_wiring.py`, `aegis/tests/data/test_vector_column.py` | ~60 | Long tail. |

Then: delete `aiosqlite` from `aegis/pyproject.toml:dev` and
`backend/pyproject.toml:dev`; add `greenlet` to `aegis`'s dev extra (backend already has
it; `aegis` will need it once its tests use a real async driver under `run_sync`).

**Effort: 4 days.** This is the number most at risk. It assumes ~1 day of the four is
spent on genuine SQLite-vs-Postgres behaviour differences that were never exercised
(§4.1).

#### 2c — Delete the SQLite parity claim from the code

`RlsConfig` in `aegis/governance/types.py` and `config.py:133` (`fail_closed=False` →
`True` once Phase 4 lands), the "a documented no-op on SQLite" field descriptions, the
`if bind.dialect.name != "postgresql": return` guards in `rls.py` (keep the guard — it is
still correct defensive code — but stop describing it as "so the tests run"), and the
`test_*_is_a_noop_on_sqlite` tests, which are deleted outright.

**Effort: 0.5 day.**

**Phase 2 total: 6.5 days.**

**Demoable at boundary:** `uv run pytest` against a live local Postgres, ~1300 green, and
the honest sentence *"there is no SQLite anywhere in this project — the tests run against
the same database the product runs on."* This is a strong, checkable claim.

---

### Phase 3 — The tenant hierarchy and sub-roles

**Depends on:** Phase 1 (migrations). **Should follow:** Phase 2 (so the new tables are
tested on the database that will enforce them).

**Work items**

1. **Migration** `tenant_roles` + `users.tenant_role_id` + `users.is_tenant_owner`
   (§1.4). Seed `tenant_owner` / `tenant_admin` built-ins inside the same revision.
2. `aegis/src/aegis/governance/permissions.py` — the permission-key catalogue, the
   `Permission` StrEnum, `permissions_for(coarse_role, fine_role, tenant_role)`, and
   `has_permission(...)`. Pure, dependency-free, unit-testable without a DB.
3. `aegis/governance/models.py` — `TenantRole` ORM; `User.tenant_role_id`,
   `User.is_tenant_owner`.
4. `aegis/governance/types.py` — `TenantRoleRow` DTO; `AdminUserRow` gains
   `tenant_role` + `is_tenant_owner`.
5. `aegis/governance/enforcement.py` — `list_tenant_roles`, `create_tenant_role`,
   `update_tenant_role`, `delete_tenant_role` (refuse on built-ins and on last owner),
   `assign_tenant_role`. `create_tenant(name)` grows an `owner_username`/`owner_password`
   pair so a tenant is never created without its main client id.
6. **API** (`backend/src/app/api/routes.py`, `schemas.py`):
   - `GET/POST /admin/tenants/{id}/roles`, `PATCH/DELETE /admin/tenants/{id}/roles/{key}`
     — `require_permission("roles.manage")`, held by `platform_admin` and `tenant_admin`
     (satisfies "aegis admin and tenant admin can make more sub roles under the tenant").
   - `POST /admin/users` gains `tenant_role` and enforces that a `tenant_admin` may only
     assign roles inside its own tenant.
   - `require_permission(...)` dependency factory replaces the ad-hoc guards on the
     tenant-owned surfaces. `require_roles` stays for platform surfaces.
7. **Approvals re-owning** (§1.5) — the `require_admin` → `require_permission` swap and
   the deletion of the `PLATFORM_ADMIN` early-exit in `_enforce_approval_tenant`.
8. **Web client** — add the missing `createUser` / `createTenant` / tenant-role calls to
   `web/src/lib/api/client.ts`. *(The forms themselves belong to the console plan; this
   phase owns the transport and types only, so the console team is unblocked.)*

**Files:** 1 migration, 3 new `aegis/governance` modules, 3 edited,
`backend/src/app/api/routes.py` + `schemas.py`, `web/src/lib/api/{client,types}.ts`,
~8 new test files.
**Effort: 4 days.**

**Demoable at boundary:** platform admin creates *Acme Corp* with an owner login; the owner
logs in for real, defines a `Claims Reviewer` sub-role, adds a user to it; that user logs
in and sees only what the sub-role permits; a HIGH-risk gate raised in Acme's tenant
appears in Acme's admin inbox and is **not decidable** by the Aegis platform admin. That
is the single most jury-legible thing in this entire plan.

---

### Phase 4 — RLS that fails closed, on every tenant-scoped table

**Depends on:** Phase 2 (you cannot verify RLS without Postgres tests) and Phase 3 (the
platform-admin read paths must be enumerated before you can cut the fail-open branch).

**Work items**

1. `scripts/db-roles.ps1` / `.sh` + a migration creating `aegis_app`, granting it exactly
   `SELECT/INSERT/UPDATE/DELETE` on the 13 tenant-scoped tables and nothing else.
   `aegis_owner` keeps `BYPASSRLS`.
2. `backend/src/app/config.py`: `postgres_dsn` (app role) + `postgres_admin_dsn` (owner).
   `backend/src/app/data/session.py`: `get_admin_engine()` / `get_admin_sessionmaker()`
   alongside the existing pair; `bootstrap`/migrations move to the admin engine.
3. `aegis/governance/rls.py`:
   - `_RLS_TABLES` grows from 3 to **13** (§0.2). The memory and ops tables are declared
     in `aegis.memory.stores` and `aegis.ops.models`, so the list must be assembled from
     the metadata (`every table with a tenant_id column`) rather than hand-maintained —
     otherwise the next table added silently has no policy. Make that assembly the
     implementation and keep an explicit expected-set assertion in a test so an
     unexpected table cannot appear unnoticed either.
   - `_TENANT_ISOLATION_PREDICATE` loses the `IS NULL OR` branch (§1.2).
   - `bootstrap_rls` additionally `REVOKE`s and re-`GRANT`s so the policy set and the
     grants land together.
4. `aegis_auth_lookup(p_username text)` `SECURITY DEFINER` function (§1.3), installed by
   migration, `REVOKE ALL … FROM PUBLIC; GRANT EXECUTE … TO aegis_app`.
   `_authenticate` in `routes.py:225` calls it.
5. **Route the platform-admin reads.** Enumerate every handler that legitimately spans
   tenants — `admin_tenants`, `admin_users(tenant_id=None)`, `admin_budgets`,
   `admin_usage`, `audit_log(tenant_id=None)`, the platform forecast surfaces — and bind
   them to the admin sessionmaker behind `require_platform_admin`. **Every other
   handler keeps the app engine.** Write this list into ADR 0010; it is the security
   boundary and it must be reviewable on one page.
6. `RlsConfig.fail_closed = True`, `tables` reflects the real 13.
7. **The tests that make this real** — a new `pg_fresh`-marked suite, ~20 tests:
   - for each of the 13 tables: bound scope A cannot see a row of tenant B (SELECT);
   - bound scope A cannot INSERT or UPDATE a row *into* tenant B (the implicit
     `WITH CHECK`);
   - **unset scope sees zero rows** — the fail-closed assertion, which is the whole point
     and which cannot exist today;
   - `aegis_app` cannot bypass; `aegis_owner` can;
   - login succeeds under `aegis_app` via the definer function with no scope bound;
   - a platform-admin listing spans tenants on the admin engine and does not on the app
     engine.
   Delete the three fake-engine DDL-string tests; they are superseded.

**Files:** `aegis/governance/rls.py`, `config.py`, `types.py`,
`backend/src/app/data/session.py`, `backend/src/app/config.py`,
`backend/src/app/api/routes.py`, 1 migration, 2 scripts, 1 new test suite.
**Effort: 4 days** (1 of which is the enumerate-and-route work in item 5 — it is
tedious and it is where a mistake becomes a 500 in the demo).

**Demoable at boundary:** open two `psql` windows as `aegis_app`; in one,
`SELECT set_config('app.tenant_id','1',false); SELECT * FROM memory_fact;` — tenant 1's
rows. In the other, no scope — **zero rows**, on every table. That is a live demonstration
of a security control, not a slide.

---

### Phase 5 — Audit, filters, and downloadable reports

**Depends on:** Phase 3 (permissions decide who may export) and Phase 4 (RLS decides what
the export can contain — an export written before RLS closes is an export that can leak).

**Work items**

1. `aegis/governance/audit.py` — `list_audit(...)` replacing `list_recent_audit`, with
   `tenant_id`, `actor`, `action_prefix`, `model`, `approved_by`, `since`/`until`,
   `has_approval`, and keyset pagination (`before_id`) rather than a bare `limit`. Keep
   `list_recent_audit` as a thin shim so nothing breaks mid-migration.
2. `AuditLog` gains an index on `(tenant_id, ts DESC)` — the filter's driving predicate;
   today only `ts` and `action` are indexed. Migration.
3. `GET /audit` grows the filter query params; `_scope_tenant` still pins a tenant-admin
   to its own tenant (satisfies "a tenant can view audit for its own users").
4. **A single report module**, `backend/src/app/platform/reports.py`, generating **CSV**
   (streaming, `StreamingResponse` + `Content-Disposition`) for four reports:
   - `GET /reports/audit.csv` — filtered audit trail
   - `GET /reports/tenant.csv` — tenant roster: users, roles, sub-roles, last login
   - `GET /reports/budget.csv` — caps vs consumption, from the existing
     `BudgetStatusRow` (`aegis/governance/dashboard.py`) so the report and the enforcer
     cannot disagree
   - `GET /reports/usage.csv` — the ledger rollup
   *(The forecast report the v2 note also asks for belongs to the forecast/ML plan; it
   should reuse this module's CSV writer and `Content-Disposition` helper.)*
   **Recommendation: CSV, not PDF.** PDF needs a rendering dependency (WeasyPrint pulls
   GTK on Windows; ReportLab is pure-Python but you are then hand-laying-out documents).
   CSV opens in Excel, which is what an enterprise buyer actually does with a compliance
   export. If a branded PDF is wanted later, generate it in the browser from the same
   data — no server dependency.
5. Every export writes its own `audit_log` row (`report.export`) with the filter
   parameters in the payload. An export of the audit trail that is not itself audited is
   a hole a procurement reviewer will find.

**Files:** `aegis/governance/audit.py`, `aegis/governance/types.py`, 1 migration,
`backend/src/app/platform/reports.py` (new), `backend/src/app/api/routes.py`,
`web/src/lib/api/client.ts`, ~4 test files.
**Effort: 2.5 days.**

**Demoable at boundary:** a tenant admin filters their audit page to `approval.decision`
in the last 7 days and downloads a CSV containing only their tenant's rows — with the
download itself appearing as the newest audit row.

---

### Phase 6 — Remove ML from the agent graph

**Independent of Phases 1-5.** Can be done at any point; it is small and it unblocks the
agent-graph plan, so schedule it early — it is the cheapest win in this document.

**What comes out (measured, not estimated):**

| Site | Change |
|---|---|
| `aegis/src/aegis/agent/graph.py:713-732` | delete the `ml_predict` node body |
| `graph.py:1151-1153`, `1195-1196` | delete `add_node("ml_predict", …)`; rewire `retrieve → plan` (currently `retrieve → ml_predict → plan`) |
| `graph.py:93` | drop `"ml_predict"` from `NODE_LABELS` |
| `graph.py:780-782`, `985-986` | delete the `ml_summary` injection into the planner prompt and the answer |
| `graph.py:835-850` | delete the `ml_explanation` event emission |
| `aegis/agent/deps.py:231,236-237` | remove `predict_explain`, `features_for`, `describe_prediction` from `AgentDeps` |
| `aegis/agent/deps.py:104,125,153` | remove `AgentConfig.run_ml` |
| `aegis/agent/state.py:131-132` | remove `ml_response`, `ml_summary` from `AgentState` |
| `aegis/agent/events.py:124-140` | delete the `ml_explanation` builder |
| `aegis/agent/harness.py:63,305` | drop `run_ml` from the config surface and the `ml_explanation` lookup |
| `aegis/agent/topology.py:87,92-93` | drop the three `_unreachable` deps |
| `backend/app/agent/deps.py:323,343,348-349,453-457,521-534` | remove the wiring + the two `_default_*` helpers |
| `backend/app/agent/events.py:27,52` | drop the re-export |
| `backend/app/api/schemas.py:245` | remove the `ml_explanation` event variant from the SSE union |
| `web/src/lib/stream.ts:182`, `state/runReducer.ts:275`, `config/signals.ts:59`, `components/trace/describeEvent.tsx:107`, `mock/mockTransport.ts` (×3), `mock/platform.ts:193` | remove the event from the console's run timeline |
| `backend/tests/integration/test_ml_nonblocking.py` | delete |

**What stays — and this is the important half.** The v2 note wants SHAP *and* interactive
feature engineering in the **admin forecast dashboard**. So:

- `aegis/ml/**` and `backend/src/app/ml/**` stay entirely, unchanged.
- `POST /ml/explain` stays as a **standalone surface**, reachable from the MLOps and
  Forecast pages, not from the agent.
- `backend/app/adapter/ml_spec.py` stays — but is re-framed in `SWAP.md` as *"the tenant's
  ML use-case spec"*, not *"the agent's predict step"*. Its "ML reshape points" section is
  rewritten accordingly (it currently documents `run_ml` and "when ML runs" in the agent,
  which will be false).
- The trust-stack narrative in `docs/hackathon/brief.md` §4 says *conformal → human gate →
  SHAP → guardrails → traces*, implying ML feeds the gate. **It never did** — the gate is
  driven by `ToolSpec.risk` alone (`adapter/tools.py:405,433`), and `graph.py:835`'s own
  docstring says the ML event carries "no gating semantics". Removing ML from the graph
  therefore **breaks no real behaviour** — but the pitch sentence must be rewritten, or
  the jury will ask a question you cannot answer. Move conformal prediction's role in the
  narrative to the forecast dashboard, where it genuinely lives
  (`statsforecast` + `ConformalIntervals`).

**Effort: 1.5 days** (0.5 core, 0.5 web + tests, 0.5 the narrative rewrite, which is the
part people skip and then regret).

**Demoable at boundary:** a run trace with no dead ML step in it, and a *separate*
forecast page where SHAP and feature selection are the point rather than a decoration.

---

### Phase 7 — A genuinely domain-neutral adapter seam

**Independent of Phases 1-5.** Do it after Phase 6.

**The correction that matters.** There is no "refund domain" to delete (§0.5). The adapter
is already a neutral case-management world. What actually needs doing is different, and
more valuable:

1. **Delete the hard-coded fiction from the console.** `web/src/mock/**` (8 files) and
   `web/src/config/personas.ts:45` embed a specific refund story into the *product's UI*,
   not into the adapter. On the day, those strings are what a judge sees if any surface
   falls back to mock. Replace the mock corpus with either (a) neutral placeholders that
   are visibly labelled as samples, or (b) — better — generate the mock fixtures from
   `adapter/generator.py`'s output at build time, so the mock data *is* the domain data
   and retargets automatically. **Recommend (b)**; it converts a maintenance liability
   into a swap-time freebie.
2. **Retire the two refund assets** — `adapter/corpus/kb_refund_process.md`,
   `adapter/skills/handling_refunds.md`, and the `"refund"/"billing"/"charge" →
   handling_refunds` routing table at `adapter/memory_spec.py:158-160`. Replace with
   domain-neutral equivalents from the case-management world already in `schema.py`.
3. **Harden the seam itself.** Today `SWAP.md` names six files but the seam is enforced
   only by convention. Make it structural:
   - a `DomainAdapter` Protocol in `aegis/` (the library) that a host's adapter package
     satisfies — `schema`, `tools`, `personas`, `prompts`, `corpus`, `generator`,
     `memory_spec`;
   - an `AEGIS_ADAPTER` setting naming an importable module, resolved once at startup, so
     a second adapter can live beside the first and be switched by env var rather than by
     editing files;
   - a `tests/adapter/test_contract.py` that runs the *same* conformance suite against
     any registered adapter — round-trip a generated dataset, every tool typed and
     audited, every persona's allowlist non-empty, `feature_matrix` yields trainable
     `(X, y)`.
   This is the change that makes "retargetable in ~2 hours" a demonstrable claim: you ship
   **two** adapters (the case-management one and a deliberately unrelated second one) and
   switch between them live on stage.
4. **The human gate stays exactly where it is.** It is already domain-neutral:
   `RiskLevel` on a `ToolSpec` → `gate` node → durable `approvals` row → inbox. Nothing
   about it references any domain. Do not touch it; only its *ownership* changes, in
   Phase 3.

**Files:** `web/src/mock/**` (8), `web/src/config/personas.ts`,
`backend/src/app/adapter/{memory_spec,corpus,skills}`, new `aegis/src/aegis/adapter/protocol.py`,
`backend/src/app/config.py`, `backend/tests/adapter/test_contract.py`, `SWAP.md`.
**Effort: 3 days** (1 for the mock-generation pipeline, 1.5 for the protocol + conformance
suite, 0.5 for asset replacement).

**Demoable at boundary:** `AEGIS_ADAPTER=app.adapter_alt` restarts into a completely
different domain with the same console, same governance, same gate — the "weapon, not
solution" thesis proven rather than asserted.

---

### Phase 8 — Post-foundation hardening (the things worth doing once the base is right)

Grouped here because each is small and none blocks anything.

1. **Session/refresh tokens.** JWTs are 720-minute bearers with **no revocation**
   (`security.py:79`). Deactivating a user or demoting them does not invalidate a live
   token — a 12-hour window in which a removed user keeps their access. Add a `jti` claim
   + a `revoked_tokens` table, or short access tokens + a refresh endpoint. *1.5 days.*
2. **Password policy + rotation.** `create_user` accepts any string. No minimum length, no
   forced first-login change, no reset flow. A tenant admin cannot reset a user's
   password. *1 day.*
3. **Per-tenant model defaults.** The v2 note asks for model selection "for every tenant
   and profile user preference". That is a `tenant_settings` / `user_preferences` table
   and it is *data/tenancy* work even though the UI belongs to the console plan. Land the
   tables and the API here so the console team is unblocked. *1.5 days.*
4. **`tenants.created_by` and a soft-delete/`suspended` path.** `TenantStatus.SUSPENDED`
   exists in the enum and is enforced **nowhere** — a suspended tenant's users log in and
   spend normally. Either enforce it in `_authenticate` and `enforce_governance`, or
   delete the enum value. Recommend enforcing. *0.5 day.*
5. **Audit retention + partitioning story.** For "Production Roadmap" scoring: a documented
   retention policy and a monthly partition on `audit_log`. Even a documented plan with
   one implemented partition scores. *1 day.*

**Effort: 5.5 days.**

---

## 3. Dependency order

```
Phase 0 (docs truth) ──────────────────────────────────► can run in parallel with anything

Phase 1 (Alembic) ──┬──► Phase 3 (tenant_roles, sub-roles, approvals ownership)
                    │         │
Phase 2 (Postgres   │         ├──► Phase 5 (audit filters + CSV reports)
        everywhere) ┴──► Phase 4 (RLS fail-closed, 13 tables)
                              │
                              └──► Phase 8 (hardening)

Phase 6 (ML out of graph) ─── independent ─── blocks the AGENT-GRAPH plan
Phase 7 (adapter seam)    ─── independent ─── blocks the DEMO-SCRIPT rewrite
```

**Hard ordering constraints, and why:**

- **Phase 1 before Phase 3.** `tenant_roles` needs a table with an FK plus a NOT-NULL
  defaulted column on an existing table. `reconcile_additive_columns` refuses the second
  and cannot do the first (`plan_additive_columns` skips tables that do not exist and
  `SchemaDriftError`s on NOT-NULL-without-default).
- **Phase 2 before Phase 4.** Cutting the fail-open branch with no Postgres tests means
  discovering the breakage in the browser. Phase 4's value *is* its test suite.
- **Phase 3 before Phase 4.** You cannot decide which handlers get the `BYPASSRLS` engine
  until you have decided who is allowed to span tenants, and that is Phase 3's permission
  model.
- **Phase 4 before Phase 5.** An export endpoint written while RLS still fails open is an
  export endpoint that will leak the first time someone forgets a `WHERE`.

**What the sibling plans need from this one:**

| Sibling work | Blocked on | Why |
|---|---|---|
| Console: admin add-user / add-tenant forms | Phase 3 item 8 | the API client functions do not exist yet |
| Console: tenant-scoped approvals inbox | Phase 3 item 7 | the ownership semantics change |
| Console: audit page filters | Phase 5 items 1-3 | the query params do not exist |
| Agent graph: node topology, trace timeline | **Phase 6** | `ml_predict` must be gone before the topology is re-drawn, or it gets re-drawn twice |
| Memory UX: per-tenant upload/delete/inspect | Phase 4 item 3 | the memory tables have no RLS today; building tenant-facing memory management on top of that is building on sand |
| Chat sessions in Postgres | Phase 1 | new tables → needs the migration tool |
| Multi-agent / skills | Phase 3 | "users should have the option to add skills" is a per-tenant permission |

---

## 4. Risk register

### 4.1 SQLite→Postgres will surface behaviour that was never exercised — **High likelihood, medium impact**

Concrete, named risks found while reading the code:

- **Native enums.** `SAEnum(Role, name="user_role")`, `budget_scope`, `budget_window`,
  `tenant_status`, `approval_status`, `approval_risk` become real Postgres types. SQLite
  stores them as VARCHAR with a CHECK. Any test inserting a raw string that is not an
  exact enum label passes today and fails on Postgres. `models.py:104-113` already
  documents that a live database may carry a legacy `'user'` label in `user_role`.
- **The `approvals` table-name collision.** `aegis/tests/ops/conftest.py` declares
  `FakeApproval.__tablename__ = "approvals"` on the *shared* `AegisBase` metadata, while
  `backend/app/data/models.py` declares the real `Approval` on its own `Base`. On SQLite,
  separate per-test files hide this. On one shared Postgres test database, whichever
  metadata is created second either collides or silently binds to the wrong shape.
  **Must be fixed in Phase 2b step 3, not discovered in step 4.**
- **`Index("ux_memory_profile_subject", "subject_id", "tenant_id", unique=True)`**
  (`memory/stores.py:174`). In Postgres, `NULL != NULL` in a unique index, so *many* rows
  with `tenant_id IS NULL` and the same `subject_id` are permitted. SQLite behaves the
  same way here, so this is not new — but the memory suite's NULL-tenant tests have never
  run against a real `jsonb` + real index, and the "null-tenant scope" concept
  (`vector_ops.py:14`) is load-bearing.
- **`func.now()` and `UtcDateTime`.** Postgres returns tz-aware; SQLite returns naive.
  `_iso_utc` in both `audit.py` and `enforcement.py` defensively handles naive input —
  which means any test asserting a naive timestamp will now see aware.
- **Integrity-error classes.** `DuplicateTenantError`/`DuplicateUserError` are raised from
  caught `IntegrityError`s; asyncpg's exception hierarchy differs from aiosqlite's.
- **Transaction semantics.** Application code calls `session.commit()` inside its own
  short-lived sessions (`record_audit`, `record_usage`). Under the rollback-per-test
  strategy those become savepoint releases. Any test asserting "the row survived a commit"
  needs `pg_fresh`.

**Mitigation:** sub-phase 2b's ordering exists for this — move the *semantically dense*
suites (governance, memory, ops) first and in isolation, so each class of difference is
met once, in a small suite, with the cause obvious. Budget the full extra day. Do not
migrate `backend/tests/conftest.py` (169 downstream tests) until 1-3 are green.

### 4.2 Fail-closed RLS breaks a path nobody enumerated — **Medium likelihood, high impact**

The fail-open branch is load-bearing in ways the code comments only partially capture.
Every read on the `aegis_app` connection that today runs without binding a scope returns
**zero rows** after Phase 4 — silently, with no error. Known unscoped readers:
`list_recent_audit` (never calls `set_tenant_scope` at all — see `audit.py:113-140`), the
LLM-Ops registry cache warm at startup (`main.py:168-177`, reads `prompt_versions` with no
tenant), the SLA sweeper and the memory consolidation sweeper (background tasks with no
request context, `main.py:198-215`), and anything in `aegis/ops` reached outside a
request.

**Mitigations, in order:**
1. Ship the predicate change **behind a setting** (`RLS_FAIL_CLOSED=false` default) so it
   can be flipped per environment and rolled back in one env var.
2. Before flipping, add a temporary logging wrapper that records every session which
   executes a query on a tenant-scoped table **without** having bound a scope. Run the
   full test suite and a manual click-through with it on. The log is the enumeration.
3. Background tasks (sweepers, cache warm) move to the **admin engine** explicitly —
   they are platform-scoped by nature and should never have been on the tenant connection.
4. Only then flip the default and delete the setting.

### 4.3 The approvals-ownership change breaks the money-shot demo — **High likelihood, medium impact**

The demo currently logs in as `admin` (the dev demo principal, `tenant_id=None`,
`platform_admin`) and approves. After Phase 3 that principal can no longer decide a
tenant's gate — by design. Any demo script, video, or `_DEMO_USERS` seed that assumes
otherwise breaks.

**Mitigation:** in the same commit as the permission change, seed a real demo tenant with
a real `tenant_admin` login and update `_DEMO_USERS` / the seed script together. Rehearse
the gate flow immediately after. This is a one-line risk to write down and a one-hour risk
to fix if caught, and a demo-day disaster if not.

### 4.4 Alembic baseline mismatch against a live dev database — **Medium likelihood, low impact**

`create_all` + two reconcilers have been mutating dev databases for weeks. The baseline
revision must match what is *actually* on disk, not what the models declare, or
`alembic stamp head` lies and the first real migration fails.

**Mitigation:** generate the baseline with `--autogenerate` against a **freshly created**
database (guaranteed to match the models), then run `alembic check` against the live dev
database and reconcile the diff explicitly before stamping. Budget half a day for this
inside Phase 1; it is the step people skip.

### 4.5 Scope creep from `tenant_roles` into a full policy engine — **Medium likelihood, medium impact**

Permission systems are a tarpit: resource-level grants, role inheritance, deny rules,
ABAC. The v2 note asks for something much smaller — a tenant can name sub-roles and pick
what they can do.

**Mitigation:** fix the permission catalogue as a **flat, closed set of string keys** with
no inheritance and no deny rules, defined in one file, in Phase 3. If a later requirement
genuinely needs hierarchy, that is a new ADR, not a Phase 3 expansion.

### 4.6 Two engines double the connection-pool footprint — **Low likelihood, low impact**

On a 16 GB laptop running Postgres + Neo4j Desktop + Memurai + Phoenix + Next.js, two
SQLAlchemy pools at default `pool_size=5, max_overflow=10` is up to 30 connections.
Postgres' default `max_connections=100` absorbs this, but Neo4j Desktop is the memory
hog on this box.

**Mitigation:** size the admin pool small and explicitly (`pool_size=2, max_overflow=3`) —
it serves only platform-admin reads and bootstrap. Note the sizing in ADR 0010.

---

## 5. Total effort

| Phase | Days |
|---|---|
| 0 — Documentation truth | 1.0 |
| 1 — Alembic migrations | 2.5 |
| 2 — Postgres everywhere (2a harness 2.0 / 2b migrate 4.0 / 2c cleanup 0.5) | 6.5 |
| 3 — Tenant hierarchy + sub-roles + approvals ownership | 4.0 |
| 4 — RLS fail-closed on 13 tables | 4.0 |
| 5 — Audit filters + CSV reports | 2.5 |
| 6 — ML out of the agent graph | 1.5 |
| 7 — Domain-neutral adapter seam | 3.0 |
| 8 — Post-foundation hardening | 5.5 |
| **Total** | **30.5 days** |

Excluding Phase 8, the foundation is **25 days**. These are focused engineer-days; on a
calendar with other workstreams running, apply your own multiplier. Phase 2's 4-day
migration sub-phase is the estimate most likely to be wrong, and it is wrong in the
upward direction.

**A defensible early cut,** if a checkpoint demo lands mid-stream: Phases 0 + 6 + 3
(≈6.5 days) produce the most jury-legible delta — honest docs, a clean agent trace, and a
real tenant hierarchy where a tenant admin owns their own approvals. Phases 1, 2 and 4 are
worth more architecturally but demo as "the tests are green and the database says no",
which needs a `psql` window to land.

---

## 6. Decisions I am recommending, and the ones I cannot make alone

**Recommended, with reasons given above — treat these as decided unless overridden:**

1. Alembic, host-side, with a stamped baseline. (§Phase 1)
2. A hand-rolled asyncpg test harness; **not** `pytest-postgresql`. (§Phase 2a)
3. Transactional-rollback isolation by default, template-clone via a `pg_fresh` marker.
4. Two Postgres roles + two engines; **no** GUC bypass flag. (§1.1)
5. `SECURITY DEFINER` login lookup rather than a table-wide fail-open policy. (§1.3)
6. `tenant_roles` layered under the existing 4-value coarse `Role`; **do not** widen the
   `user_role` Postgres enum. (§1.4)
7. Flat, closed permission-key catalogue; no inheritance, no deny rules. (§4.5)
8. CSV exports, not PDF. (§Phase 5)
9. Generate the web mock fixtures from the adapter generator. (§Phase 7)
10. RLS table list derived from metadata (`has tenant_id`), pinned by an explicit
    expected-set test.

**Genuinely needs the user — with my default if no answer comes:**

- **Does `platform_admin` get read-only visibility of tenant approvals, or none at all?**
  The v2 note says "aegis admin checks their stuff not tenant/persona admin stuff", which
  reads as *none*. But a platform operator with zero visibility into a stuck tenant gate
  cannot support their customer. **Default: read-only `approvals.observe`, decision
  disabled with a visible reason.** This is reversible in one permission grant.
- **Is `tenant_owner` a distinct role or just the first `tenant_admin`?** The note says
  "one id will be the main client id which will have the power to add admin". **Default:
  a distinct `is_tenant_owner` flag, exactly one per tenant, undeletable, the only
  principal that can appoint or remove `tenant_admin`s.**
- **Do the demo principals (`_DEMO_USERS`, routes.py:205-211) survive v2?** They are
  dev-only and already disabled outside dev, but they are also the reason the offline demo
  works with no seeding. **Default: keep, but re-point them at a seeded demo tenant so
  they exercise the real tenancy path instead of bypassing it.** A platform-scoped demo
  admin will otherwise mask exactly the bugs Phase 4 is meant to catch.
- **Retention/deletion policy for tenant data.** "Tenant offboarding" is not in the note
  but is the first question an enterprise buyer asks. **Default: out of scope for now,
  documented as a roadmap item in ADR 0010.**

---

## 7. What the v2 note missed in this domain

The note explicitly asks for suggestions. These are the gaps I would raise, ordered by how
badly their absence would show under scrutiny.

1. **Token revocation.** 12-hour bearer JWTs with no `jti` and no denylist. Deactivating a
   user, demoting them, or removing them from a tenant has **no effect on a live token**.
   For a platform whose pitch is "secure enough to buy", this is the single most likely
   question from a security-minded judge, and today the honest answer is "we can't". This
   is the highest-value thing in this list. (Phase 8.1)

2. **Nothing enforces `TenantStatus.SUSPENDED`.** The enum value exists and the admin UI
   can presumably display it; no code path checks it. A suspended tenant's users
   authenticate and spend budget normally. Either wire it into `_authenticate` and
   `enforce_governance` or delete the value — a status that means nothing is worse than no
   status. (Phase 8.4)

3. **No password policy, no reset, no forced rotation.** `POST /admin/users` accepts
   `"a"` as a password. A tenant admin who provisions 20 users cannot reset any of their
   passwords. "Admin can add a user and they can really log in" is only half the story;
   the other half is what happens on Monday when one of them forgets. (Phase 8.2)

4. **No login audit.** `audit_log` records tool calls, approvals, admin CRUD and (after
   Phase 5) exports — but **not authentication events**. No successful-login row, no
   failed-login row, no lockout, no rate limit on `/auth/login`. Both a compliance gap and
   a brute-force exposure. Cheap: two `record_audit` calls and a counter.

5. **Budgets have no alerting and no soft threshold.** `enforce_governance` is binary:
   under cap → proceed, over cap → `BudgetExceededError`. A tenant hits 100 % with no
   warning at 80 %, and there is no notion of a grace/soft cap. The note asks for budget
   *reports*; what a tenant admin actually needs is to know **before** they are cut off.
   The data is already there (`BudgetStatusRow` computes `*_remaining`) — this is a
   threshold and an event.

6. **`audit_log` has no index for the filters the note asks for.** Today: indexes on `ts`
   and `action` only. "Filter by tenant and stuff" over a growing trail will table-scan.
   Covered in Phase 5.2, but worth naming as its own oversight.

7. **`chunks` (retrieval) carries `tenant_id` and has no RLS and no dedicated app filter
   review.** The note is emphatic about memory isolation; RAG chunks are the *same* class
   of data and were not mentioned. Included in Phase 4's 13 tables.

8. **The "no code change, everything from the dashboard" goal implies a settings store
   that does not exist.** The note wants tenants to set their own guardrails, model
   defaults, system prompts, and skills from the UI. `prompt_versions` exists (per-tenant,
   in `aegis/ops/models.py`) — but there is no `tenant_settings` table, so guardrail
   config, model defaults and feature toggles have nowhere to live. Without it, every
   "configurable from the dashboard" feature invents its own storage. **Recommend one
   `tenant_settings(tenant_id, key, value jsonb, updated_by, updated_at)` table with a
   typed accessor layer, landed in Phase 8.3, before the console team needs it.**

9. **`config.py` has no way to *see* the effective governance config per tenant.**
   `GovernanceConfig` (`aegis/governance/config.py`) surfaces the *platform* knobs as
   read-only data — a genuinely good idea that already exists. Extend it per tenant and
   you get a "why did this happen to me" page for free, which is worth more to a jury than
   another chart.

10. **Backup/restore is unmentioned.** One `pg_dump` line in the runbook, plus a documented
    restore test, is ten minutes of work and a direct hit on the Production Roadmap
    weighting.

---

## 8. Files this plan touches — index

**`aegis/src/aegis/governance/`** — `rls.py` (predicate, table list, grants), `types.py`
(`TenantRoleRow`, `RlsConfig`, `AdminUserRow`), `models.py` (`TenantRole`, `User` columns),
`enforcement.py` (tenant-role CRUD, tenant owner creation), `audit.py` (filters,
pagination), `config.py` (`fail_closed`, table list), `schema.py` (demoted to drift
detector), **new** `permissions.py`.

**`aegis/src/aegis/agent/`** — `graph.py`, `deps.py`, `state.py`, `events.py`,
`harness.py`, `topology.py` (all Phase 6, ML removal).

**`backend/src/app/`** — `data/session.py` (two engines, bootstrap→verify),
`config.py` (`postgres_admin_dsn`, `AEGIS_ADAPTER`, `RLS_FAIL_CLOSED`), `main.py`
(background tasks → admin engine), `api/routes.py` (permissions, approvals ownership,
tenant-role endpoints, audit filters, report endpoints), `api/schemas.py`,
`agent/deps.py` + `agent/events.py` (ML removal), `adapter/**` (Phase 7),
**new** `platform/reports.py`, **new** `backend/migrations/**` + `alembic.ini`.

**`web/src/`** — `lib/api/client.ts` + `lib/api/types.ts` (create-user/create-tenant/
tenant-roles/report downloads), `lib/stream.ts`, `state/runReducer.ts`, `config/signals.ts`,
`components/trace/describeEvent.tsx` (ML event removal), `mock/**` + `config/personas.ts`
(Phase 7).

**Tests** — 5 conftests + 19 inline-engine files migrated (Phase 2); **new**
`aegis/tests/governance/test_rls_postgres.py` (~20 tests, Phase 4); new suites for
tenant roles, permissions, reports, and the adapter contract.

**Docs** — `docs/adr/0008` (correction), **new** `docs/adr/0010`,
`docs/teaching/memory/{10-guide,50-interview}.{md,html}`,
`docs/architecture/memory-spec.md`, `docs/learn/40-pipelines.md`,
`backend/src/app/adapter/SWAP.md`, `INSTALL.md` (roles + test DB setup),
`docs/operations/runbook.md` (migrations, backup).
