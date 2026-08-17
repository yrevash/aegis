# Backlog — after 30 August

**This is a menu, not a spec.**

Everything here was considered, costed, and deliberately left out of the 14-day plan. It is
written down for two reasons: so nobody quietly starts one of these before the hackathon, and
so nothing that took real research to find gets lost after it.

Each item says what it is, why it waited, and what it unblocks. Day estimates come from the
deep research in [`plans/`](plans/) and are focused engineer-days.

Nothing on this list is cancelled. Several items are better engineering than what shipped.
They are simply not what has to be true on 30 August.

---

## Data and schema

### Postgres everywhere, including the test suite — ~6.5d

There is **no SQLite in production source** — `grep -rn sqlite backend/src aegis/src` returns
nothing. SQLite survives only in the test suites: 5 conftest `db` fixtures plus 19 test files
with an inline `create_async_engine`, 24 entry points in total, covering roughly 165 directly
bound tests and the whole 169-test `backend/tests/api` suite that rides
`backend/tests/conftest.py`. Deferred because it buys correctness we can get more cheaply
before the hackathon from one targeted live-Postgres isolation test (Phase 1, task 1.4), and
because 6.5 days is nearly half the window. It unblocks the honest sentence *"the tests run
against the same database the product runs on"*, and it is a hard prerequisite for verifying
fail-closed RLS — you cannot test a Postgres policy on SQLite. Expect a day of it to be
genuine dialect differences that were never exercised: native enums (`user_role`,
`budget_scope`, `approval_status`), tz-aware `func.now()`, asyncpg's integrity-error
hierarchy, and the `FakeApproval.__tablename__ = "approvals"` collision in
`aegis/tests/ops/conftest.py` against the host's real `approvals` table.

### Alembic — ~2.5d

There is no migration tool. Schema comes from `AegisBase.metadata.create_all` plus
`reconcile_additive_columns` (additive-only, Postgres-only) and a one-off timestamp retype.
`backend/pyproject.toml:36` documents the deliberate absence. That handles added nullable
columns and nothing else — no renames, no type changes, no enum widening, no backfill, no
downgrades. Deferred because it is valuable and not load-bearing for 30 August: the new tables
this plan adds (`chat_sessions`, `chat_messages`) are creations, which `create_all` handles.
It unblocks every non-additive schema change after the hackathon — the tenant-role hierarchy
needs a table with a foreign key plus a NOT-NULL defaulted column on an existing table, which
the current reconciler refuses. The sharp edge is the baseline: generate it with
`--autogenerate` against a *freshly created* database, then `alembic check` against the
long-lived dev database and reconcile the diff explicitly before stamping.

### The `tenant_settings` / `user_preferences` table — ~1.5d

The v2 doc asks for model selection with Aegis defaults overridable **per tenant and per
user**. Phase 5 ships the picker and the per-run override — the runtime seam is the
`set_governance_context` contextvar already bound in `/query` — but the persisted default
needs a settings table with `platform < tenant < user` resolution. Deferred because the
picker demonstrates the capability and the persistence does not appear on screen. It unblocks
saved model defaults, per-tenant harness knob values, per-tenant guardrail toggles, and
generally the "almost zero code change, everything set from the dashboard" goal, which needs
somewhere for a setting to live.

### Audit retention and partitioning — ~1d

`audit_log` grows forever with no documented retention and no partition. Deferred as pure
production-roadmap work with no demo surface. It unblocks a credible answer to "what happens
after a year", and even a documented policy with one implemented monthly partition scores
under Production Roadmap. Pair it with the `pg_dump` backup line and a documented restore
test, which is ten minutes of work nobody has done.

---

## Security and identity

### RLS that fails closed on an unset scope — ~4d

