# Phase 7 — Control planes

**The "0 code change from the dashboard" phase.** Every control below is a `SettingSpec`
entry from Phase 3 §3.7 rendered by one catalogue-driven form, not a bespoke screen. If a
task here needs its own storage, its own precedence rules and its own idea of who may write
it, it has been designed wrong.

Depends on **Phase 3** (settings catalogue, `fine_role` on the wire, two-tenant seed, job
substrate, `run_events`) and **Phase 6** (the console shell). Nothing here is buildable
against an empty database.

Research behind it: [`plans/06-dashboards-control.md`](plans/06-dashboards-control.md) ·
[`plans/04-enterprise-substrate.md`](plans/04-enterprise-substrate.md) §3–4

---

## What is actually wrong

### 1. Measured: every role can see almost everything and change almost nothing

`plans/06` Part 1 scored each role against the duties that role must be able to perform,
where a row counts only if the endpoint exists **and** a screen calls it, both verified in
source:

| Role | Fully working | The shape of the gap |
|---|---|---|
| **Client** | **1 / 12** | No console at all. Zero write actions in the entire product. |
| Platform admin | 3 / 22 | Two endpoints are `require_platform_admin`-only (`routes.py:1226`, `:1235`). Everything else is shared with tenant admins. |
| Tenant admin | 4 / 18 | The role has **no portal** — `ROLES` carries four coarse values. |
| DevOps | 5 / 12 | Six read screens, one write button, and that button runs a fixed configuration. |
| AI team | 7 / 16 | The richest portal — 14 sections — and every gap is a *write* gap. |

Grepping every API-client function against its call sites in `web/src` finds **eleven write
actions in the whole product**, and `createBudget` (`web/src/lib/api/client.ts:195`) is called
from nowhere — dead client code, which is why there is no budget form.

### 2. The approvals inbox has no screen — and Phase 6's ownership move depends on one

```
web/src/components/sim/SimulationView.tsx:126        <ApprovalCard approval={…} …/>
web/src/components/console/MoneyShotConsole.tsx:214  <ApprovalCard …/>
```

Those are the **only two mounts** of `ApprovalCard` in the frontend. There is no inbox, no
queue, no list.

`_enforce_approval_tenant` (`routes.py:1132`) exempts the platform admin outright:

```python
# backend/src/app/api/routes.py:1144
if auth.fine_role == PLATFORM_ADMIN:
    return
```

So today the Aegis operator decides a tenant's business gate. Moving that ownership to the
tenant admin is correct and it is one deletion — but it **relocates the capability into
nowhere** until an inbox exists. The screen is a prerequisite, not a follow-up.

### 3. There is no way to look at the data without `psql`

No schema browser, no query page, no read-only database role. The requirement is *"view full
db, not go into code or db checking"*, and the naive implementation of it —
`await conn.execute(user_sql)` on the app connection — is measurably unsafe in five distinct
ways (§7.9).

### 4. Pipeline health does not exist, and two probes are missing

Aegis computes honest per-component truth in five places already — `aegis/core/health.py`
probes, the RLS bootstrap read-back, `GET /security/posture`, `GET /stack`,
`latency_summary()`. **Nothing joins them.**

- **`/readyz` is referenced in three docstrings** (`aegis/src/aegis/core/health.py:3`, `:73`,
  `:133`) and **no route implements it**.
- **There is no Neo4j probe.** `health.py` defines exactly three: `probe_redis` (line 51),
  `probe_postgres` (78), `probe_vector_store` (103).
- There is no probe for the LLM gateway, the job substrate, or workers.

### 5. Per-tenant anything is blocked by three concrete defects

| Defect | Source | Consequence |
|---|---|---|
| The guardrail pipeline is a **process-wide singleton** built from process settings | `backend/src/app/guardrails/__init__.py:82` | Per-tenant rails need per-request construction; there is no seam for one. |
| `_ACTIVE_CACHE: dict[str, …]` is keyed by **prompt key alone** | `aegis/src/aegis/ops/registry.py:28` | Two tenants cannot have different active prompts. Whichever was cached last wins for everybody. |
| `list_versions(session, prompt_key)` takes **no `tenant_id`** | `aegis/src/aegis/ops/registry.py:169` | Every tenant sees every tenant's prompt versions, held back only by an RLS predicate that is fail-*open* on an unset scope. |

`get_active()` (`registry.py:156`) already accepts and filters on `tenant_id`, and
`PromptVersion` already carries it. **The table is multi-tenant; the read path is not.**

### 6. Memory is the most open subsystem in the product and the least visible

Eight endpoints, all on `require_auth`, mounted on exactly one portal (`ai_team`):

```
GET  /memory/facts                       routes.py:1525
GET  /memory/profile                     routes.py:1586
GET  /memory/sessions                    routes.py:1615
GET  /memory/sessions/{id}/messages      routes.py:1658
GET  /memory/writes                      routes.py:1726
GET  /memory/recall_debug                routes.py:1770
POST /memory/forget                      routes.py:1879
DELETE /memory/facts/{fact_id}           routes.py:1945
```

**There is no write endpoint**, and `MemoryEvent` (`schemas.py:367-387`) carries
`recalled_fact_count`, `recalled_message_count` and `tokens_used` — counts, no identities. The
user asked to see *which* memories were referenced; the stream says *how many*.

### 7. Three surfaces are declared and inert