Phase 1 closes the *coverage* gap: every table with a `tenant_id` column gets a policy, up
from three (`_RLS_TABLES = ("users", "usage_ledger", "approvals")`,
`aegis/src/aegis/governance/rls.py:32`). It does not close the *unset* gap. The predicate
still fails open when `app.tenant_id` is unbound, and deleting that branch is not a predicate
tweak — it needs a `SECURITY DEFINER` `aegis_auth_lookup(username)` function so login can read
`users` before a tenant is known, and two Postgres roles (`aegis_owner` with `BYPASSRLS` for
bootstrap and enumerated platform-admin reads, `aegis_app` without it for every tenant-scoped
request), plus two engines and a routing decision at the FastAPI dependency. Deferred because
of that scope and because it depends on the Postgres test migration above — flipping the
predicate with no Postgres tests means discovering the breakage in a browser. It unblocks the
strongest available claim: *"the request connection is a Postgres role that physically cannot
see another tenant's rows — there is no flag to get wrong."* Known unscoped readers that will
go silently empty when it flips: `list_recent_audit`, the LLM-Ops registry cache warm at
startup, the SLA sweeper and the memory consolidation sweeper. Enumerate them with a logging
wrapper before flipping, and move background tasks to the admin engine.

### The tenant and sub-role hierarchy — ~4d

Today `Role` is a closed 4-value `StrEnum` persisted as a native Postgres enum, and the fine
tier (`platform_admin` / `tenant_admin`) is *derived* from `role is ADMIN and tenant_id is
None`, not stored. There is no tenant-defined role, no permission table, no grant model. The
target is a `tenant_roles(tenant_id, key, label, permissions jsonb)` table layered **under**
the coarse enum — the enum stays the portal selector, so no native-enum widening — plus a flat,
closed permission-key catalogue and a `require_permission(...)` dependency. Deferred because
the existing role model is sufficient for the demo, and Phase 6 moves approvals ownership
using the fine role that already exists. It unblocks tenant admins defining their own
sub-roles, per-permission gating of skills and memory upload, and the harness scoping story
("the AI team can select per user or per tenant; a tenant can do the same for itself"). Guard
against the tarpit: flat keys, no inheritance, no deny rules. If hierarchy is ever genuinely
needed, that is a new ADR.

### Token revocation — ~1.5d

JWTs are 720-minute bearers with no revocation (`aegis/src/aegis/governance/security.py:243`
mints the `exp` and there is no `jti`, no deny list, no refresh endpoint). Deactivating or
demoting a user does not invalidate a live token — a 12-hour window in which a removed user
keeps their access. Deferred because it never appears in the demo and nothing depends on it.
It unblocks an honest answer to the offboarding question, which is the first thing an
enterprise security reviewer asks. Either a `jti` claim plus a `revoked_tokens` table, or
short access tokens plus a refresh endpoint.

### `TenantStatus.SUSPENDED` enforcement — ~0.5d

The enum value exists (`aegis/src/aegis/governance/models.py:51`) and is enforced **nowhere**.
A suspended tenant's users log in and spend budget exactly as before. Deferred because it is
half a day and invisible. It unblocks a real offboarding and non-payment path, and it removes
a dead enum value that an AI reader scoring the repo will notice. Enforce it in `_authenticate`
and in `enforce_governance`, or delete the value — but do not leave a control that is declared
and not applied. That is the exact anti-pattern the master plan's "no silent fallbacks"
principle exists to prevent.

### Login audit and rate limiting — ~1d

Half of this is already done, and the plans overstated the gap. A **successful** login writes
an `auth.login` audit row (`backend/src/app/api/routes.py:707`). A **failed** login writes
nothing — the 401 raises before the audit call. And there is no rate limiter anywhere in
`backend/src`: no `slowapi`, no per-IP or per-username throttle, on any endpoint. So the
system cannot see a brute-force attempt and cannot slow one down. Deferred because it is not
demoable and nothing depends on it. It unblocks the credential-stuffing answer, and the failed
-login audit row is also what makes the rate limiter's threshold observable. Pair it with a
password policy — `create_user` accepts any string today, there is no minimum length, no forced
first-login change, and a tenant admin cannot reset a user's password.