- **`GET /harness/config`** (`routes.py:2506`) serves knob descriptors carrying key, type,
  effective value, default, doc and bounds — and its own docstring says it is read-only. The
  descriptors are already a form; there is no `PUT`.
- **`POST /redteam/run`** throws away every parameter the runner accepts:
  ```python
  # backend/src/app/api/routes.py:2646
  report = await run_redteam()          # no completer, no battery, no thresholds
  ```
  `run_redteam(check, *, completer, battery, thresholds)` is fully parameterised
  (`aegis/src/aegis/redteam/runner.py:305`). Nothing is persisted either — the audit row keeps
  three summary numbers.
- **`CacheView.tsx:44`** renders a hard-coded `SPECS` array of configuration prose with no
  fetch and no measurement, on a page labelled with a metric. `GET /metrics` already returns a
  real `cache_hit_rate`. This is the closest thing left in the repo to the fiction Phase 2
  deleted.

### 8. The admin forecast page conflates two different models

`GET /forecast/usage` (`routes.py:2966`) is a **univariate** series over `usage_ledger` fed to
`statsforecast` with conformal intervals (`aegis/src/aegis/forecast/series.py`). SHAP does not
apply to it — there are no features to attribute. SHAP belongs to `TrustworthyModel`, which
builds a `shap.TreeExplainer` per ensemble member (`aegis/src/aegis/ml/model.py:608`) over an
explicit feature list. D7 as literally requested cannot be built.

---

## The ranked gap list, and which task closes it

| Rank | Gap | Roles | Task |
|---|---|---|---|
| 1 | Approvals inbox screen | both admins, client | **7.1** |
| 2 | Tenant-admin portal + navigation split | tenant admin | **7.2** |
| 3 | Admin/tenant create forms (`createUser`, `createTenant`) | both admins | **7.3** |
| 4 | Harness knobs become writable — first consumer of the catalogue | ai_team, tenant admin | **7.4** |
| 5 | Memory: write endpoint, identities on the event, screens on 3 portals | all | **7.5** |
| 6 | Guardrail policy: read platform defaults, add tenant rules (tightening-only) | both admins | **7.6** |
| 7 | Per-tenant LLMOps, after the cache fix | tenant admin, ai_team | **7.7** |
| 8 | Named seats | tenant admin | **7.8** |
| 9 | Admin DB page | platform admin | **7.9** |
| 10 | Pipeline health + `/readyz` | devops, platform admin | **7.10** |
| 11 | Audit filtering | both admins, devops | **7.11** |
| 12 | Downloadable reports | all admins | **7.12** |
| 13 | Red-team parameterisation + history | devops | **7.13** |
| 14 | Live cache surface | devops, ai_team | **7.14** |
| 15 | Admin forecast: two panels, SHAP, feature selection | platform admin | **7.15** |

---

## Tasks

### 7.1 — The approvals inbox screen (0.75d) — **prerequisite for everything else here**

`ApprovalCard` already renders a single gate. This is the list around it.

- `web/src/app/app/[role]/approvals/` mounting a new `ApprovalInbox` on `tenant_admin`,
  `platform_admin` and `client`.
- `GET /approvals` gains `status`, `since` and (platform admin only) `tenant_id`, all through
  `_scope_tenant` so a tenant admin cannot widen its own scope with a query parameter.
- **Delete the `PLATFORM_ADMIN` early exit at `routes.py:1144`.** A platform admin keeps a
  **read-only** view of every tenant's queue: decision buttons rendered, disabled, with an
  honest reason string. Not hidden — disabled and explained. That distinction is the most
  jury-legible sentence in the phase.
- A platform admin may still decide gates whose `tenant_id IS NULL` — Aegis's own actions.
- The **client** sees the fate of gates they raised, read-only. Today `GET /approvals` is
  `require_admin` (`routes.py:1113`), so a user who triggers a HIGH-risk action has no screen
  that tells them what happened to it. That is a workflow hole, not UI polish.

**The seed moves in the same commit.** The Phase 3 two-tenant seed must contain a real
`tenant_admin` who can decide, or the demo can approve nothing at all. Rehearse the gate flow
the same afternoon.

### 7.2 — The tenant-admin portal and the navigation split (0.5d)

`fine_role` reaches the browser in Phase 3.9. This is what the browser does with it.

`ROLE_SECTIONS` (`web/src/lib/portal.ts:278`) becomes five portals, not four:

```
platform_admin: dashboard · governance · tenants · roles · approvals(read-only)
                audit · forecast · policy · health · jobs · db · console
tenant_admin:   dashboard · team · approvals · budget · knowledge · memory
                policy(read + tighten) · llmops · audit · reports · console · settings
ai_team:        console · harness(writable) · agents · llmops · evals · mlops · tokenopt
                memory · rag · graph · cache · voice · vision · guardrails · simulation
devops:         dashboard · health · jobs · stack · patch · security · redteam
                latency · cache(live) · audit
client:         console · dashboard · savings · forecast · risk · memory · settings
```

Two rules that stop this becoming 28 sections per role:

- **A section exists for a role only if that role can *act* on it.** A read-only copy of
  someone else's screen is a tab on their own screen, not a section.
- **`simulation` is a demo artefact, not an operator tool.** It stays on `ai_team` and
  `client` where it tells the isolation story. Do not propagate it.