---

## Platform capabilities

### The MCP server, done properly, and the MCP client — ~7–9d

`backend/src/app/mcp/server.py` is already a real 593-line `mcp` SDK 2.0 low-level server over
the adapter tool registry, and its policy is already right: the tool list is allowlist-filtered
per persona, every call re-checks the allowlist and writes an audit row, and HIGH-risk tools
are listed but never auto-executed — they return "requires human approval, routed to the
inbox". What it is not: stdio only, so nothing in-product can reach it; and the persona is
pinned by an `MCP_PERSONA_ID` env var (`server.py:90`), which is a process-wide single-tenant
assumption with no per-caller identity. The work is Streamable HTTP mounted at `/mcp`, an
`AuthContext` derived from the bearer per call, per-caller RBAC on `on_list_tools` (a tool the
caller may not use is never *listed*), MCP resources for `aegis://tenants/{id}/budget` and
friends, and `via="mcp"` on every audit row. Deferred because it is genuinely excellent and
genuinely not required to win a blind problem statement. It unblocks Claude Desktop or any MCP
host driving Aegis under real RBAC.

The **client** is the bigger prize and the one the framing usually misses: a general MCP client
in `aegis/src/aegis/mcp/client.py` that connects to configured servers and adapts their tools
into the platform registry, with an unknown external tool defaulting to HIGH risk and therefore
landing at the existing human gate. That turns any external MCP server into agent capability
under the same gate, the same audit and the same allowlist — and it is what lets the Aegis
admin point the client at Aegis's own `/mcp` and ask questions of the platform in natural
language. Add an `mcp_servers` registry table so servers are added from the dashboard.

### The skills subsystem — ~6–8d

The plumbing is already real: `aegis/src/aegis/memory/recall.py:300-319` lists `*.md` under
`spec.SKILLS_DIR`, `spec.select_skills(query, persona, available)` chooses, and
`memory/working.py` injects the bodies into working memory with a token budget share and an
eviction priority. The reference implementation is a hardcoded keyword→skill dict over two
files. What is missing is everything that makes it a subsystem: no frontmatter, no description
tier, no progressive disclosure (bodies are injected whole), no storage beyond the repo
filesystem, no per-tenant or per-user scoping, no authoring UI, and no visibility that a skill
fired. The recommendation is to adopt the published **Agent Skills open standard** verbatim —
a folder, a `SKILL.md` with `name` + `description` frontmatter, three-tier progressive
disclosure — rather than inventing a format, and to make activation a `load_skill(name)` **tool
call** so the user *sees* their skill being loaded as a chip in the agent's log. Deferred
because it is a whole subsystem and the demo does not need it. It unblocks per-tenant agent
behaviour without a code change, and a much better sentence than "we invented a skill format".
Worth stating in the UI when it lands: a tool is a capability, a skill is instructions, and the
risk gate applies to tools — which is exactly why tenants can author skills freely while tool
registration stays a platform action.

### Per-tenant LLMOps — ~3d of the harness/LLMOps block

`aegis/src/aegis/ops/` already has draft → eval-gate → promote → rollback over a
`PromptVersion` table that **already carries `tenant_id`**. There is a real multi-tenancy
defect underneath it: the active-prompt cache is keyed by prompt key alone —
`_ACTIVE_CACHE: dict[str, ...]` at `aegis/src/aegis/ops/registry.py:28` — so two tenants
cannot have different active prompts and whichever was cached last wins for everybody. The
table is multi-tenant; the read path is not. **Fix that first, in half a day, before any
per-tenant prompt surface is built** — until it is fixed no such surface is truthful. Then the
surface itself: version list, diff, active marker, the eval delta that gated each promotion
(`ops/gate.py` already computes it), and rollback. Deferred with the rest of the LLMOps
surfaces. It unblocks the thing the v2 doc actually wants tenants to see — the eval score
before and after each promotion, **on that tenant's own runs**, joined from `eval_results` and
the run-event log, both already tenant-stamped.