The Phase 3.10 route-coverage test is extended to the new portals in the same change.

### 7.3 — The two admin forms, and the budget form that was never wired (0.4d)

`POST /admin/users` (`routes.py:1262`) and `POST /admin/tenants` (`routes.py:1226`) are
complete server-side: Argon2 hashing in the data layer, a tenant-admin pinned to its own
tenant with a 403, 409 on a duplicate username, an `admin.user.create` audit row. The gap is
entirely in the browser — `web/src/lib/api/client.ts` has `getTenants`, `getUsers`,
`getBudgets`, `createBudget` and `assignUserRole`, and **no `createUser`, no `createTenant`**.

- Add `createUser` / `createTenant` to the API client with mirrored types.
- `CreateUserForm.tsx`, `CreateTenantForm.tsx`, `BudgetForm.tsx` (wiring the already-dead
  `createBudget`), mounted beside `RolesAccess.tsx`.
- **Surface the server's real errors.** The cross-tenant 403 is the isolation story showing
  its work in front of the jury; rendering it as "something went wrong" throws that away.
- Verify by logging out and logging back in **as the user you just created**. That round trip
  is the entire point of the task.

### 7.4 — Every settings screen is generated from the catalogue (0.75d)

This is the "0 code change" mechanism made visible. One `SettingsForm` component renders any
list of `SettingSpec` descriptors: label, control type from `type` + `bounds`, disabled when
`writable_by` excludes the caller, and a `(value, source)` badge saying *"Aegis default"* /
*"your tenant's"* / *"your choice"*.

- **`PUT /harness/config` is the first consumer and the proof.** The descriptors at
  `aegis/src/aegis/agent/harness.py` already carry type, default and bounds; the route
  (`routes.py:2506`) is read-only by design today. Writes are clamped to the declared bounds
  server-side and audited.
- Then the same component renders the tenant settings page, the platform policy page and the
  seats page in 7.8. **Three screens, one component, zero bespoke forms.**
- The Phase 3 bijection test now bites: a catalogue key with no control fails the suite.

### 7.5 — Per-tenant and per-user memory (1.5d)

Three pieces, in value order.

**(a) Identities on the `memory` event.** Add `recalled: list[MemoryRef]` — `{id, kind,
text_preview, score}` — to `MemoryEvent` (`schemas.py:367`), capped at ~8 entries with
truncated previews because this rides an SSE stream. The data already exists:
`aegis/src/aegis/memory/scoring.py` and `recall.py` produce scored `RecallCandidate`s and
`GET /memory/recall_debug` (`routes.py:1770`) already serves exactly this shape out of band.
**A projection of an existing structure onto an existing event, not a new subsystem.**

**(b) `POST /memory/facts`.** Three non-negotiable requirements:

- **The text goes through `check_input` before storage.** An uploaded memory is injected into
  a future prompt; an unscreened one is a stored prompt injection with a long fuse. This is
  the sharpest security edge in the phase and it is easy to miss because the endpoint looks
  like a CRUD write.
- `origin` is stamped `USER` and the row is written through the existing `MemoryWriteLog` path
  so it appears in `GET /memory/writes` like every other write. No side door.
- `subject_id` is **derived server-side** from `AuthContext`, never accepted from the client.

**(c) Tenant-level memory beside user-level.** `memory_subject_for` returns
`f"user:{user_id}"` (`backend/src/app/adapter/memory_spec.py:112-114`) and `recall()` takes a
single `subject_id`. Do a **second recall pass over `tenant:{id}`, merged in the assembler** —
one call site, `recall()`'s contract unchanged, and precedence is explicit (user facts outrank
tenant facts on a tie). Widening the parameter to a sequence pushes the merge into every
backend query for no extra capability.

**Screens are a mount, not a build.** `MemoryView.tsx`, `SemanticFactsPanel`, `WriteLogPanel`,
`RecallDebugPanel` and `StructuredProfilePanel` all exist. Mount on `tenant_admin` (tenant +
all users, with delete), on `client` (their own only), keep `ai_team`.

**Order matters:** Phase 6 fixes `session_id` on the wire. Until it does, every live run
recalls nothing and this screen is honest and empty.

### 7.6 — Per-tenant guardrails, tightening only (2.0d)

**(i) Read the platform defaults first — nearly free, and it is a trust feature.**
`GET /guardrails/policy` returns the effective rail stack as data: each rail's name, what it
screens, hard-block vs advisory, its threshold, and whether the model-backed layer is wired.
All of it is honest introspection of configuration that already exists in
`Guardrails.__init__`'s parameters (`aegis/src/aegis/guardrails/pipeline.py:118-135`).
Readable by every role. *"Here is exactly what we screen, read it yourself"* is a strong
enterprise answer and it costs a serialiser.

**(ii) Then the write half.** The mechanism the code offers is already correct: injected
`input_rails` / `output_rails` run **after** the built-ins and any non-PASS verdict
short-circuits with its own layer label. Custom rails are therefore **additive by
construction** — a tenant rail can block something the platform passed, and it runs too late
to unblock something the platform blocked.

The obstacle is the singleton at `backend/src/app/guardrails/__init__.py:82`. Fix: **a small
cache keyed on `(tenant_id, policy_version)`** — construct on miss, reuse on hit, invalidate on
a policy write. `Guardrails` holds no per-call state, so instances are cheap and safe to cache;
do **not** rebuild per request, because the injection cache and the media screen it constructs
are not free.