### The durable run-event log, replay, and the harness as a projection — ~2.5d

Not on the required list, and it is the single highest-leverage idea in the research, so it is
recorded here rather than lost. Aegis already *produces* a stamped, ordered event stream —
`orchestrator.py` stamps every emitted dict with `run_id` and a monotonic `seq` through an
injected `stamp` callable — and then throws it away when the socket closes. `run_summary()` is
already a projection over it; it just runs over a list that lives only in memory. Persisting
every stamped event to a `run_events(run_id, seq, ts, tenant_id, user_id, session_id, agent_id,
type, payload)` table turns five separate features into one table and five queries: the
harness becomes a query, per-agent inspection becomes `WHERE agent_id = …`, per-user scoping
becomes an RLS policy on two columns, per-tenant LLMOps evidence becomes a join, and **replay
becomes free** — re-streaming a real recorded run through the exact same reducer, at real
timings, honestly labelled. That last one is the best demo insurance available and it is not
mock data. Deferred because it is a new table plus a background drain task plus new endpoints,
and Phase 5's three days do not contain it. The write must be best-effort, batched and off the
hot path: a bounded queue, drop-oldest with a counted warning, never blocking a stream.

Adjacent and cheap once it exists: a **cancel endpoint** (`POST /runs/{run_id}/cancel`) with a
cooperative token checked between agent steps. Four agents burning tokens with no way to stop
them is both a live-demo hazard and something no enterprise buyer accepts.

---

## Quality and measurement

### Making the knowledge graph load-bearing — ~6–8d

The graph works today as a second recall arm feeding RRF. It is not a reasoning substrate. The
work to change that: entity resolution first — LightRAG's extractor produces near-duplicate
entities ("Acme Corp" / "Acme Corporation" / "ACME") and a graph with three nodes for one
company cannot traverse, which makes canonicalisation the highest-value and least glamorous
fix. Then a bounded, **parameterised** Cypher path query exposed as an agent tool (no
model-generated Cypher — that is an injection surface), traversed paths rendered as citations
beside passage citations, query-time entity linking so we control the entry points, and
graph-path candidates fused as a genuinely third list. Deferred because it is the largest and
least predictable phase in the research — entity-resolution quality is empirical — and 14 days
cannot absorb it safely. It unblocks a two-hop question that vector search demonstrably cannot
answer, shown with the path as evidence. Ship it with the **ablation**: run the eval set five
ways (vector / +BM25 / +graph / +rerank / all) and publish the table. If the graph adds nothing
measurable, say so. And measure the overlap between the vector and graph lists first — two arms
returning nearly the same documents look like corroboration and are not.

---

## Also deferred, in one line each

- **`AegisEmitter` / AG-UI.** Two streaming protocols where only one is live. Its only
  production construction is `routes.py:801`, and `web/src/lib/streamNames.ts` is imported by
  nothing. Either document the split deliberately or delete it — an AI reader scoring the repo
  will notice.
- **The `DomainAdapter` protocol and a second adapter.** Makes "retargetable in ~2 hours" a
  demonstrable claim instead of an asserted one: `AEGIS_ADAPTER=…` switches domains live.
- **Generating the web mock fixtures from `adapter/generator.py`.** Converts eight files of
  hard-coded demo fiction into a swap-time freebie.
- **Corpus-wide BM25 via Postgres full-text search.** No new dependency; the current BM25 arm
  does not fire in production.
- **A local cross-encoder reranker** (`fastembed` `TextCrossEncoder`) alongside the LLM one.
- **Memurai does not ship RediSearch** — verified — which breaks the memory cache's vector
  path. Needs a documented fallback rather than a silent degradation.