What a tenant may configure — a closed list, as catalogue entries:

| Setting | Merge rule |
|---|---|
| `guardrails.topics.allowed` | `tighten_only` |
| `guardrails.topical.block` | `tighten_only` (false→true only) |
| `guardrails.grounding.block` | `tighten_only` |
| `guardrails.denylist.terms` | `additive` |
| `guardrails.pii.entities` | `additive` |
| `guardrails.custom_rails` | `additive`, **templates only** |

**Custom rails come from a closed template set** — `contains_term`, `matches_pattern` from a
vetted pattern library, `max_length`, `requires_citation` — and the tenant fills parameters.
**Not free-form regex** (a ReDoS vector on the request path) and **not a Colang flow or Python
callable** (remote code execution). If free-form regex is ever wanted it needs a timeout, a
complexity check and a sandbox, and that is a different project.

### 7.7 — Per-tenant LLMOps — fix the read path before building the surface (0.5d + 1.5d)

**Order is the point.** Two defects first:

1. Key `_ACTIVE_CACHE` (`registry.py:28`) on `(prompt_key, tenant_id)`.
2. Give `list_versions` (`registry.py:169`) a `tenant_id` parameter and pass it from
   `GET /ops/prompts` (`routes.py:2038`).

Neither has ever manifested, because `prompt_versions` holds zero rows and there has never
been more than one tenant configuration. **They will manifest on the first day two tenants
exist**, which is the day the Phase 3 seed runs — and the screen will look correct while being
wrong, which is the exact defect class this project has already been burned by.

Then the surface: version list with an active marker, a diff between versions, **the eval delta
that gated each promotion** (`aegis/src/aegis/ops/gate.py` already computes it), and rollback.
Mounted on `tenant_admin` scoped to its tenant, and on `ai_team` with a tenant selector.

**The prompt floor is not editable by a tenant.** `render_floor_prompt`
(`aegis/src/aegis/ops/config.py:8-9`) is the baseline the registry "builds on but never goes
below". A tenant writes a *version*; the platform composes the floor underneath it at render
time.

### 7.8 — Tenant sub-roles: **cut**. Named seats instead (0.75d)

The requirement is *"aegis admin and tenant admin can make more sub roles under the tenant"*.
The literal version is a trap, and the arithmetic is the argument:

- A `tenant_roles(tenant_id, key, label, permissions jsonb)` table plus a closed permission
  catalogue plus a `require_permission(...)` dependency layered under the eight role guards in
  `routes.py` is **~4 days**.
- It needs a table with a foreign key **plus a NOT-NULL defaulted column on an existing
  table**, which `reconcile_additive_columns` refuses — so it drags in **Alembic, ~2.5 days**.
- **Seven days whose demo is "I made a role called Analyst."**

Ship instead a closed set of tenant-scoped capability toggles in the **same settings table**
from 7.4:

```
seat.can_upload_documents      bool   default false
seat.can_edit_memory           bool   default false
seat.can_approve               bool   default false   (tenant-owned gates only)
seat.can_view_tenant_audit     bool   default false
seat.can_change_agent_mode     bool   default true
seat.label                     string default ""      ("Analyst", "Support Lead")
```

- Zero new schema mechanisms and zero new auth mechanisms. The coarse guard still runs first;
  the toggle is a second, **narrowing** check that can only remove capability, never add it.
- It demos identically: *"I created a Support Lead seat that can approve but cannot upload
  documents"* is the same sentence, and it is true.
- It does not foreclose the real thing — if `tenant_roles` is ever built, these keys are the
  initial permission catalogue.

**The honest limitation, stated so nobody oversells it:** these are per-user flags, not a role
*object* assignable to many users. Twelve identical Analysts means setting six flags twelve
times. That is a real ergonomic shortcoming and it is worth 6.25 days.

### 7.9 — The admin database page: build the hardened path first (1.0d + 0.75d + 0.5d)

Five measured findings decide this design. Every one was run against a live Postgres.

| # | Finding | What it kills |
|---|---|---|
| 1 | asyncpg's `execute()` with no arguments **runs multiple statements silently**. `"SELECT 1; SET default_transaction_read_only = off; SELECT 2;"` returns the status tag `'SELECT 1'` and the setting is now `off`. | The obvious `await conn.execute(user_sql)` implementation, and any belief that the return value would tell you. |
| 2 | The **extended protocol refuses multi-statement** — `fetch()`, or `execute()` with any bind parameter, raises `PostgresSyntaxError: cannot insert multiple commands into a prepared statement`. | The regex. *How the query is sent* is itself a control, and it is free. |
| 3 | **`SET LOCAL ROLE` is not a boundary.** `RESET ROLE` is one legal statement; the probe went from `aegis_ro` back to the superuser and read `pg_authid`. | The "run it on the app connection with a role assumed" design, entirely. |
| 4 | **`default_transaction_read_only` is user-settable** — the read-only role turned its own setting off. What stopped the write was the absent grant. | Relying on the setting. The **privilege** is the boundary; the setting is a guard rail that turns a mistake into a clean error. |
| 5 | **Column-level grants work and `information_schema` respects them.** Withhold `users.password_hash`: `SELECT *` is refused, a `WHERE password_hash LIKE …` predicate is refused, **and the catalog stops listing the column**. | The denylist. The permission model *is* the schema browser's source of truth, with nothing to drift. |

**The recommendation: hardened read-only path → schema browser + saved parameterised queries
on it → free-form SQL on the *same* path behind a platform-admin toggle.** One execution path,
two front doors. **Cut the box if something must go; never cut the path.**

The controls, ordered by how much they carry:

1. A dedicated login role `aegis_readonly` — `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`,
   owning nothing, `GRANT SELECT` only, with column grants withholding `users.password_hash`.
   A third `.sql` file beside `scripts/sql/aegis-app-role.sql`, mirroring `SERVING_ROLE`'s
   provisioning at `aegis/src/aegis/governance/rls.py:543` — not a new pattern.
2. **Its own DSN, its own engine and pool.** Never `SET ROLE` on the app connection (Finding 3).
3. **Every query over the extended protocol** (Finding 2).
4. `ALTER ROLE aegis_readonly SET statement_timeout = '10s'` — verified: `pg_sleep(10)` →
   `canceling statement due to statement timeout`.
5. `idle_in_transaction_session_timeout = '30s'`.
6. `default_transaction_read_only = on` — belt, not boundary.
7. **Row and byte caps.** Wrap as `SELECT * FROM (<query>) _q LIMIT 1001` — 1000 shown, the
   1001st proves truncation — and abort past ~5 MB serialised. Read-only is not the same as
   harmless; this page can dump every tenant to CSV.
8. **`EXPLAIN` pre-flight** (not `ANALYZE`), refusing above a cost ceiling and showing the
   plan. Turns "timed out after 10s" into "this would scan 40M rows, here's why".
9. **An audit row per query, always** — `db.query.execute` with SQL text, parameters, row
   count, bytes, duration, verdict and `via: 'browser'|'saved'|'freeform'`. Written **before**
   execution and updated after, so a query that kills the process still leaves a trace.
10. `require_platform_admin`, never `require_admin` — the latter admits both tiers.
11. A rate limit. There is no rate limiting anywhere in `backend/src` today, and this is the
    worst page for that to remain true.
12. `ADMIN_SQL_CONSOLE=off` as a kill switch, default off outside dev.

**Deliberately NOT a control: a SQL parser, a keyword denylist, or a regex.** They are
bypassable by comments, casing, unicode and nested CTEs, they create false confidence, and
every threat they aim at is closed by controls 1–3 at a layer that cannot be tricked.

**The precedent worth knowing:** Metabase — a mature product whose entire business is this
problem — **disables native SQL for any database with row/column security**, because it cannot
parse SQL well enough to know which tables a query touches. Aegis has RLS on 13 tables. That is
a serious product's considered verdict that free-form SQL and row-level security do not compose
safely through string analysis.

**The schema browser, concretely:** catalog from `information_schema` executed *as*
`aegis_readonly`; keyset pagination on the primary key, not `OFFSET`; identifiers **matched
against the catalog list**, never string-escaped — the same discipline `_SAFE_ROLE_NAME` uses
at `rls.py:551`; foreign-key navigation from `information_schema.referential_constraints`;
row-count estimates from `pg_class.reltuples` with exact counts only on request.

**And the thing worth more than the free-form box:** a **tenant-impersonation toggle**. Bind
`app.tenant_id` to a chosen tenant and re-run the same query. Watching rows disappear when a
scope is bound is the most convincing thirty seconds of the isolation story available, and it
is one `set_config` call.

**Note for Phase 9:** this page reads across tenants only because the RLS predicate is
fail-open on an unset scope (`rls.py:195`). When Phase 9 closes that, this page goes silently
empty unless it is on the enumerated list. It is.

### 7.10 — Pipeline health as an aggregation, not a subsystem (1.0d)

Anyone proposing a new metrics subsystem should be shown the five existing sources first. The
page is a **join** over them plus three missing probes.

- **`probe_neo4j`** beside the other three in `aegis/src/aegis/core/health.py` — a driver
  `verify_connectivity()`.
- **LLM gateway health derived from work already done**, not a synthetic ping: last successful
  call timestamp and error rate over the last N calls, read from `usage_ledger`. A probe that
  spends money to prove it can spend money is a bad trade on $100 of credit, and evidence of
  work beats a fabricated ping.
- **`/readyz`** — run every probe concurrently, 200 only if every *required* dependency is up,
  503 otherwise, per-component detail in the body.

```
GET /platform/health          -> [ComponentHealth]   admin/devops
GET /readyz                   -> 200 | 503           unauthenticated
GET /platform/jobs            -> depth by kind/status, oldest pending, dead, workers
GET /platform/logs?component= -> the ring buffer     platform admin
```

`ComponentHealth` is one shape for every component: `{key, name, category, status, detail,
measured_at, evidence}`. **`evidence` is required** — the query or probe that produced the
verdict. A status with no provenance is the exact class of claim the audits keep catching.

Two states this page must have that most do not:

- **`unknown` ≠ `down`.** A probe that timed out is not the same fact as a dependency that
  answered "no". Rendering the first as the second is a lie in the safe direction, and this
  project has already been bitten by lies in the safe direction.
- **Degraded is loud.** If Neo4j is down, retrieval keeps working *and the answer carries a
  banner saying the graph arm was unavailable*. The degradation is a property of the answer,
  not only of an admin page nobody has open.