- **Sub-agents spawning sub-agents.** Deliberately one level deep; depth is where budgets and
  legibility both die.
- **Token-level streaming from the model.** Currently chunked *after* the output rail, which is
  the right call; changing it needs a streaming-aware output rail, not just a streaming
  gateway call.
- **OAuth 2.1 authorization server for MCP.** Step 2 after bearer-JWT-over-HTTP. Do not claim
  spec compliance before it lands.
- **Password policy, reset flow, and forced first-login change.** `create_user` accepts any
  string today.

---

## Budget natural key has no unique constraint (found 2026-08-17)

`upsert_budget` documents that it deliberately keeps its natural-key lookup un-narrowed *so
that a cross-tenant conflict is refused rather than duplicated*. Row-level security narrows
that lookup behind its back: under a bound scope, tenant 2 cannot see tenant 1's row, so the
"does this key already exist?" check comes back empty and the insert proceeds.

Measured on a scratch database: tenant 1 writes `(user, 42, day)`, tenant 2 posts the same
triple, and the result is **two rows** — `(id 1, tenant 1, cap 100)` and `(id 2, tenant 2,
cap 999999)`.

Isolation itself holds — each tenant reads only its own row. But `budgets` carries only
`PRIMARY KEY (id)`; there is no unique constraint on `(scope_type, scope_id, window)`, so a
platform-admin read sees both and `_budgets_for(...).first()` picks arbitrarily between them.

**Why deferred:** the fix is a unique constraint, which means a migration, and migrations are
deferred until after 30 August. It is pinned by a `KNOWN DEFECT` comment in
`aegis/tests/governance/test_enforcement.py` so it cannot get quietly worse.

**What it unblocks:** a defensible answer to "what stops two tenants claiming the same budget
key?" — currently the honest answer is "nothing structural, and the app-level guard that was
supposed to is blinded by RLS".

---

## Stale scratch databases after an aborted test run

A test process killed with SIGKILL/SIGINT leaves its `aegis_tmpl_*` database and role behind.
The role cannot be dropped until its databases are — drop the database first, then the role.
No auto-sweeper was added deliberately, because it would clobber a concurrently running
suite. Worth a `scripts/` helper that sweeps only objects older than a few hours.

---

## Live defects found while planning the job substrate (2026-08-17)

Verified in source, not inferred. All three are pre-existing.

### Background jobs spend money outside budget enforcement

The memory sweeper binds the **live** `complete` and the real embedding function
(`backend/src/app/main.py:99-112`) and runs a batch every 60 seconds. `enforce_governance`
does not appear anywhere on that path — it lives on the request path only.

So a background job makes real, billed model calls that no budget can stop. With ~$100 of
total gateway credit before a final, a stuck or looping sweeper is a live financial risk, not
a theoretical one.

**Fix:** every model-calling job carries the enqueuer's governance context and spends through
the same enforcer, with `BudgetExceededError` as a first-class job outcome. Costs a payload
field and a `with` block.

### A killed worker strands its job in RUNNING forever

`memory_consolidation_job` is claimed with a guarded `PENDING → RUNNING` update
(`consolidate.py:983-1005`) and has **no lease, no heartbeat, no `claimed_at`, and no reaper**.
A worker killed mid-job leaves the row `RUNNING` permanently — matched by no sweeper and
retried by nothing. This is the same shape as the `RESUMING` hazard Phase 1 fixed in approvals.

`attempts` is incremented at `consolidate.py:1005` and **read nowhere**, so a poison job that
crashes the worker every time is invisible.

**Fix:** claim + lease + reaper, and consult `attempts` to dead-letter a job that keeps failing.

### `/readyz` is documented and does not exist

Referenced in three docstrings; no route implements it. There is also no Neo4j probe in the
health path, and `latency_summary` is a per-process RAM deque that resets on restart.