RLS is the one row that is **red, always, no exceptions** when
`audit_rls_enforcement()` (`rls.py:630`) reports a shortfall.

**"Logs of their own component" — and what is refused.** Not a log-aggregation stack (Loki,
Seq, ELK) on a 16 GB Windows box with no Docker; that is a second platform to operate for one
page. Three real sources instead: `run_events` filtered (every request-path component),
`jobs` rows (every background component), and a bounded in-process structured-log ring buffer
(`deque(maxlen=2000)` with `trace_id`, ~60 lines) for boot, sweepers and driver warnings —
labelled **per-process and volatile**, exactly the way `latency_summary` already labels its
window. **Say plainly on the page: this is not a log store.**

### 7.11 — Audit filtering (0.15d)

`GET /audit` (`routes.py:1090`) takes `limit`, clamped to `[1, 200]`, and nothing else.

- Add `tenant_id`, `actor`, `action_prefix`, `since`, `until` — all through `_scope_tenant`
  (`routes.py:436`), so a tenant admin cannot widen its own scope with a parameter.
- The tenant selector renders only for a platform admin.
- Add the index on `(tenant_id, ts DESC)`. It is the filter's driving predicate; today only
  `ts` and `action` are indexed.

### 7.12 — Downloadable reports (0.4d)

One module, `backend/src/app/platform/reports.py`, streaming CSV with a shared
`Content-Disposition` helper. There is no `Content-Disposition`, no CSV writer and no report
endpoint anywhere in `routes.py` today.

- `GET /reports/audit.csv` (the filtered trail) · `/reports/tenant.csv` (roster: users, roles,
  last login) · `/reports/budget.csv` (caps vs consumption) · `/reports/forecast.csv`.
- **`budget.csv` reads the same `BudgetStatusRow` the enforcer reads**, so the report and the
  cap cannot disagree. Generally: **the report is generated from the same accessor the screen
  renders**, never a parallel query.
- **`forecast.csv` carries the caveats as columns, not as a footnote.** `ForecastResponse`
  carefully distinguishes `requested_coverage` from **achieved** coverage on rolling-origin
  held-out windows, and `/forecast/budget` (`routes.py:2998`) explicitly flags
  `cumulative_bounds_are_calibrated = false` because summed marginal conformal bounds are not a
  calibrated cumulative interval. A CSV that drops those turns a carefully honest surface into
  a misleading spreadsheet the moment it leaves the browser.
- **Every export writes its own `report.export` audit row** carrying the filter parameters. An
  export of the audit trail that is not itself audited is the first hole a procurement reviewer
  finds.
- CSV, not PDF. PDF needs a rendering dependency on a no-Docker Windows box; a compliance
  export gets opened in Excel.

### 7.13 — Red-teaming the infra profile can actually control (1.0d)

The battery is real: a curated `Attack` dataclass set tagged with garak-aligned categories and
OWASP LLM Top-10 ids, split into attacks and benign controls so the report measures a
**false-positive rate** as well as a block rate, with real verdicts from real `GuardResult`s.
None of it is a gimmick. The endpoint at `routes.py:2646` throws all of it away.

Expose the parameters that already exist:

| Control | Backed by |
|---|---|
| Select categories | `Attack.category` — already tagged |
| **Run against the live model layer** | the `completer` argument. The battery already marks `needs_llm=True` attacks the deterministic rails cannot catch by design — this is the difference between "our signatures hold" and "our stack holds" |
| Adjust thresholds | `RedTeamThresholds` |
| Site-specific probes | `battery`, from parameterised templates — same discipline as 7.6 |
| History and trend | **new** — nothing is persisted today |
| Scheduled runs | the Phase 3 job scheduler; a natural first job for the substrate |

**Two guardrails on the guardrail tester.** A live-model run is dozens of model calls: route it
through the same governance context and budget enforcer as `/query`, show an estimate before
the button, and keep the offline run as the default. A red-team run that silently drains the
demo credits on the morning of the 30th is a self-inflicted wound. And it is **devops only,
never tenant-facing**.

**The honest framing on the report:** the offline block rate is *"our deterministic signatures
blocked N of M"*, not *"Aegis blocks N%"*. The battery's own docstring is already careful about
this. Keep the distinction on the screen.

### 7.14 — The live cache surface (0.5d)

Replace the hard-coded `SPECS` array at `web/src/components/cache/CacheView.tsx:44` with a
fetch of the `cache_hit_rate` that `GET /metrics` already returns, plus hits/misses/evictions
and the honest empty state when nothing has been cached yet. Half a day, and it removes the
last static-prose-on-a-measured-label surface in the repo.

### 7.15 — The admin forecast page: two panels, two `Source:` lines (1.5d)

**The label is load-bearing.** Two panels, two sources, two different models:

```
┌─ Platform forecast ─────────────────────────────────────────────┐
│  Spend / calls forward, conformal band, ACHIEVED coverage,      │
│  burn-down against the cap, exhaustion date.  [Download CSV]    │
│  Source: usage_ledger · univariate · statsforecast              │
├─ Model explainability ──────────────────────────────────────────┤
│  Features:  [x] a  [x] b  [ ] c  [x] d        [ Retrain ]       │
│  SHAP waterfall · MAE / interval coverage · Δ vs the full model  │
│  Source: TrustworthyModel · XGBoost ensemble + MAPIE            │
└─────────────────────────────────────────────────────────────────┘
```

- **The top panel is a UI change, not a backend one.** `GET /forecast/usage?tenant_id=` with a
  null tenant is already the platform aggregate and `_scope_tenant` already permits exactly
  that (`routes.py:2966`). Today `ForecastView` is mounted for both `admin` and `client` with
  a role prop; make the admin variant default to the platform aggregate and add a tenant
  selector.
- **`POST /ml/experiment`** → `{features: [...]}` → trains a spine on that subset and returns
  its model card, held-out metrics, SHAP attributions **and the delta against the currently
  served model**. That delta is the real answer to "how does the forecast look if I delete a
  feature", and it is a measured number.
- **Run it as a job on the Phase 3 substrate — submit, poll, render.** Training is CPU work;
  `POST /ml/explain` already runs *prediction* through `asyncio.to_thread` (`routes.py:1010`)
  for exactly this reason, and a training call needs more than that. Do not retrain inline.
- **Never overwrite the served artifact.** An experiment is an experiment; promotion is a
  separate audited action on the `POST /ops/release` gate pattern.
- `ShapWaterfall.tsx` already exists and `MLOpsView` already renders a SHAP explanation. The
  chart is not new work.

**A jury member who asks "is the SHAP explaining the spend forecast?" must get "no — the
forecast is a univariate time series, the SHAP is the supervised spine, here is what each one
is." Merging them into one visual would be the single most damaging thing in this phase.**

### 7.16 — The fifteen controls a tenant must never have (0.5d)

Each row is a **catalogue entry** under Phase 3 §3.7 (`writable_by` / `merge`), so this is
executable configuration rather than a paragraph in a document nobody re-reads.

| # | Control | Enforcement |
|---|---|---|
| 1 | Weakening any platform guardrail — PII redaction, injection screening, content safety, a lowered threshold | `merge: tighten_only`. The resolver **cannot compute** a weaker value |
| 2 | Raising their own budget cap | `writable_by: platform` at `scope_type='tenant'`. A tenant admin may set sub-caps on their own users, always ≤ the tenant cap |
| 3 | Raising `gate_min_risk` | `tighten_only`. It is the **only** gating signal (`aegis/src/aegis/agent/deps.py:135`); raising it removes human oversight from destructive tool calls. A tenant may *lower* it |
| 4 | Free-form SQL or any database browse | `readable_by: platform` (7.9) |
| 5 | Reading platform-scoped audit rows, or another tenant's anything | `_scope_tenant` on every read, with a test per new endpoint |
| 6 | A model outside the platform's allowed deployments | The picker enumerates `routing_table()`; **the server validates the override against that set**. A UI enum is not enforcement |
| 7 | Overriding the model role used by the guardrails | `forbid`. The guardrail completer is deliberately separate from the answer completer; pointing the injection classifier at a model of their choosing disables it without appearing to |
| 8 | Disabling audit logging, or exporting without being audited | `audit: always`; no key exists to turn it off |
| 9 | Unbounded fan-out (`max_parallel_agents`, `max_plan_iterations`, `agentic_retrieval_max_rounds`) | Platform cap clamps, and the `routing` event reports `decided_by='platform_cap'` so the clamp is **visible, not silent** |
| 10 | Widening a tool allowlist | `effective = platform ∩ persona ∩ tenant ∩ user`, through the one `is_allowed` function |
| 11 | Uploading memory or documents that bypass the input rails | `check_input` server-side before storage (7.5b). **A stored prompt injection is the most patient attack in the product** |
| 12 | Setting their own `subject_id`, `tenant_id` or persona | Derived server-side from `AuthContext` (`routes.py:908`). The isolation key is never client-supplied |
| 13 | Initiating a live-model red-team run | `require_devops` + platform admin. Offline reports may be *readable* by a tenant admin |
| 14 | Editing the prompt floor | The tenant writes a version; the platform composes the floor underneath (7.7) |
| 15 | Granting themselves `platform_admin`, or creating users in another tenant | Already enforced — `admin_create_user` pins a tenant admin to its own tenant with a 403 (`routes.py:1263`). Keep the client-side self-lockout guard in `RolesAccess.tsx` too |

**The meta-rule: every one is enforced server-side, in the resolver or the guard, and the UI
merely reflects it.** A disabled control in a form is a hint. Rows 6 and 9 are exactly the kind
that get "enforced" by a dropdown and bypassed by a curl. **Test each one with a request a UI
would never send.**

---

## What is cut, and named so nobody starts one

- **Secret / key rotation UI.** Real work, zero demo surface, and a web form handling API keys
  is a new attack surface for a problem `.env` already solves on a single-operator deployment.
- **The full `tenant_roles` + permission hierarchy.** Replaced by 7.8.
- **A tenant-facing free choice of model.** Tenants pick from the platform's *allowed*
  deployments, not an open field.
- **Per-user notification / email / theme settings and profile pages.**
- **A separate "reports" subsystem.** 7.12 is one module streaming CSV. Keep it that way.
- **Act-as-tenant support impersonation.** Genuinely useful, and an audit-and-consent problem
  that will not get the thought it needs before 30 Aug.

**Not** cut, though it is tempting: **document lifecycle (list / delete / re-ingest)**. Without
it a tenant who uploads the wrong PDF on demo day has no recovery, and "we'll fix it in the
database" is precisely what this whole phase exists to eliminate.

## If time runs short

Cut order, back to front: **7.15's experiment panel** (keep the two-panel forecast with its
labels — that is the honest part) → **7.9's free-form box** (keep the path and the browser) →
**7.14** → **7.13's history** → **7.12's forecast CSV**.

**7.1 and 7.2 are firm.** Without them the tenant admin has neither a home nor the screen the
phase's central decision lives on.

---

## Definition of done

- [ ] A tenant-owned approval is decidable by that tenant's admin and **not** by the Aegis
      platform admin, who sees it read-only with a stated reason on the same screen.
- [ ] A client can see the fate of a gate they raised.
- [ ] The seed and the approvals guard changed in the same commit, and the gate flow has been
      rehearsed since.
- [ ] `tenant_admin` lands on its own portal; the route-coverage test covers all five portals.
- [ ] A platform admin creates a tenant, a user and a budget through the UI, and that user
      logs in. A duplicate username and a cross-tenant create both show the server's real
      message.
- [ ] `PUT /harness/config` writes a knob, clamped to the declared bounds, audited — and the
      same `SettingsForm` component renders the tenant, policy and seats screens.
- [ ] Every settings screen shows `(value, source)`.
- [ ] The `memory` stream event names the recalled facts; `POST /memory/facts` stores a fact
      **only after `check_input`**, with `origin=USER` in `GET /memory/writes`; memory is
      mounted on `tenant_admin` and `client`.
- [ ] A tenant recall pass returns tenant facts merged under user facts, tested for precedence.
- [ ] `GET /guardrails/policy` returns the effective rail stack; a tenant can add a template
      rail and **cannot** resolve a weaker value for any `tighten_only` key — tested per key.
- [ ] Two tenants have different active prompts simultaneously, and `GET /ops/prompts` as
      tenant A never returns tenant B's versions.
- [ ] Six seat toggles narrow capability and provably cannot widen it.
- [ ] The SQL console: `INSERT`/`UPDATE`/`DELETE`/`CREATE` all fail with **`permission
      denied`**, not a read-only-transaction error; `SELECT 1; SELECT 2;` raises at the driver;
      `RESET ROLE` leaves `current_user` unchanged; `SELECT *` on `users` and a
      `WHERE password_hash …` predicate are both refused and the column is absent from
      `information_schema.columns`; every query leaves a `db.query.execute` audit row.
- [ ] The tenant-impersonation toggle makes rows disappear on screen.
- [ ] `/readyz` returns 503 with per-component detail when Postgres is stopped; `probe_neo4j`
      exists; killing Neo4j turns the component `down` **and puts a degradation banner on the
      answer**; a timed-out probe reads `unknown`, never `down`.
- [ ] Every `ComponentHealth` row carries `evidence`.
- [ ] The audit page filters by tenant, actor, action and date; a tenant admin cannot widen
      scope through a query parameter.
- [ ] Four CSVs download, each carries its caveat columns, and each download is the newest
      audit row.
- [ ] A red-team run can select categories, adjust thresholds and target the live model layer
      behind a budget pre-flight; history persists and trends.
- [ ] The Cache page fetches a real number.
- [ ] The forecast page renders two panels with two `Source:` lines; `POST /ml/experiment`
      runs as a job and never overwrites the served artifact.
- [ ] All fifteen forbidden controls are catalogue entries with a test that sends the request
      a UI would never send.
- [ ] Full suites green, ruff clean, `next build` green.

## Demo at the end of this phase

Log in as *Acme*'s tenant admin. Raise a HIGH-risk action — it lands in **Acme's** inbox.
Switch to the Aegis platform admin: the same gate is visible, the decide buttons are disabled,
and the reason says why. Decide it back in Acme's inbox.

Then, from the dashboard and with no code change: tighten Acme's guardrails one notch and watch
the platform default stay visible beside the tenant value; try to *weaken* one and watch the
resolver refuse; give a user a Support Lead seat that can approve but cannot upload.

Then open the DB page as the platform admin, browse `users` — `password_hash` is not in the
column list at all — bind the scope to Acme, and watch the other tenant's rows vanish from the
result set.

Finish on Health: stop Neo4j, watch the component go red with its evidence string, and watch
the *answer* carry the banner saying the graph arm was unavailable.

## Risks

**7.1 is the phase's critical path and it is easy to under-scope.** Moving approval ownership
without the inbox — or without the seed — leaves the demo unable to approve anything at all.
Either both, or neither.

**The per-tenant surfaces will look correct with one tenant.** The LLMOps cache bug cannot even
manifest with one tenant in the database. Build nothing per-tenant before the Phase 3 seed and
the 7.7 cache fix; this project has been burned by exactly that class of defect more than once.

**The settings catalogue can sprawl into seventeen screens by accident.** The rule that saves
it is 7.4: one component, rendered from the catalogue. The first bespoke settings form is the
one that ends the "0 code change" claim.

**The SQL console is the highest-risk item in the phase and the ordering is the mitigation.**
Building the box before the path ships the attack surface before the control. If the schedule
bites, the box is the cut — the path and the browser are the demo.

**A red-team run against the live model layer can drain the balance.** It must be budgeted and
pre-flighted before the button exists, not after.

**Two panels can quietly become one chart.** Under time pressure, merging the forecast and the
SHAP panel looks like a simplification. It is the one change in this phase that would make the
product dishonest.
