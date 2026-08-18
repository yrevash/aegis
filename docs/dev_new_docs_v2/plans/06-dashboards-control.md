# Plan 06 — Dashboards, control, and the tenant-facing capabilities

**Research output for requirements C1–C3 and D of [`01-V2-ADDITIONS.md`](../01-V2-ADDITIONS.md).**
The per-profile control matrix (C3) · Grok-style agent selection (C1) · multi-agent visibility
as a *control* (C2) · and the seven tenant-facing capabilities in D that have no plan yet.

This is research, not a phase file. It ends with a dependency-ordered sequence that
[`phase-05-console.md`](../phase-06-console.md) and [`phase-06-admin-surfaces.md`](../phase-07-control-planes.md)
absorb. Where those two already own a task, this document says so and stops.

---

## How to read the claims in this document

Same convention as [`plans/04-enterprise-substrate.md`](04-enterprise-substrate.md), because it
is the standard for this project now:

| Marker | Means |
|---|---|
| **[SOURCE]** | Read in this repository. File and line are given. If a claim about Aegis's behaviour is *not* marked, I did not read it and you should not trust it. |
| **[MEASURED]** | Run against the live PostgreSQL `taif` database at `localhost:5432` as `yrevash`, read-only (`SELECT` and `information_schema` only — no DDL). Every probe is in Appendix A and re-runnable. |
| **[EVIDENCE]** | External. Cited. Where the evidence is thin I say so rather than rounding up. |

Nothing here is asserted from memory about how Aegis behaves.

---

## The recommendations, up front

| # | Question | Recommendation |
|---|---|---|
| **C3** | How does "every profile can do everything from their dashboard" become real without N bespoke settings screens? | **One settings mechanism, one declared catalogue.** A `settings` table with three scopes (`platform` / `tenant` / `user`) and a **`SettingSpec` catalogue** that declares every controllable knob: key, type, default, bounds, who may write it, who may read it, and its **merge rule**. Screens are rendered *from* the catalogue. This is the pattern `harness_config()` / `_KNOB_SPECS` already proves for agent knobs (`aegis/src/aegis/agent/harness.py:54` **[SOURCE]**) — generalise it rather than inventing a second one. §1.7. |
| **C3** | Biggest structural gap? | **The `client` role has no console.** `ROLE_SECTIONS.client = ['dashboard','savings','forecast','risk','simulation']` (`web/src/lib/portal.ts:282` **[SOURCE]**) — the tenant end-user, who is the whole point of the product, cannot ask the agent a question. §1.5. |
| **C3** | Second biggest? | **There is no tenant-admin portal.** The browser only ever learns the *coarse* role: `LoginResponse` carries `role`, `token`, `tenant_id` and **not** `fine_role` (`backend/src/app/api/schemas.py:516-525` **[SOURCE]**). Phase 6's "platform admin sees the tenant's gate read-only, tenant admin decides it" cannot be rendered until the fine role is on the wire. §1.7. |
| **C1** | Agent selection design | **Three orthogonal axes in one composer cluster: `mode` (Auto / Fast / Deep / Team / Custom) · `model` · `tools`.** Mode is a *strategy*, not a model — matching what Grok, ChatGPT and Claude all converged on **[EVIDENCE]**, §2.1. Manual **overrides** the Phase-4 classifier and the override is *recorded on the routing event*, so the screen always says who decided. §2.4. |
| **C1** | Manual vs automatic budget policy | **Auto-escalation is free; manual escalation is charged and pre-flighted.** OpenAI ships exactly this split (auto Instant→Thinking does not consume the Thinking quota; a manual pick does) **[EVIDENCE]**. It is the only rule that makes a user-facing TEAM button safe against a $100 credit ceiling. §2.4. |
| **C1** | Pinning agents/tools | **Pinning may only ever narrow.** The effective tool set is `platform_allow ∩ persona_allow ∩ tenant_allow ∩ user_pin`. Phase 4 already states this invariant for sub-agents (`phase-04-multi-agent.md`, task 4.2); make it the *same* code path for the UI pin so there is one intersection, not two. §2.5. |
| **C2** | What "clear options of tools" means | A tool row is a **control** (allow / deny / require-approval), not a legend. The three-state toggle maps onto the two mechanisms that already exist — the persona allowlist (`backend/src/app/adapter/tools.py::is_allowed`) and `gate_min_risk` (`aegis/src/aegis/agent/deps.py:135` **[SOURCE]**). No third mechanism. §3.3. |
| **C2** | The missing control | **Cancel.** Four agents burning tokens with no stop button is a live-demo hazard and an enterprise blocker. It is one cooperative token checked between agent steps. §3.4. |
| **D1** | Per-tenant memory | **Highest-value item in D — build it first.** 6 of the 8 endpoints already exist (`GET /memory/{facts,profile,sessions,writes,recall_debug}`, `DELETE /memory/facts/{id}`, `POST /memory/forget` **[SOURCE]**); what is missing is a *write* endpoint, a UI on any portal but `ai_team`, and — the part that matters — **memory identities on the `memory` stream event**, which today carries only counts (`schemas.py:361-387` **[SOURCE]**). §4.1. |
| **D2** | Per-tenant guardrails | **A tightening-only lattice, never a form.** A tenant's guardrail settings merge with the platform's by `max(strictness)`, computed server-side; a tenant literally cannot express "weaker". The platform defaults are exposed **read-only** at a new `GET /guardrails/policy`. Today the pipeline is a process-wide singleton (`backend/src/app/guardrails/__init__.py:82` **[SOURCE]**) — that is the one real piece of work. §4.2 and §5. |
| **D3** | Per-tenant LLMOps | **Fix the cache bug before building any surface.** `_ACTIVE_CACHE` is keyed by prompt key alone, so two tenants cannot have different active prompts. Already logged in the backlog; restating it because *no per-tenant prompt screen is truthful until it is fixed*. §4.3. |
| **D4** | Tenant sub-roles | **Cut the hierarchy. Ship named seats instead.** A `tenant_roles` table + permission catalogue is ~4d and needs schema the additive reconciler refuses. A closed set of tenant-scoped capability toggles in the *same* settings table gets ~90% of it with zero new mechanisms. §4.4. |
| **D5** | Reports | **Phase 6.4 already owns audit/tenant/budget CSV. This plan adds only the forecast report** and one rule Phase 6 got right and must not lose: every export writes its own `report.export` audit row. §4.5. |
| **D6** | Red-teaming the infra profile controls | The battery is real and already parameterised — `run_redteam(check, *, completer, battery, thresholds)` (`aegis/src/aegis/redteam/runner.py:305` **[SOURCE]**). The endpoint hard-codes every argument (`routes.py:2631` **[SOURCE]**). **Expose the parameters that already exist**, add history, and make "run against the live model layer" an explicit, budgeted, devops-only choice. §4.6. |
| **D7** | Admin forecast + SHAP + feature selection | **Two different models, and the plan must not blur them.** The platform forecast is a *univariate* series (`statsforecast` + conformal) — SHAP does not apply to it. The SHAP/feature story belongs to `TrustworthyModel`, which already takes an explicit feature list and can retrain (`aegis/src/aegis/ml/model.py:290` **[SOURCE]**). Ship them as **one page with two panels and an honest label**, not one chart pretending to be both. §4.7. |

---

# Part 0 — What a person can actually *do* from a screen today

Everything in this part was read or measured. It is the baseline the matrix in Part 1 scores against.

### 0.1 Four portals, twenty-eight sections, one nav catalogue

`web/src/lib/portal.ts` **[SOURCE]** is the single source of truth for navigation. `SECTIONS`
(line 69) declares 28 sections; `ROLE_SECTIONS` (line 278) assigns them:

```ts
admin:   ['dashboard','forecast','governance','audit','roles']                       // 5
ai_team: ['console','harness','mlops','llmops','evals','tokenopt','memory','rag',
          'graph','cache','voice','vision','guardrails','simulation']                // 14
devops:  ['dashboard','stack','patch','security','redteam','latency','audit']        // 7
client:  ['dashboard','savings','forecast','risk','simulation']                      // 5
```

`web/src/app/app/[role]/[section]/page.tsx` **[SOURCE]** maps each slug to a mount; an
unmatched slug is a 404. So the nav catalogue *is* the control surface. Every gap in Part 1
is ultimately an edit to those four arrays plus the component behind it.

### 0.2 Fifty-seven endpoints, and their guards

I enumerated every `@router.*` in `backend/src/app/api/routes.py` with the `Depends(...)`
guard resolved (Appendix A, probe 1) **[SOURCE]**. There are **57**, and this is the only
router in the backend — `grep -rl 'APIRouter' backend/src/app` returns `routes.py` alone.

Distribution by guard:

| Guard | Count | Notes |
|---|---|---|
| `require_auth` (any logged-in role) | 17 | includes all 8 memory endpoints and `/query` |
| `require_admin_or_ai_team` | 7 | the LLMOps / harness block |
| `require_tenant_admin` (admits platform_admin too) | 8 | users, budgets, usage, governance dashboard, both spend forecasts |
| `require_admin_or_devops` | 6 | stack, patch, audit, security, latency, redteam |
| `require_admin` (either tier) | 4 | all three approval endpoints + release decide |
| `require_platform_admin` | 2 | `GET`/`POST /admin/tenants` — **the only two** |
| public / other | 13 | login, health, about, capabilities, public metrics, … |

Two things fall straight out:

1. **Platform-admin-only surface is two endpoints.** Everything else an "Aegis operator" does
   is shared with tenant admins through `require_tenant_admin`, which admits both tiers
   (`routes.py:412` **[SOURCE]**) and then narrows by `_scope_tenant` (`routes.py:436`).
   That is a defensible design — but it means the *distinction the v2 doc asks for*
   ("aegis admin checks their stuff not tenant admin stuff") is not expressible in the UI
   today, because the UI never learns which tier it is.
2. **Memory is the most open subsystem in the product and the least visible.** Eight
   endpoints on `require_auth`, mounted on exactly one portal (`ai_team`).

### 0.3 The whole product exposes eleven write actions from a screen

Grepping every API-client function against its call sites in `web/src` **[SOURCE]**:

| Action | Screen | Role that can reach it |
|---|---|---|
| `assignUserRole` | Roles & Access | admin |
| `postApproval` (in-run gate decision) | console, via `liveTransport.ts:59` | ai_team (the only portal with a console) |
| `postOpsDiagnose` / `postOpsRelease` | LLMOps → DiagnosePanel | ai_team |
| `postOpsRollback` / `postOpsReleaseDecision` | LLMOps → ReleaseGate | ai_team |
| `runRedteam` | Red-team, Guardrails | devops, ai_team |
| `checkPatches` | Patch Check | devops |
| `analyseImage` | Vision | ai_team |
| `transcribeVoice` | Voice | ai_team |
| `mlExplain` | MLOps | ai_team |
| `POST /query` | Console | ai_team |

**`createBudget` is defined in `web/src/lib/api/client.ts:195` and called from nowhere in
`web/src`** **[SOURCE]** — dead code, and the reason there is no budget form.
`createUser` and `createTenant` do not exist in the client at all, which
`phase-06-admin-surfaces.md` §1 already documents.

**The `client` role can perform zero write actions.** Not one.

### 0.4 The live database is empty — and that changes what "works today" means

Probe 2, against `taif` **[MEASURED]**:

```
users              0        audit_log          46
tenants            0        prompt_versions     0
budgets            0        usage_ledger        0
approvals          0        memory_fact         0
chunks             0
```

Consequences that must be stated before anyone claims a surface "works":

- **Every login today is a `_DEMO_USERS` fallback** (`routes.py:205`, consulted only for
  usernames with no real row, `routes.py:225` **[SOURCE]**). The real `users`-table path has
  never been exercised against this database.
- Every governance figure on every dashboard is currently an honest zero. That is the
  `BackendGate` / empty-state design working (`web/src/components/shared/BackendGate.tsx:10-17`
  **[SOURCE]**) — but it also means **no per-tenant surface in this plan has ever run against
  more than one tenant**, because there has never been one tenant.
- `prompt_versions` is empty, so the per-tenant LLMOps defect in §4.3 has never had a chance
  to manifest. It will manifest the first day two tenants exist.

**Recommendation, and it is not optional: a seed script that creates two real tenants with
real users, real budgets and real prompt versions is a prerequisite for testing anything in
this document.** Not fixtures in the browser — rows in Postgres. Without it, "per-tenant"
is untested by construction.

### 0.5 Thirteen tables carry `tenant_id`. The corpus does not.

Probe 3 **[MEASURED]** — tables in `taif` with a `tenant_id` column:

```
approvals, audit_log, budgets, eval_results, memory_consolidation_job,
memory_fact, memory_message, memory_profile, memory_session,
memory_write_log, prompt_versions, usage_ledger, users
```

Exactly the 13 registered in `aegis/src/aegis/governance/rls.py:103-121` **[SOURCE]**.
Consistent — good.

But `chunks` has columns `id, doc_id, persona, content, embedding, meta` and **no
`tenant_id`** **[MEASURED]**, matching `backend/src/app/data/models.py:141-156` **[SOURCE]**.
The ten `lightrag_*` tables have none either.

Retrieval isolation is therefore **not** a database property. It is:

- `RetrievalScope(tenant_id, persona, corpus_version)` threaded as a required argument
  (`aegis/src/aegis/retrieval/types.py:60-88` **[SOURCE]**), reconciled against the request's
  governance context by `_governed_scope` (`backend/src/app/retrieval/pipeline.py:105`), which
  raises `TenantScopeMismatch` rather than serving the wrong tenant; plus
- a tenant tag carried inside LightRAG's `file_path` field and **post-filtered on the way
  out** — `lightrag_backend.py:389-397` **[SOURCE]** states this plainly in its own docstring:
  *"the search space is still shared and foreign rows are discarded on the way out"*.

That is honest and it is documented, and it is also the sharpest constraint on Part 1's
"tenant uploads and manages their own documents" row: **a document-management screen is a
screen over a store with no row-level tenancy.** Two implications the plan must carry:

1. Phase 3's task 3.7 (corpus-wide BM25 on Postgres FTS) will query `chunks` directly. That
   query has **no tenant predicate available**. Add `tenant_id` to `chunks` and register it in
   `_TENANT_SCOPED_TABLES` *in the same change*, or the keyword arm becomes the leak Phase 1
   just closed.
2. Plan 04's admin DB-query page (A1) must treat `chunks` and `lightrag_*` as **content
   tables with no tenant column** — the schema browser can list them; a cross-tenant read of
   them is not prevented by RLS, only by the browser's own allowlist.

### 0.6 The seams that make all of Part 1 cheap

Four things already in the code do most of the work, and none of them need redesigning:

| Seam | Where | Why it matters here |
|---|---|---|
| `get_agent_deps()` builds `AgentDeps.default()` **per request** | `routes.py:658` **[SOURCE]** | Per-tenant / per-user agent configuration is a dependency change, not a graph change. No signature churn. |
| `set_governance_context` is bound inside the `/query` streaming task | `routes.py:915-919` **[SOURCE]** | A contextvar the whole graph can read. Phase 5 already uses it for model override; agent mode rides the same seam. |
| `harness_config()` → `_KNOB_SPECS` renders an editable form descriptor (key, type, value, default, doc, bounds) | `aegis/src/aegis/agent/harness.py:54,142`; `GET /harness/config` at `routes.py:2506` **[SOURCE]** | A **catalogue-driven settings UI already exists** for one subsystem. It is read-only. Generalising it is the whole of §1.7. |
| `Guardrails.__init__` takes injected `input_rails` / `output_rails` / thresholds | `aegis/src/aegis/guardrails/pipeline.py:118-135` **[SOURCE]** | Per-tenant guardrails need a per-request *construction*, not a new engine. §4.2. |

---

# Part 1 — C3, the per-profile control matrix

**This is the spine.** For each role: what that person is supposed to be able to do, the
screen it lives on, the endpoint behind it, and whether that endpoint exists.

### 1.0 Method, and the rule that decides inclusion

A row earns its place only if I can answer: **"what can this person not do without it?"**
Rows that fail that test are named in §1.9 and cut.

Status legend:

| | |
|---|---|
| **✅** | Endpoint exists **and** a screen calls it. Verified in source both ways. |
| **⚠️** | Endpoint exists, **no screen calls it**. Frontend-only work. |
| **◐** | Partly there — exists but wrong scope, wrong role, or read-only where a write is needed. |
| **❌** | Nothing. New endpoint and new screen. |
| **📋** | Already owned by a written plan — Phase 3/4/5/6 or plan 04/05. Listed for completeness; **do not re-plan.** |

---

### 1.1 Platform admin — the Aegis operator

Portal today: `dashboard · forecast · governance · audit · roles`.

| # | What they must be able to do | Screen | Endpoint | Status |
|---|---|---|---|---|
| P1 | Create a tenant | Governance → new form | `POST /admin/tenants` `routes.py:1232` | ⚠️ 📋 Phase 6.1 |
| P2 | List / inspect tenants | Governance | `GET /admin/tenants` | ✅ |
| P3 | Suspend or offboard a tenant | Governance | — | ❌ `TenantStatus.SUSPENDED` exists and is enforced nowhere (backlog, confirmed) |
| P4 | Create the first `tenant_admin` for a tenant | Governance → new form | `POST /admin/users` `routes.py:1262` | ⚠️ 📋 Phase 6.1 |
| P5 | Deactivate a user / force a password reset | Roles & Access | — | ❌ `AdminUserRow.is_active` exists as a field; no endpoint writes it |
| P6 | Set a tenant's budget caps | Governance → new form | `POST /admin/budgets` `routes.py:1411` | ⚠️ `createBudget` is dead client code |
| P7 | See platform-wide spend and usage | Overview, Governance | `GET /admin/usage`, `GET /governance/dashboard` | ✅ |
| P8 | Audit across all tenants, filtered | Audit | `GET /audit` | ◐ 📋 Phase 6.3 — `limit` only today (`routes.py:1090`) |
| P9 | Decide **Aegis's own** gates; **see, not decide,** a tenant's | *(no screen)* | `GET /approvals`, `POST /approvals/{id}/decision` | ◐ 📋 Phase 6.2 for the guard. **The inbox has no screen at all** — `ApprovalCard` is mounted only in `SimulationView` and `MoneyShotConsole` **[SOURCE]** |
| P10 | Platform-level forecast, not a tenant's | Forecast | `GET /forecast/usage?tenant_id=` (null ⇒ platform aggregate) | ✅ backend, ◐ UI — see §4.7 |
| P11 | SHAP + interactive feature selection on the platform model | Forecast | — | ❌ §4.7 |
| P12 | Download forecast / audit / tenant / budget reports | every admin screen | — | ❌ 📋 Phase 6.4 owns three of four; forecast is §4.5 |
| P13 | Browse any data without psql | new DB section | — | 📋 **plan 04 §4** — do not re-plan |
| P14 | Ask Aegis about Aegis (internal console + MCP client) | new Console section on the admin portal | `POST /query` exists; MCP server exists (`backend/src/app/mcp/server.py`, stdio) | ◐ The admin portal has **no console section** at all |
| P15 | See per-component pipeline health | new Health section | — | 📋 **plan 04 §3** |
| P16 | Read *and set* the platform guardrail defaults | new Policy section | — | ❌ §4.2. Today the only guardrail screen is on `ai_team` and reads `GET /security/posture` |
| P17 | Set platform model-routing defaults | Governance | `routing_table()` is read-only via `GET /metrics` | ❌ write path |
| P18 | Set platform agent-selection defaults and **caps** (max fan-out, who may pick TEAM) | new Policy section | — | ❌ §2.6 |
| P19 | Create sub-roles inside a tenant | Roles & Access | — | ❌ §4.4 |
| P20 | See and control jobs / queues / schedules | new Jobs section | — | 📋 **plan 04 §1** |
| P21 | Cancel a running query | anywhere a run is visible | — | ❌ §3.4 |
| P22 | Export or delete one tenant's data on request (DSAR) | Governance | `POST /memory/forget` covers memory only | ◐ |

**Score: 3 of 22 fully working.** Four are already planned elsewhere. **Eleven are genuinely
unplanned**, and P9, P14 and P16 are the ones that change what the role *is* rather than
adding a convenience.

---

### 1.2 Tenant admin — the customer's own administrator

**Portal today: none.** There is no `tenant_admin` entry in `ROLES`
(`web/src/lib/portal.ts:50` **[SOURCE]**), and `LoginResponse` never sends the fine role
(`schemas.py:516-525` **[SOURCE]**). A tenant admin logs in and lands on the *platform*
admin portal, scoped by `_scope_tenant` server-side. The scoping is correct; the navigation
is a lie of omission — it offers Aegis-operator framing to a customer.

| # | What they must be able to do | Screen | Endpoint | Status |
|---|---|---|---|---|
| T1 | Create users inside their own tenant | Team | `POST /admin/users` (tenant pinned server-side, `routes.py:1263`) | ⚠️ 📋 Phase 6.1 |
| T2 | Assign a role to their users | Team | `POST /admin/users/{id}/role` | ✅ (shares the platform screen) |
| T3 | Define sub-roles / seats inside their tenant | Team | — | ❌ §4.4 |
| T4 | See **and decide** their tenant's approvals | Approvals | `GET /approvals`, `POST /approvals/{id}/decision` | ◐ 📋 Phase 6.2 for ownership — **but no inbox screen exists to move ownership *to*.** Phase 6.2 is not shippable without one |
| T5 | See their tenant's budget, spend, remaining | Governance | `GET /admin/budgets`, `GET /governance/dashboard` | ✅ |
| T6 | Set sub-caps on their own users | Governance | `POST /admin/budgets` (scope_type='user') | ⚠️ no form. **Must not be able to raise their own tenant cap** — §5 |
| T7 | See their tenant's audit trail | Audit | `GET /audit` (`_scope_tenant` already narrows) | ✅ read; ◐ filters 📋 Phase 6.3 |
| T8 | Upload documents, watch them ingest | Knowledge | — | 📋 **Phase 3, tasks 3.5 + 3.12** |
| T9 | List / delete / re-ingest their documents | Knowledge | — | ❌ Phase 3 plans *upload*, not *management*. §1.9 |
| T10 | Upload / view / delete tenant memory | Memory | `GET /memory/*`, `DELETE /memory/facts/{id}`, `POST /memory/forget` | ⚠️ reads exist and are mounted only on `ai_team`; **the write endpoint does not exist**. §4.1 |
| T11 | See which memories a run referenced | Console | `memory` stream event exists | ◐ carries **counts only** (`schemas.py:361-387`). §4.1 |
| T12 | Add their own guardrails | Policy | — | ❌ §4.2 |
| T13 | **Read** the platform's default guardrails | Policy | — | ❌ §4.2 — the user asked for this by name |
| T14 | See / manage their own prompt versions and eval deltas | LLMOps | `GET /ops/prompts`, `/ops/prompts/active`, `/ops/evals` | ◐ endpoints exist but **`list_versions` takes no `tenant_id`** (`aegis/src/aegis/ops/registry.py:169` **[SOURCE]**) and the active cache is not tenant-keyed. §4.3 |
| T15 | Set tenant defaults: model, agent mode, tool allowlist, bounded harness knobs | Settings | — | ❌ §1.7 + §2.6 |
| T16 | Download tenant-scoped reports | anywhere | — | 📋 Phase 6.4 |
| T17 | Their own console / chat | Console | `POST /query` | ◐ exists; **not on any admin portal** |
| T18 | Their tenant's forecast and burn-down | Forecast | `GET /forecast/usage`, `GET /forecast/budget` | ✅ |

**Score: 4 of 18.** And the role has no home. **T4 is the sharpest: Phase 6.2 moves approvals
ownership to the tenant admin, and there is no approvals screen in the entire frontend.**
Landing 6.2 without building an inbox moves a decision to a person who has nowhere to make it.

---

### 1.3 AI team — the people who build and tune the agent

Portal today is the richest by far: 14 sections. The gaps are all *write* gaps.

| # | What they must be able to do | Screen | Endpoint | Status |
|---|---|---|---|---|
| A1 | Run a query with full glass-box trace | Console | `POST /query` | ✅ 📋 Phase 5 rebuilds it |
| A2 | See every knob the graph reads | Harness | `GET /harness/config` | ✅ |
| A3 | **Change** a knob | Harness | — | ❌ **read-only by design today** (`routes.py:2506` docstring says so). This is the single highest-leverage write gap for this role — the descriptors already carry type, default and bounds |
| A4 | Draft → eval → release → rollback a prompt | LLMOps | `POST /ops/{diagnose,release,rollback}`, `/ops/releases/{id}/decide` | ✅ |
| A5 | Do all of that **per tenant** | LLMOps | — | ◐ §4.3 |
| A6 | Select a per-user or per-tenant *harness session* and inspect it | Harness | — | ❌ the user asked for this explicitly. Needs `run_events` — 📋 plan 04 §2 / backlog |
| A7 | Run the eval regression gate on demand | Evals | `GET /evals/report` | ◐ **read-only; no run trigger.** `aegis.evals.harness` exists |
| A8 | Inspect and forget memory | Memory | `GET /memory/*`, `DELETE`, `POST /memory/forget` | ✅ |
| A9 | Tune retrieval (arms, rerank, thresholds) | RAG | `GET /gateway/optimization`, `/harness/config` | ◐ read-only |
| A10 | Inspect guardrail rails and verdicts | Guardrails | `GET /security/posture`, `POST /redteam/run` | ✅ |
| A11 | Run the red-team battery | Guardrails, Red-team | `POST /redteam/run` | ✅ but unparameterised — §4.6 |
| A12 | Model card + SHAP for the ML spine | MLOps | `GET /ml/model-card`, `POST /ml/explain` | ✅ |
| A13 | Author and attach **skills** to agents | new Skills section | — | ❌ skills exist as adapter markdown (`backend/src/app/adapter/skills/*.md`, 2 files **[SOURCE]**); no authoring surface. 📋 backlog ~6–8d |
| A14 | Edit the sub-agent roster (who exists, their prompts, their tools) | new Agents section | `agent_roster` is an injected callable in `AgentDeps` | ❌ §2.5 |
| A15 | See per-agent logs, tool calls, cost | Console → Agents tab | `agent_id` on events | 📋 Phase 4.4 + Phase 5.4 |
| A16 | Cancel a run | Console | — | ❌ §3.4 |

**Score: 7 of 16.** The pattern is unmistakable: **this role can see everything and change
almost nothing.** A3 alone — making `GET /harness/config` a `GET`+`PUT` pair over the
descriptors that already declare their own bounds — closes the largest single gap in the
whole matrix for the smallest amount of work, and it is the proof-of-concept for §1.7.

---

### 1.4 DevOps / infra — "sec + devops mix"

| # | What they must be able to do | Screen | Endpoint | Status |
|---|---|---|---|---|
| O1 | See every runtime and library version | Stack | `GET /stack` | ✅ |
| O2 | Check for outdated dependencies | Patch Check | `POST /stack/patch-check` | ✅ |
| O3 | See OWASP-Agentic posture | Security | `GET /security/posture` | ✅ |
| O4 | See latency p50/p95 | Latency | `GET /latency` | ◐ honest but **per-process and resets on restart** (`aegis/src/aegis/observability/latency.py`, per plan 04 §0.3) |
| O5 | Read the audit trail | Audit | `GET /audit` | ✅ |
| O6 | **Initiate and control** red-teaming | Red-team | `POST /redteam/run` | ◐ **runs one hard-coded configuration.** The runner already accepts `battery`, `completer`, `thresholds`; the route passes none. §4.6 |
| O7 | See red-team history and trend | Red-team | — | ❌ nothing is persisted; the audit row carries three summary numbers (`routes.py:2652`) |
| O8 | See component-level health / readiness | new Health | — | 📋 plan 04 §3 (`/readyz` is documented and does not exist) |
| O9 | See workers, queues, job failures, DLQ | new Jobs | — | 📋 plan 04 §1 |
| O10 | See the cache actually working | Cache | — | ❌ **`CacheView.tsx` renders a hard-coded `SPECS` array of configuration prose** — no fetch, no measurement **[SOURCE]**. `GET /metrics` already returns a real `cache_hit_rate`. This surface is the closest thing left in the repo to the fiction Phase 2 deleted, and the v2 doc names the cache specifically |
| O11 | Kill switch: suspend a tenant, drain, stop a run | Governance / Console | — | ❌ §3.4, P3 |
| O12 | Rotate keys / manage secrets | — | — | ❌ **deliberately out of scope.** Named in §1.9 so nobody starts it |

**Score: 5 of 12.** O10 is the one I would fix first here — not because it is hard, but
because a static configuration table on a page called "Cache" is exactly the class of thing
the last two phases were spent removing, and a jury member who clicks it and asks "is this
measured?" gets an answer nobody wants to give.

---

### 1.5 Client — the tenant's end user, and the reason the product exists

Portal today: `dashboard · savings · forecast · risk · simulation`. **No console.**

| # | What they must be able to do | Screen | Endpoint | Status |
|---|---|---|---|---|
| C1 | **Ask a question** | Console | `POST /query` (`require_auth` — the client role is already admitted) | ❌ **not on their portal.** One line in `ROLE_SECTIONS` |
| C2 | See sources and citations for the answer | Console → Answer tab | on the stream | 📋 Phase 5.4 |
| C3 | See their own budget and remaining spend | composer pill | — | ❌ 📋 Phase 5.7 (`GET /me/budget`). Today **no endpoint returns a client's own budget** — `/admin/budgets` and `/governance/dashboard` are both `require_tenant_admin` |
| C4 | Keep and revisit chat sessions | Console rail | — | 📋 Phase 5.2 |
| C5 | Upload a document and ask about it | Console composer | — | 📋 Phase 3.5 |
| C6 | Upload / view / delete **their own** memory | Memory | reads exist; write does not | ⚠️/❌ §4.1 |
| C7 | See which memories were used to answer | Console | `memory` event | ◐ counts only. §4.1 |
| C8 | Choose model / agent mode | composer | — | ❌ 📋 Phase 5.5 for model; §2 for mode |
| C9 | See their own value: savings, forecast, risk | Savings, Forecast, Risk | `GET /savings`, `/forecast/domain`, `/risk-map` | ✅ |
| C10 | See what happened to an action they triggered | Console / Approvals | `GET /approvals` is `require_admin` | ❌ a client cannot see the fate of their own request |
| C11 | Export their own data | Settings | — | ❌ |
| C12 | Upload an image / speak a query | composer | `POST /vision/analyse`, `/voice/transcribe` (`require_auth`) | ⚠️ endpoints open to them; screens are `ai_team`-only |

**Score: 1 of 12.**

**This is the headline finding of the whole document.** The role the platform is *for* has a
read-only value dashboard and no way to use the product. Six of these twelve are already
planned as Phase 3/5 work — but every one of those plans builds on `ConsoleMount`, which
today is reachable only from `/app/ai_team/console`. **Phase 5 must state the portal change
explicitly or it will ship a console the customer cannot open.**

---

### 1.6 The consolidated gap list, ranked

Ranked by *"what can a user not do without it"* × *cost*, not by how impressive it sounds.

| Rank | Gap | Roles | Cost | Why here |
|---|---|---|---|---|
| 1 | **Console on the `client` and `admin` portals** | client, both admins | ~1 h + Phase 5 | One array edit. Without it, four roles cannot use the product. Highest ratio in the document. |
| 2 | **`fine_role` on `LoginResponse` + a `tenant_admin` portal** | tenant admin | ~0.5 d | Phase 6.2's read-only-vs-decidable split is unrenderable without it. |
| 3 | **An approvals inbox screen** | both admins, client | ~0.75 d | Phase 6.2 moves ownership of a decision to a screen that does not exist. |
| 4 | **The settings mechanism + `SettingSpec` catalogue** | all | ~2 d | This *is* the "0 code change" goal. Everything below reuses it. §1.7 |
| 5 | **Harness knobs become writable (`PUT /harness/config`)** | ai_team, tenant admin | ~0.5 d on top of #4 | Descriptors already carry type/default/bounds. First consumer of #4, and the proof it works. |
| 6 | **Memory: a write endpoint, a screen on 3 more portals, identities on the `memory` event** | tenant admin, client, ai_team | ~1.5 d | §4.1. The user called memory the most important item. |
| 7 | **`GET /me/budget`** | client | ~0.25 d | 📋 Phase 5.7. Listed because a client seeing their own budget is table stakes and no endpoint returns it. |
| 8 | **Agent selection control (mode / model / tools)** | all | ~1 d on top of Phase 4 | §2 |
| 9 | **Cancel a run** | all | ~0.5 d | §3.4 |
| 10 | **Guardrail policy: read platform defaults, add tenant rules (tightening-only)** | tenant admin, platform admin | ~2 d | §4.2. Highest security risk in D. |
| 11 | **Per-tenant LLMOps — after the `_ACTIVE_CACHE` fix** | tenant admin, ai_team | 0.5 d fix + ~1.5 d surface | §4.3 |
| 12 | **Red-team parameterisation + history** | devops | ~1 d | §4.6 |
| 13 | **Live cache surface replacing the static one** | devops, ai_team | ~0.5 d | O10 |
| 14 | **Admin forecast: SHAP panel + feature selection + report** | platform admin | ~1.5 d | §4.7 |
| 15 | **Document management (list / delete / re-ingest)** | tenant admin | ~0.75 d | Phase 3 ships upload, not lifecycle |
| 16 | **Named seats instead of sub-roles** | tenant admin | ~0.75 d | §4.4 — replaces a ~4 d item |
| 17 | **Tenant suspend + user deactivate** | platform admin | ~0.5 d | P3, P5 |

Items 1–3 are together under two days and unblock five other rows. **They should be done
before Phase 5 starts, not after.**

---

### 1.7 The one mechanism behind the whole "0 code change" goal

The user's goal — *"users from the dashboard have all the options to be able to do and set,
and that should be consistent throughout"* — has a failure mode that is easy to walk into:
seventeen settings screens, each with its own storage, its own precedence rules and its own
idea of who may change what. That is three specialised mechanisms where one belongs.

**Aegis already has the right pattern and uses it in exactly one place.**
`aegis/src/aegis/agent/harness.py:54` **[SOURCE]** declares `_KNOB_SPECS` — per knob: key,
type, effective value, default, doc string and bounds — and `harness_config()` projects it
as a form descriptor that `GET /harness/config` serves and the Harness screen renders.
`aegis/tests/agent/test_harness_config.py` asserts a **bijection** between `AgentConfig`
fields and `_KNOB_SPECS`, so a knob cannot be added without a UI control appearing for it.

That is the mechanism. Generalise it, once:

**A `SettingSpec` catalogue.** One declaration per controllable thing:

```
key                 'agent.mode' | 'guardrails.pii.enabled' | 'model.default.generation' | …
type + bounds       enum/int/bool/string, with the legal range
default             the platform default
writable_by         the minimum scope that may write it: platform | tenant | user
readable_by         who may read it (a tenant reading the platform default is a feature — D2)
merge               how scopes combine: override | tighten_only | intersect | forbid
audit               whether a write emits an audit row (default: yes)
```

**A `settings` table**, one row per `(scope_type, scope_id, key)`, with `scope_type ∈
{platform, tenant, user}`, `tenant_id` denormalised for RLS, plus `updated_by` and
`updated_at`. It joins the 13 registered tenant-scoped tables in
`aegis/src/aegis/governance/rls.py` in the same change — plan 04's rule that every new table
arrives with a tenancy story applies here without exception.

**One resolver:** `resolve(key, tenant_id, user_id) -> (value, source)`. Precedence
`platform < tenant < user < per-run`, with the `merge` rule applied at each step. The
`source` half of that return is not decoration — it is what lets every screen say *"Aegis
default"* vs *"your tenant's"* vs *"your choice"*, which Phase 5.5 already identified as a
requirement for the model picker and which every other control needs just as badly.

**Three consequences worth the two days:**

1. **`merge: tighten_only` is where the guardrail security question is solved** (§4.2, §5).
   A tenant cannot express "weaker" because the resolver will not compute it. The safety
   property lives in one function with one test, not in seventeen form validations.
2. **New controls cost a catalogue entry, not a screen.** That is literally the "almost 0
   code change" goal, and it is the only version of it that stays consistent.
3. **`writable_by` is the answer to "which controls must a tenant never have"** — it is data,
   it is enumerable, it is testable, and §5 becomes a table in the catalogue rather than a
   paragraph in a document nobody re-reads.

**What I am deliberately not proposing:** a general-purpose policy engine, inheritance
between tenants, or dynamic setting registration. Flat keys, three scopes, five merge rules,
one closed catalogue. If hierarchy is ever genuinely needed, that is a new ADR — the same
guard the backlog already puts on the permission model.

**The honest cost:** the catalogue must be *complete* to be useful, and completing it is
tedious. Start with the ~20 keys that back items 5, 8, 10 and 15 above, and add the bijection
test from day one so it cannot rot.

---

### 1.8 What the navigation should become

`ROLE_SECTIONS` after this plan. Additions in **bold**, moves in *italics*.

```
platform_admin: dashboard · governance · tenants · roles · approvals(read-only)
                audit · forecast · policy · health · jobs · db · console
tenant_admin:   dashboard · team · approvals · budget · knowledge · memory
                policy(read + tighten) · llmops · audit · reports · console · settings
ai_team:        console · harness(writable) · agents · skills · llmops · evals
                mlops · tokenopt · memory · rag · graph · cache · voice · vision
                guardrails · simulation
devops:         dashboard · health · jobs · stack · patch · security · redteam
                latency · cache(live) · audit
client:         console · dashboard · savings · forecast · risk · memory · settings
```

Two rules that keep this from becoming twenty-eight sections per role:

- **A section exists for a role only if that role can *act* on it.** A read-only copy of
  someone else's screen is not a section; it is a tab on their own.
- **`simulation` (the access demo) is a demo artefact, not an operator tool.** Keep it on
  `ai_team` and `client` where it tells the isolation story; do not propagate it.

---

### 1.9 What I am cutting, and why

Stated plainly so nobody quietly starts one:

- **Secret / key rotation UI (O12).** Real work, zero demo surface, and a web form that
  handles API keys is a new attack surface for a problem `.env` already solves on a
  single-operator deployment. Not now, and not as a stretch goal.
- **The full `tenant_roles` + permission-catalogue hierarchy (D4).** ~4 d, needs schema the
  additive reconciler refuses, and the demo shows nothing the coarse roles do not. Replaced
  by named seats — §4.4.
- **A tenant-facing "which model exactly" free choice.** Tenants pick from the platform's
  *allowed* deployments, not from an open field. §5.
- **Per-user notification / email settings, theme settings, and profile pages.** They pass
  "a user can't do X without it" only in a trivial sense. Not in a hackathon build.
- **A separate "reports" subsystem.** Phase 6.4 is one module streaming CSV. Keep it that way.

One thing I am explicitly **not** cutting even though it is tempting: **document lifecycle
management (T9)**. Phase 3 ships upload and a live ingest log. Without list/delete/re-ingest,
a tenant who uploads the wrong PDF on demo day has no recovery, and "we'll fix it in the
database" is precisely the answer this whole plan exists to eliminate.

---

# Part 2 — C1, Grok-style agent selection

> *"user option to launch a specific agent or auto setting... like grok where I have option
> to select agent and stuff — fast, think and agents. Or custom. user specific given tool or
> automatic."*

Phase 4 builds the automatic half: a depth classifier extending `RouterDecision` with
`depth: SINGLE|TEAM` and `fanout: int`, deterministic-first with one cheap-model tiebreak,
defaulting to SINGLE on every failure path (`phase-04-multi-agent.md`, task 4.1). **This
section is the manual override on top. It does not re-plan the classifier.**

### 2.1 What the three products the user referenced actually ship

| Product | The control | What it selects |
|---|---|---|
| **Grok** | One mode switch in the query input bar: **Auto · Fast · Expert · Heavy** — the dropdown's own descriptions are "Chooses Fast or Expert", "Quick responses", "Thinks hard", "Team of experts" **[EVIDENCE]** | A *strategy*, not a model. Auto is a router between the two cheaper strategies. "Heavy" is explicitly a multi-agent team. |
| **ChatGPT** | Auto router with **Instant · Thinking · Pro**; "Auto-switch to Thinking" is a configurable behaviour **[EVIDENCE]** | Same shape. The load-bearing detail: **automatic escalation to Thinking does not consume the weekly Thinking quota; manually picking Thinking does.** |
| **Claude** | The model menu holds **three settings at once** — model, effort (standard/high/xhigh/max), and an extended-thinking toggle — and they are independent. Tools and connectors are a *separate* surface (`/tools`, `/connectors`) **[EVIDENCE]** | Depth and model are orthogonal. Tools are not in the depth control. |

Three conclusions, and all three are load-bearing for Aegis:

1. **All three ship an Auto default that routes, and none of them removed the manual
   override.** The manual control is not a hedge against a bad classifier; it is a
   first-class product surface. Phase 4's classifier and this section are complements.
2. **The mode names a strategy, not a model.** Grok's "Heavy = team of experts" maps exactly
   onto Aegis's `Depth.TEAM`. This is the mapping the user was pointing at, and it means
   Aegis needs *no new concept* — only a control over the one Phase 4 is already building.
3. **Depth, model and tools are three separate controls.** Claude separates all three;
   Grok's mode dropdown does not contain tool toggles. Fusing them into one "agent picker"
   would be the over-complex option, and it is also not what the products the user admires
   actually do.

### 2.2 The design: three axes, one composer cluster

```
┌─ composer ─────────────────────────────────────────────────────────────┐
│  [ Mode: Auto ▾ ]   [ Model: Aegis default ▾ ]   [ Tools: 6 of 9 ▾ ]    │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Ask anything…                                                   │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ◷ $2.14 of $50 today                                    [ Send ]      │
└────────────────────────────────────────────────────────────────────────┘
```

| Axis | Owner | State |
|---|---|---|
| **Mode** | this plan | ❌ new |
| **Model** | 📋 Phase 5.5 (`GET /models` projected from `routing_table()`, override on the governance contextvar) | planned |
| **Tools** | this plan §2.5 | ❌ new |

### 2.3 The mode set

Five values. Named for what they do to *this* system, not copied from Grok's marketing.

| Mode | What it does | Maps to |
|---|---|---|
| **Auto** *(default)* | Phase 4's classifier decides `SINGLE` vs `TEAM` and the fan-out width. | `RouterDecision` unchanged |
| **Fast** | Force `SINGLE`. Skip the classifier's model tiebreak entirely. Additionally pin the cheap deployment and disable the agentic-retrieval loop for this run. | `depth=SINGLE`, `agentic_retrieval_enabled=False` |
| **Deep** | Force `SINGLE` but with the full retrieval loop and the larger model — the "think harder in one lane" option Grok calls Expert and Claude expresses as effort. | `depth=SINGLE`, `agentic_retrieval_max_rounds` at its ceiling |
| **Team** | Force `TEAM` at the platform-capped width. The user is explicitly buying a fan-out. | `depth=TEAM`, `fanout=cap` |
| **Custom** | Reveals the pinning panel: choose the agents from the roster, the tools, and the width. | `depth=TEAM` with an explicit roster |

**Why five and not three:** Fast and Deep both force SINGLE and differ only in retrieval depth
and model tier — but that difference is the entire "quick answer vs think hard" distinction
every comparable product ships, and Aegis already has both knobs
(`agentic_retrieval_enabled`, `agentic_retrieval_max_rounds`, `answer_cache_enabled` on
`AgentConfig` **[SOURCE]**). Collapsing them would throw away a control the code already has.

**Why Custom is one mode and not a separate screen:** because "user specific given tool or
automatic" is the same decision as depth, made at the same moment, by the same person.

### 2.4 How manual interacts with automatic — the precedence rule

**Manual wins, and the screen says so.**

```
effective_depth = user_mode if user_mode != AUTO else classifier_decision
```

Three rules make that safe:

1. **The `routing` event carries the source.** Phase 4 already puts `depth` and `fanout` on
   the `routing` event (`aegis/src/aegis/agent/events.py:243-256`, per phase-04 task 4.1).
   Add one field: `decided_by: 'auto' | 'user' | 'tenant_default' | 'platform_cap'`. The
   trace then reads *"TEAM ×3 — you selected Team mode"* or *"SINGLE — single-intent query,
   answering in one pass"*. Never a width with no explanation.
2. **A manual choice can be *narrowed* by the platform, never widened by the user.** If the
   platform cap is `max_parallel_agents=4` and a user pins 6, they get 4 and the event says
   `decided_by='platform_cap'`. `Custom` is not a way around a budget.
3. **Manual escalation is charged and pre-flighted; auto-escalation is not.**
   This is the OpenAI rule **[EVIDENCE]** and it is the only thing standing between a Team
   button and a burned credit balance. Concretely:
   - Picking **Team** shows an estimate before the run ("~8–12 model calls, est. $0.04")
     computed from `unit_cost` in `routing_table()` × the roster's model roles — figures that
     already exist.
   - A manual Team run that would cross the budget is refused **at the button**, with the
     existing `BudgetExceeded` reason string, rather than being refused mid-fan-out.
   - Auto-escalation stays governed by the ordinary budget enforcer, exactly as today.

**The failure default is Fast, not Auto.** If the settings resolver cannot read a value, if
the classifier throws, if the roster is empty — SINGLE. Phase 4 states this for the
classifier; the manual path must not introduce a second, more permissive default.

### 2.5 Pinning agents and tools — and the one invariant

**Tools.** The picker lists the persona's tools with three states per row:

| State | Mechanism that already exists |
|---|---|
| **Allowed** | in the effective allowlist |
| **Ask first** | tool risk ≥ `gate_min_risk` → the existing human gate (`AgentConfig.gate_min_risk`, `deps.py:135` **[SOURCE]**) |
| **Off** | removed from the allowlist for this run |

No third mechanism. The gate and the allowlist are the two controls Aegis already has, and
the UI is a projection of them.

**The invariant, and it is non-negotiable:**

```
effective_tools = platform_allow ∩ persona_allow ∩ tenant_allow ∩ user_pin
```

Every operator is an intersection. **A pin can only ever narrow.** Phase 4 task 4.2 already
states this for sub-agents ("a sub-agent can never widen its own reach", intersecting with
`backend/src/app/adapter/tools.py::is_allowed`). **Make the UI pin go through that same
function.** One intersection in the codebase, not two — because two is how the second one
ends up subtly more permissive.

**Agents.** `Custom` mode lists the sub-agent roster (`AgentDeps.agent_roster`, an injected
callable **[SOURCE]**) with checkboxes. Selecting agents sets the task list `plan_team` would
otherwise generate. The same intersection rule applies: a pinned agent still gets its spec's
allowlist ∩ the persona's.

**Editing the roster itself** (A14) is an `ai_team` capability, not an end-user one, and it
belongs in the settings catalogue as `agents.roster` with `writable_by: platform` (or
`tenant` if a tenant is allowed its own specialists — my default is **platform only**, see
§8).

### 2.6 Persistence — and this is where §1.7 pays for itself

Four scopes, resolved by the one resolver:

| Scope | Key | Who writes it |
|---|---|---|
| Platform | `agent.mode.default`, `agent.mode.max` (the cap), `agent.team.max_parallel` | platform admin |
| Tenant | `agent.mode.default`, `agent.tools.allow` | tenant admin, bounded by `agent.mode.max` |
| User | `agent.mode.default`, `agent.tools.pin` | the user |
| Run | the composer control | the user, per query |

`resolve('agent.mode', tenant, user)` returns `(value, source)` and the composer renders the
source label — the same pattern Phase 5.5 specifies for the model picker. **One resolver, two
consumers, consistent by construction.** That consistency is exactly what the user asked for
("that things should be consistent throughout") and it is not achievable if the model picker
and the mode picker each invent their own persistence.

### 2.7 The wire changes, in full

Small, additive, and every one of them lands on a seam that already exists:

```python
# backend/src/app/api/schemas.py — QueryRequest gains (currently: query, persona, session_id)
mode: Literal['auto','fast','deep','team','custom'] | None = None
model_overrides: dict[str, str] | None = None        # Phase 5.5 already specifies this
agent_pins: list[str] | None = None                  # roster ids, custom mode only
tool_pins: dict[str, Literal['allow','ask','off']] | None = None
fanout: int | None = None                            # custom mode only; clamped to the cap
```

```python
# routing event gains
decided_by: Literal['auto','user','tenant_default','platform_cap']
```

```python
# get_agent_deps() gains an auth dependency so AgentConfig is built per principal
def get_agent_deps(auth: AuthContext = Depends(require_auth)) -> AgentDeps: ...
```

That last line is the only structural change, and `AgentDeps.default(config=...)` already
takes a config argument (`aegis/src/aegis/agent/deps.py:306` **[SOURCE]**). Nothing in the
graph changes.

### 2.8 What I am deliberately not building

- **A prompt-level `@agent` mention syntax.** Two ways to select an agent is one too many.
- **Per-agent model selection in the composer.** The model picker is per *role*
  (Phase 5.5); per-agent-per-model is a combinatorial UI for a distinction nobody on a jury
  will ask about.
- **Saved "agent presets" as a first-class object.** A preset is a settings row. If someone
  wants named presets later, they are rows in the same table.
- **Streaming the classifier's confidence.** The reason string is already demoable and a
  number nobody calibrated is worse than no number.

---

# Part 3 — C2, multi-agent visibility as a control

Phase 4 owns the event protocol (`agent_id` on every event, task 4.4). Phase 5.3 owns the
agent-card layout, the tool-call chips and the activity rail. **This section covers only what
makes those cards a *control* rather than a display**, which is the part neither plan states.

### 3.1 The distinction that matters

A card that shows what an agent is doing is a display. A card becomes a control when the
person watching can **change the outcome of the run they are watching**. Today there is
exactly one such control anywhere in the console — the approval gate — and it is the single
most-praised thing in the product. That is not a coincidence.

### 3.2 Three controls on the agent card, and no more

| Control | Behaviour | Cost |
|---|---|---|
| **Stop this agent** | Cancels one sub-agent cooperatively; the run continues with the rest and the synthesis event names it as omitted — which Phase 4 task 4.5 already requires for *timeouts*. Same terminal state, different trigger. | Small, once §3.4 exists |
| **Stop the run** | Cancels every agent. §3.4. | §3.4 |
| **Approve / deny a proposed action** | Already exists. Phase 4 task 4.2 routes sub-agent HIGH-risk proposals into the main gate; the card should surface it *on the agent that proposed it*, not only in the global spotlight. | Small |

**Everything else on the card stays a display.** No "retry this agent", no "edit its prompt
mid-run", no "add a tool now". Each of those is a new state machine in the orchestrator for a
control nobody demos.

### 3.3 "Clear options of tools" as a control

The user's phrase is *"clear options of tools"*, and read as a display it means a tool legend,
which is worth almost nothing. Read as a control it means: **before the run, the user can see
every tool the agent may reach and change it; during the run, they can see which fired and
what it returned; after, they can see the cost.**

- **Before** — the tool picker of §2.5, showing the *effective* set and where each state came
  from (persona / tenant / your pin).
- **During** — Phase 5.3's tool-call chips (`web_search("…") → 5 results · 820 ms`),
  attributed to an agent by `agent_id`.
- **After** — the Agents tab (Phase 5.4) plus per-tool cost, which `node_finished` already
  carries.

The only new thing here is the *before*, and it is the same picker as §2.5. One control,
three views of it.

### 3.4 Cancel — the control the whole surface is missing

There is no cancel path anywhere: no `POST /runs/{id}/cancel`, no cancellation token in the
orchestrator, nothing. The backlog names it as "adjacent and cheap once `run_events` exists"
— **it is cheap now and it does not depend on `run_events`.**

Design, deliberately minimal:

- An in-process `asyncio.Event` per `run_id`, held in the same registry pattern as
  `get_approval_registry()`.
- `POST /runs/{run_id}/cancel` sets it. Guard: the run's own principal, or a tenant admin for
  a run in their tenant, or a platform admin. Writes a `run.cancel` audit row.
- The orchestrator checks it **between agent steps and between graph nodes** — never
  mid-tool-call, which would leave a side effect half-applied.
- The run terminates with a *designed* status (`cancelled`), not an error. Phase 4 task 4.5
  already establishes "partial completion is a designed state, not a bug"; cancellation is
  the same state with a different cause.

**Why this is not optional.** Four concurrent agents against a $100 credit balance, on a
stage, with no stop button, is a risk that costs money and composure. And it is the first
thing an enterprise reviewer asks after "can I see what it did".

**The honest limit:** single-process only. `scripts/start.ps1:34` runs uvicorn with no
`--workers` (plan 04 §0.2 **[SOURCE]**), so an in-memory registry is correct today. When
plan 04's job substrate lands, cancellation moves to a `cancel_requested` column and becomes
multi-process for free. Build the in-process one now; do not build the distributed one twice.

### 3.5 The failure states that must be designed, not discovered

Named here because Phase 5.3 lists the happy path and one failure:

| State | What the card must show |
|---|---|
| Timeout | 📋 Phase 4.5 — a terminal state and a duration |
| Cancelled by user | "Stopped by you at 12 s" — never a spinner that just vanishes |
| Budget exceeded mid-fan-out | The `BudgetExceeded` event already carries scope, cap and consumption. Show the number, not "an error occurred" |
| Tool denied by the allowlist | This is a **control working**. Render it as a refusal with a reason, exactly as Phase 6.1 requires for the cross-tenant 403 |
| Guardrail blocked a tool result | 📋 Phase 4.7 adds `GuardStage.TOOL_RESULT` — give it a chip, not a silent drop |

---

# Part 4 — D, the tenant-facing capabilities

### 4.1 D1 — Per-tenant memory · **the highest-value item in D**

> *"most important is memory — the option for the tenant to upload their memory, and for
> their tenant user to upload some of their memory + see what their memory is being added and
> delete + during retrieval what memory is being referenced needs to be shown. Memory should
> be real and used across tenant and user, not some gimmick."*

**Why this one first.** The user named it most important. It is also the cheapest of the
seven, because most of it is built: `GET /memory/facts`, `/profile`, `/sessions`,
`/sessions/{id}/messages`, `/writes`, `/recall_debug`, `DELETE /memory/facts/{id}`,
`POST /memory/forget` — **eight endpoints, all on `require_auth`** (`routes.py:1525-1965`
**[SOURCE]**). And it is the most legible thing on screen once the last piece lands.

**What is actually missing — three things, in order of value:**

**(a) Identities on the `memory` event.** Today `MemoryEvent` carries
`recalled_fact_count`, `recalled_message_count`, `tokens_used` and nothing else
(`schemas.py:361-387` **[SOURCE]**). "Three facts recalled" is a count; *"recalled: 'prefers
email contact' · 'enterprise tier' · 'do not contact after 18:00 CET'"* is the feature the
user asked for. Add `recalled: list[MemoryRef]` with `{id, kind, text_preview, score}` —
capped at ~8 entries and truncated previews, because this rides an SSE stream.

The scoring already exists: `aegis/src/aegis/memory/scoring.py` and `recall.py` produce
`RecallCandidate` with scores; `GET /memory/recall_debug` (`routes.py:1770`) already surfaces
exactly this data *out of band*. **This is a projection of an existing structure onto an
existing event, not a new subsystem.**

**(b) A write endpoint.** `POST /memory/facts` — a tenant admin or a user asserting a durable
fact directly instead of waiting for the extractor to distil one. Three requirements, none
negotiable:

- **The text goes through `check_input` before it is stored.** An uploaded memory is injected
  into a future prompt; an unscreened one is a stored prompt injection with a long fuse. This
  is the sharpest security edge in D1 and it is easy to miss because the endpoint looks like
  a CRUD write.
- `origin` is stamped `USER` and the row is written through the existing `MemoryWriteLog`
  path so the write appears in `GET /memory/writes` like every other write. No side door.
- `subject_id` is derived server-side, never accepted from the client. §5.

**(c) Tenant-level memory alongside user-level.** `memory_subject_for` returns
`f"user:{user_id}"` (`backend/src/app/adapter/memory_spec.py:112-114` **[SOURCE]**), and
`recall()` takes a single `subject_id: str` (`aegis/src/aegis/memory/recall.py:373-384`
**[SOURCE]**). So "tenant memory shared across the tenant's users" needs either a second
recall pass over `tenant:{id}` merged before assembly, or `subject_id` widened to a sequence.

**Recommendation: a second recall pass, merged in the assembler.** It touches one call site,
keeps `recall()`'s contract, and makes the precedence explicit (user facts outrank tenant
facts on a tie, which is the behaviour a person expects). Widening the parameter to a
sequence pushes the merge into every backend query and is the more invasive of the two for
no extra capability.

**Screens.** Mount a memory surface on `tenant_admin` (tenant + all users' memory, with
delete), on `client` (their own only), and keep the existing `ai_team` one. The components
exist — `MemoryView.tsx`, `SemanticFactsPanel`, `WriteLogPanel`, `RecallDebugPanel`,
`StructuredProfilePanel` **[SOURCE]** — this is a mount plus a scope parameter, not a build.

**Honest risk.** `memory_fact` currently holds **0 rows** **[MEASURED]**. Nothing in this
subsystem has been exercised against real data on this database, and Phase 5 notes that every
live run today recalls nothing because the console never sends `session_id`
(`phase-05-console.md` §3 **[SOURCE]**). **Fix Phase 5.2 first, then build this.** A memory
screen over an empty store, on a path that never populates it, is a screen that will be
honest and empty on stage.

---

### 4.2 D2 — Per-tenant guardrails · **the highest-risk item in D**

> *"in the guardrails we need to have the options for the tenant to add their own guardrails
> + also see what guardrails we as Aegis platform keep on by default and read them — the
> option to read is important."*

Two requirements, and they have opposite risk profiles.

**(i) Read the platform defaults — do this first, it is nearly free and it is a trust
feature.** A new `GET /guardrails/policy` returning the effective rail stack as data: each
rail's name, what it screens, whether it is a hard block or advisory, its threshold, and
whether the model-backed layer is wired. This is honest introspection of configuration that
already exists — `Guardrails.__init__`'s parameters (`pipeline.py:118-135`), the settings
flags (`grounding_block`, `guardrails_engine`) and the rail modules themselves. Readable by
every role. Nothing about it is dangerous, and "here is exactly what we screen, read it
yourself" is a strong enterprise answer.

**(ii) A tenant adds their own — this is where a control panel becomes an attack surface.**

The mechanism the code offers is good: `Guardrails.__init__` accepts injected `input_rails` /
`output_rails` that *run after the built-ins*, and any non-PASS verdict short-circuits with
its own layer label (`pipeline.py:112-116` **[SOURCE]**). Custom rails are therefore
**additive by construction** — a tenant rail can block something the platform passed, and it
runs too late to unblock something the platform blocked.

**But there is a real obstacle: the pipeline is a process-wide singleton.**

```python
# backend/src/app/guardrails/__init__.py:82  [SOURCE]
_guard = Guardrails(
    completer=_gateway_completer,
    ground_answers=True,
    grounding_block=get_settings().grounding_block,
)
```

One instance, module scope, built from process settings. Per-tenant rails require
per-request construction. **Recommendation: a small per-tenant cache keyed on
`(tenant_id, policy_version)`** — construct on miss, reuse on hit, invalidate on a policy
write. `Guardrails` "holds no per-call state" per its own comment at `__init__.py:76`, so
instances are cheap and safe to cache. Do **not** rebuild it per request; the injection cache
and the media screen it constructs are not free.

**What a tenant may configure — a closed list, expressed as catalogue entries (§1.7):**

| Setting | Merge rule | Rationale |
|---|---|---|
| `guardrails.topics.allowed` | `tighten_only` | Their business domain. Narrowing what the agent will discuss is theirs to decide. |
| `guardrails.topical.block` | `tighten_only` (false→true only) | Advisory → hard block is a tightening. |
| `guardrails.grounding.block` | `tighten_only` | Same. |
| `guardrails.denylist.terms` | `additive` | Their own forbidden strings. Purely additive; cannot subtract. |
| `guardrails.pii.entities` | `additive` | Extra entity types to redact. Never fewer. |
| `guardrails.custom_rails` | `additive` | Declarative pattern rails only — **not** arbitrary code and **not** arbitrary regex. See below. |

**On custom rails: declarative, from a closed template set.** A tenant picks a template
(`contains_term`, `matches_pattern` from a vetted pattern library, `max_length`,
`requires_citation`) and fills parameters. **Not free-form regex** — a tenant-supplied regex
is a ReDoS vector against the request path, and a tenant-supplied Colang flow or Python
callable is remote code execution. If free-form regex is ever wanted, it needs a timeout, a
complexity check and a sandbox, and that is a different project.

**And the thing a tenant must never have — see §5 for the full list, but the load-bearing one
here:** *no setting a tenant can write may reduce the platform's strictness.* The resolver's
`tighten_only` rule is where that is enforced, in one function, with one test. Not in a form
validator, and not in a code review.

---

### 4.3 D3 — Per-tenant LLMOps

> *"llmops should be customised for every tenant we have and their own system prompt version
> should be able to be seen to them."*

**The data layer is already multi-tenant.** `PromptVersion` carries `tenant_id` as an indexed
column (`aegis/src/aegis/ops/models.py:82` **[SOURCE]**), it is one of the 13 RLS-registered
tables **[MEASURED]**, and `get_active()` already accepts `tenant_id` and filters on it
(`registry.py:156-165` **[SOURCE]**).

**The read path is not.** Two concrete defects:

1. `list_versions(session, prompt_key)` takes **no** `tenant_id` (`registry.py:169`
   **[SOURCE]**), and `GET /ops/prompts` (`routes.py:2038`) passes none. Every tenant sees
   every tenant's versions, held back only by RLS — and RLS's predicate is fail-*open* on an
   unset scope until the backlog item lands.
2. `_ACTIVE_CACHE: dict[str, ...]` is keyed by prompt key alone (`registry.py:28`, per the
   backlog **[SOURCE]**), so two tenants cannot have different active prompts — whichever was
   cached last wins for everybody.

**Recommendation, and the order is the point: fix (2) first, then (1), then build the
surface.** The backlog already says this ("no such surface is truthful until it is fixed").
I am restating it because it is the single most likely way this feature ships as a lie: the
screen will look right with one tenant in the database, and there is currently exactly one
tenant configuration in existence — zero **[MEASURED]**.

**The surface itself**, once the reads are honest: version list with an active marker, a diff
between versions, the **eval delta that gated each promotion** (`ops/gate.py` already computes
it), and rollback. Mounted on `tenant_admin` scoped to their tenant, and on `ai_team` with a
tenant selector — which is the *"AI team can select per user or per tenant"* requirement
(A5/A6), partially satisfied without `run_events`.

**And the invariant: the prompt floor is not editable by a tenant.** `render_floor_prompt` is
the adapter/persona baseline "the registry builds on but never goes below"
(`aegis/src/aegis/ops/config.py:8-9` **[SOURCE]**). A tenant edits their *version*; the floor
is composed underneath it by the platform. §5.

---

### 4.4 D4 — Tenant sub-roles · **the item I would cut and replace**

> *"within tenant, aegis admin and tenant admin can make more sub roles under the tenant"*

**The full version is a trap.** The backlog costs it at ~4 d: a `tenant_roles(tenant_id, key,
label, permissions jsonb)` table, a closed permission-key catalogue, and a
`require_permission(...)` dependency replacing or layering under the 8 role guards in
`routes.py`. It needs a table with a foreign key plus a NOT-NULL defaulted column on an
existing table, **which the current additive reconciler refuses** — so it also pulls in
Alembic (~2.5 d). Seven days of work for a capability whose demo is "I made a role called
Analyst."

**What I would ship instead: named seats.**

A tenant admin assigns each of their users one of the existing coarse roles **plus** a small,
closed set of tenant-scoped capability toggles stored in the *same* settings table from §1.7:

```
seat.can_upload_documents      bool   default false
seat.can_edit_memory           bool   default false
seat.can_approve               bool   default false   (tenant-owned gates only)
seat.can_view_tenant_audit     bool   default false
seat.can_change_agent_mode     bool   default true
seat.label                     string default ""      ("Analyst", "Support Lead")
```

- **Zero new schema mechanisms.** It is the settings table, already built for §1.7.
- **Zero new auth mechanisms.** The coarse guard still runs first; the toggle is a second,
  narrowing check. It can only remove capability, never add it — the same "narrowing only"
  property as the tool pins in §2.5, which is what makes it safe to ship quickly.
- **It demos identically.** "I created a Support Lead seat that can approve but cannot upload
  documents" is the same sentence, and it is true.
- **It does not foreclose the real thing.** If `tenant_roles` is ever built, these keys become
  the initial permission catalogue.

**The honest limitation, stated so nobody oversells it:** these are per-user capability flags,
not a role *object* that can be defined once and assigned to many users. If a tenant wants
twelve identical Analysts they set six flags twelve times. That is a real ergonomic
shortcoming and it is worth 6.25 days.

---

### 4.5 D5 — Downloadable reports

📋 **Phase 6.4 already owns `audit.csv`, `tenant.csv` and `budget.csv`**, including the two
rules it gets right and that must survive contact with this plan:

- Every export writes its own `report.export` audit row carrying the filter parameters.
- `budget.csv` reads the same `BudgetStatusRow` the enforcer reads, so the report and the cap
  cannot disagree.

**This plan adds one report and one rule.**

**`GET /reports/forecast.csv`** — the horizon, the point forecast, the interval bounds, the
interval *kind*, and the **achieved** coverage. The existing surface is careful about exactly
this: `ForecastResponse` distinguishes `requested_coverage` from what the interval actually
achieved on rolling-origin held-out windows, and `forecast/budget` explicitly flags
`cumulative_bounds_are_calibrated = false` because summed marginal conformal bounds are not a
calibrated cumulative interval (`routes.py:2966-3040` **[SOURCE]**). **A CSV that drops those
caveats turns a carefully honest surface into a misleading spreadsheet the moment it leaves
the browser.** Carry them as columns, not as a footnote.

**The rule: the report is generated from the same accessor the screen renders.** Not a
parallel query. Every divergence between a screen and its export is a bug waiting for the one
person who checks both — and on this project that person exists.

**On PDF:** Phase 6.4 chose CSV over PDF for a no-Docker Windows box. That call stands. A
compliance export gets opened in Excel.

---

### 4.6 D6 — Real red-teaming the infra profile can initiate and control

> *"for infra team which is sec + devops mix, real red teaming option should be there — they
> can control and initiate"*

**The battery is real and it is already parameterised.** `run_redteam` takes
`check`, `completer`, `battery` and `thresholds` (`aegis/src/aegis/redteam/runner.py:305-311`
**[SOURCE]**); the battery is a curated `Attack` dataclass set tagged with garak-aligned
categories and OWASP LLM Top-10 ids, split into attacks and benign controls so the report
measures a false-positive rate as well as a block rate (`battery.py:1-52` **[SOURCE]**). The
report computes real verdicts from real `GuardResult`s. **None of this is a gimmick.**

**The endpoint throws all of that away:**

```python
# backend/src/app/api/routes.py:2631  [SOURCE]
report = await run_redteam()          # no completer, no battery, no thresholds
```

So "control and initiate" today means one button that runs one fixed configuration offline.

**What to expose — parameters that already exist, plus two additions:**

| Control | Backed by | Note |
|---|---|---|
| Select categories | `Attack.category` — already tagged | Filter the battery |
| **Run against the live model layer** | the `completer` argument | The battery already marks `needs_llm=True` attacks the deterministic rails cannot catch by design. This is the difference between "our signatures hold" and "our stack holds", and it is the honest headline number |
| Adjust thresholds | `RedTeamThresholds` | Already a parameter |
| Add tenant/site-specific probes | `battery` | Same template discipline as §4.2 — parameterised templates, not free-form payloads |
| **History and trend** | new | ❌ nothing is persisted; the audit row keeps three summary numbers |
| **Scheduled runs** | 📋 plan 04 §1.13 job scheduler | A natural first job for the substrate |

**Two guardrails on the guardrail tester:**

1. **A live-model run costs money and must be budgeted.** It is dozens of model calls. Route
   it through the same governance context and budget enforcer as `/query`, show an estimate
   before the button, and keep the offline run as the default. A red-team run that silently
   drains the demo credits the morning of the 30th is a self-inflicted wound.
2. **Devops only, never tenant-facing.** §5.

**And the honest framing for the report:** the offline block rate is *"our deterministic
signatures blocked N of M"*. It is not *"Aegis blocks N%"*. The battery's own docstring is
already careful about this ("the report shows exactly which attacks the offline rail misses").
Keep that distinction on the screen.

---

### 4.7 D7 — The admin forecast page: SHAP and interactive feature selection

> *"forecast in admin of aegis should show forecast of how aegis as a platform is going to
> perform, not the tenant forecast... option to see how the ml model is predicting, which
> feature is doing what — basically I want to see a SHAP chart in there too. Also the option
> for feature engineering: if I add or delete some feature how does the forecast look —
> basically the option to select and deselect the column in the db but in the dashboard. And
> the option to download the forecast report."*

**There are two different models here and the plan must not blur them.** This is the one
place in the whole requirement where the honest answer differs from the literal request, and
saying so is worth more than a chart that lies.

| | Platform forecast | The ML spine |
|---|---|---|
| What | Daily spend / call volume, forward | Supervised prediction from feature columns |
| Model | `statsforecast` + conformal intervals (`aegis/src/aegis/forecast/`) | `TrustworthyModel` — ensemble + MAPIE + SHAP (`aegis/src/aegis/ml/model.py`) |
| Inputs | **One univariate series** from `usage_ledger` | An explicit feature list from `ResolvedSpec` |
| SHAP applicable? | **No.** There are no features to attribute | **Yes.** `_explainers()` builds `shap.TreeExplainer` per member (`model.py:608`) |
| Feature selection meaningful? | **No** | **Yes.** `ResolvedSpec.features` is an explicit ordered list and `train()` takes it |

**The platform forecast already exists and already does what was asked of it.**
`GET /forecast/usage?tenant_id=` with a null tenant is the platform aggregate for a platform
admin; `_scope_tenant` permits exactly that (`routes.py:2966-2996` **[SOURCE]**). **P10 is a
UI change, not a backend one.** Today `ForecastView` is mounted for both `admin` and `client`
with a role prop; make the admin variant default to the platform aggregate and add a tenant
selector.

**The SHAP + feature-selection half is real, and it belongs to the ML spine.** The spine takes
`features: list[str]`, `target`, `task` and an optional `frame_provider`
(`aegis/src/aegis/ml/spec.py` **[SOURCE]**), and `TrustworthyModel.train()` fits from that
spec. So "deselect a column and see what changes" is: **subset the feature list, retrain,
compare.** That is genuinely implementable, and it is genuinely the most impressive thing in
the entire D list to watch working.

**Recommendation — one page, two clearly-labelled panels, one new endpoint:**

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

- **`POST /ml/experiment`** → `{features: [...]}` → trains a spine on that subset and returns
  its model card, held-out metrics and SHAP attributions, **plus the delta against the
  currently-served model**. That delta is the answer to "how does the forecast look if I
  delete a feature", and it is a real measured number.
- **Platform-admin only.** Training is CPU work; running it in a request thread is how you
  block the event loop. `POST /ml/explain` already runs prediction through
  `asyncio.to_thread` for exactly this reason (`routes.py:1010` **[SOURCE]**) — a *training*
  call needs more than that. **Recommendation: this is the second natural job for plan 04's
  job substrate** — submit, poll, render. Do not run a retrain inline.
- **Never overwrite the served artifact.** An experiment is an experiment. Promotion is a
  separate, audited action, and the existing `POST /ops/release` gate pattern is the model for
  it.
- **`ShapWaterfall.tsx` already exists** (`web/src/components/charts/` **[SOURCE]**) and
  `MLOpsView` already renders a SHAP explanation from `POST /ml/explain`. The chart is not
  new work.

**The label is load-bearing.** Two panels, two `Source:` lines, two different models. A jury
member who asks "is the SHAP explaining the spend forecast?" must get "no — the forecast is a
univariate time series, the SHAP is the supervised spine, here is what each one is". Merging
them into one visual would be the single most damaging thing this plan could recommend.

**Honest risk:** `usage_ledger` holds **0 rows** **[MEASURED]**. The platform forecast has
nothing to forecast today, and the refusal path (`ForecastRefusal`) is what will render. That
is correct behaviour and it is also not a demo. **Seeding real ledger rows is a prerequisite,
and it must be real spend from real runs, not inserted rows** — inserting them would be
exactly the fabrication Phase 2 spent a day deleting.

---

# Part 5 — The controls a tenant must never have

Asked for directly, and it is the right question. Each row is a **catalogue entry** under
§1.7 (`writable_by` / `merge`), so this table is executable configuration rather than prose.

| # | Control | Why not | Enforcement |
|---|---|---|---|
| 1 | **Weakening any platform guardrail** — disabling PII redaction, injection screening, content safety, or lowering a threshold | The single most serious defect available here. A tenant who turns off PII redaction turns Aegis into a data-exfiltration tool while every screen still says "guardrails: active" | `merge: tighten_only`. The resolver *cannot compute* a weaker value. Test: for every guardrail key, a tenant write of a weaker value resolves to the platform value |
| 2 | **Raising their own budget cap** | The cap is a commercial boundary the platform sets. Self-service cap raising is not a control, it is the absence of one | `writable_by: platform` for `scope_type='tenant'`. A tenant admin may set **sub-caps on their own users** (T6) — always ≤ the tenant cap |
| 3 | **Raising `gate_min_risk`** (turning off the human approval gate) | `gate_min_risk` is the *only* gating signal (`deps.py:109-116` **[SOURCE]**) — that is what makes the guarantee explainable on stage. A tenant who raises it removes human oversight from destructive tool calls | `merge: tighten_only`. A tenant may *lower* it (gate more), never raise it |
| 4 | **Free-form SQL, or any database browse** | 📋 plan 04 §4 scopes it to platform admin, and that is right. Cross-tenant read is the entire risk | `writable_by`/`readable_by: platform` |
| 5 | **Reading platform-scoped audit rows, or any other tenant's anything** | Already enforced by `_scope_tenant` (`routes.py:436`) + RLS. Restated because every *new* endpoint in this plan must go through the same path | `_scope_tenant` on every read. Phase 6.4's rule — a test per report proving a tenant's CSV contains only its own rows — applies to every new endpoint here |
| 6 | **Choosing a model outside the platform's allowed deployments** | Cost and safety. The routing table's `unit_cost` is what makes budgets meaningful; an arbitrary deployment id breaks metering and can route around the vetted stack | The picker enumerates `routing_table()`; the server validates the override against that set and rejects anything else. **Validate server-side — a UI enum is not enforcement** |
| 7 | **Overriding the model role used by the guardrails** | The guardrail completer is deliberately separate from the answer completer (`pipeline.py`: `vision_completer` is kept separate for exactly this reason **[SOURCE]**). Letting a tenant point the injection classifier at a model of their choosing is a way to disable it without appearing to | The model override map excludes guardrail roles. `forbid` in the catalogue |
| 8 | **Disabling audit logging, or exporting without being audited** | An export of the audit trail that is not itself audited is the first hole a procurement reviewer finds — Phase 6.4 already says this | `audit: always` in the catalogue; no key exists to turn it off |
| 9 | **Unbounded fan-out** — `max_parallel_agents`, `max_plan_iterations`, `agentic_retrieval_max_rounds` above the platform cap | Budget denial-of-service, and it is *self*-inflicted DoS on a shared $100 balance | Platform cap clamps; the `routing` event reports `decided_by='platform_cap'` (§2.4) so the clamp is visible, not silent |
| 10 | **Widening a tool allowlist** | A tenant that can add a tool to a persona can reach data and actions the persona was scoped away from | `effective = platform ∩ persona ∩ tenant ∩ user`. Intersection only, through the one `is_allowed` function (§2.5) |
| 11 | **Uploading memory or documents that bypass the input rails** | A stored prompt injection is the most patient attack in the product. §4.1 | `check_input` on every memory write and every ingested document, on the server, before storage |
| 12 | **Setting their own `subject_id`, `tenant_id`, or persona on any request** | The isolation key must never be client-supplied. `memory_subject_for` derives it from `auth.user_id` (`routes.py:908` **[SOURCE]**) and it must stay that way | Derived server-side from `AuthContext`. Any request field that names a scope is validated against `_scope_tenant`, never trusted |
| 13 | **Initiating a live-model red-team run** | Spend, and it is the infra profile's control by the user's own framing | `require_devops` (+ platform admin). Offline runs may be readable by a tenant admin as a *report*; initiating is not theirs |
| 14 | **Editing the prompt floor** | The floor is the adapter/persona baseline the registry "builds on but never goes below" (`ops/config.py:8-9` **[SOURCE]**). A tenant editing it can delete the platform's non-negotiable instructions | A tenant writes a `PromptVersion`; the floor is composed underneath by the platform at render time |
| 15 | **Granting themselves `platform_admin`, or creating users in another tenant** | Privilege escalation | Already enforced — `admin_create_user` pins a tenant-admin to its own tenant with a 403 (`routes.py:1263` **[SOURCE]**). The self-lockout guard in `RolesAccess.tsx` mirrors it client-side. Keep both |

**The meta-rule, and it is the one that matters most:** *every one of these is enforced
server-side, in the resolver or the guard, and the UI merely reflects it.* A disabled control
in a form is a hint. Two of the fifteen (6 and 9) are exactly the kind that get "enforced" by
a dropdown and then bypassed by a curl. Test each one with a request that a UI would never
send.

---

# Part 6 — What the requirement missed

Things nobody asked for that this area needs. Ranked by how much I would fight for them.

**1. There is no "who am I" endpoint, and the browser never learns the fine role.**
`LoginResponse` carries `role`, `token`, `tenant_id` **[SOURCE]** and there is no `GET /me`.
The frontend infers tenant-admin-ness from `tenant_id != null`. Every per-role, per-tenant,
per-seat decision in this plan needs the principal's effective capabilities on the client, and
inferring them from two fields will not scale past the next feature. **`GET /me` returning
`{user, role, fine_role, tenant, seat_capabilities, effective_settings}` is the single most
reusable thing in this document.**

**2. A seed script with two real tenants is a prerequisite, not a nicety.**
`users`, `tenants`, `budgets`, `prompt_versions`, `usage_ledger` all hold **0 rows**
**[MEASURED]**. Nothing per-tenant in this plan can be tested — the LLMOps cache bug (§4.3)
cannot even manifest with one tenant. Phase 6.2 already requires a seed change for the
approvals guard; **make it two real tenants with real users, real budgets and real prompt
versions, once, and every subsequent phase gets a test bed for free.**

**3. `chunks` has no `tenant_id`, and Phase 3.7 is about to query it directly** **[MEASURED]**.
Corpus-wide BM25 over Postgres FTS has no tenant predicate available. Add the column and
register it in `_TENANT_SCOPED_TABLES` in the same change. This is the one finding in this
document that could re-open the leak Phase 1 closed.

**4. The Cache page is static prose on a screen labelled with a measurement.**
`CacheView.tsx` renders a hard-coded `SPECS` array with no fetch **[SOURCE]**. `GET /metrics`
already returns a real `cache_hit_rate`. This is the closest thing left in the repo to what
Phase 2 deleted, on a subsystem the v2 doc names specifically, and it is half a day to fix.

**5. Nobody can see the fate of an action they triggered.** `GET /approvals` is
`require_admin` (`routes.py:1113` **[SOURCE]**). A client raises a HIGH-risk action, it gates,
and they have no screen that says what happened. That is a workflow hole, not a UI polish
item.

**6. `is_active` exists on `AdminUserRow` and nothing writes it** **[SOURCE]**. Offboarding a
user requires a database edit. Combined with no token revocation (backlog), a removed user
keeps access for up to 12 hours *and* there is no way to remove them from a screen.

**7. Nothing tells a user what a control will cost before they use it.** Team mode, a
live-model red-team run, and an ML retrain are all expensive. The data to estimate all three
already exists (`unit_cost` in `routing_table()`, the battery size, the training frame size).
An estimate before an expensive button is both good product and good demo defence.

**8. There is no cross-portal search or command palette.** With ~40 sections after this plan,
a `⌘K` over the section catalogue plus the settings catalogue is perhaps two hours and it is
the difference between "comprehensive" and "sprawling". The catalogues are already data
(`SECTIONS`, `SettingSpec`) so this is a projection, not a feature.

**9. "Consistent throughout" needs a test, not an intention.** The user asked for consistency
by name. Two cheap tests make it structural rather than aspirational: (a) the `_KNOB_SPECS`
bijection test extended to the settings catalogue — every catalogue key has a control, every
control has a key; (b) a route-coverage test asserting every non-public endpoint is reachable
from at least one section of at least one portal, so an endpoint cannot exist with no way to
call it. Today that test would fail on at least eight endpoints.

---

# Part 7 — Sequence, by dependency

Timing is not a constraint; dependency is.

**Stage 0 — prerequisites (nothing here is optional).**
1. `GET /me` + `fine_role` on `LoginResponse`. *(Part 6.1)*
2. Seed script: two real tenants, real users, budgets, prompt versions. *(Part 6.2)*
3. Console + memory mounted on the `client` and admin portals — the `ROLE_SECTIONS` edit. *(§1.6 rank 1)*

**Stage 1 — the mechanism.**
4. `settings` table + `SettingSpec` catalogue + resolver + RLS registration. *(§1.7)*
5. `PUT /harness/config` as the first consumer, proving the pattern. *(A3)*
6. The `_ACTIVE_CACHE` tenant-keying fix + `list_versions(tenant_id)`. *(§4.3 — half a day, and every later per-tenant claim depends on it)*

**Stage 2 — the screens that unblock other screens.**
7. Approvals inbox screen. *(unblocks Phase 6.2, which is otherwise not shippable)*
8. Tenant-admin portal + navigation split. *(§1.8)*
9. `GET /guardrails/policy` — read-only platform defaults. *(§4.2(i) — cheap, and D2's write half depends on it existing to merge against)*

**Stage 3 — capabilities.** *(Phase 5.2 — session_id on the wire — must land before 10.)*
10. Memory: identities on the `memory` event, `POST /memory/facts`, tenant-subject recall pass, screens. *(§4.1)*
11. Agent selection: mode / model / tools. *(§2 — depends on Phase 4's classifier)*
12. Cancel. *(§3.4)*
13. Per-tenant guardrail writes, tightening-only. *(§4.2(ii) — depends on 4 and 9)*
14. Per-tenant LLMOps surface. *(§4.3 — depends on 6)*

**Stage 4 — the rest.**
15. Named seats. *(§4.4 — depends on 4)*
16. Red-team parameterisation + history. *(§4.6)*
17. Live cache surface. *(Part 6.4)*
18. Admin forecast: SHAP panel + `POST /ml/experiment` + forecast CSV. *(§4.7 — the experiment endpoint depends on plan 04's job substrate)*
19. Document lifecycle. *(T9 — depends on Phase 3.5)*
20. Tenant suspend + user deactivate. *(P3, P5)*

**The one ordering mistake to avoid:** building any per-tenant screen before step 2 and step
6. A per-tenant surface tested with one tenant, over a cache that ignores tenancy, is a
surface that looks correct and is not — and this project has been burned by exactly that class
of defect more than once.

---

# Part 8 — Decisions I cannot make alone, with my defaults

| # | Question | My default | Why you might overrule it |
|---|---|---|---|
| 1 | Does the `client` role get the **full** console (agent cards, trace tabs, harness detail) or a simplified one? | **Full, minus the harness tab.** Complexity is the product's story; hiding it makes the client portal feel like a toy | If the client persona is meant to read as a non-technical business user, a two-tab console may demo better |
| 2 | Do tenants get their own **sub-agent roster**, or only the platform's? | **Platform's only.** A tenant selects from the roster; it does not define specialists | If "the tenant configures their own agents" is a headline claim you want to make, this changes |
| 3 | Where does the **tenant-admin approvals inbox** live — its own section or a tab on Governance? | **Its own section.** It is the tenant admin's primary job and the most jury-legible screen in Phase 6 | If nav width is a concern |
| 4 | Should `Deep` mode exist, or collapse to Fast/Auto/Team/Custom? | **Keep it.** Both knobs already exist and "think harder in one lane" is what Expert/Thinking means in every comparable product | If four modes test cleaner than five |
| 5 | Is `POST /ml/experiment` synchronous-with-a-thread or a job? | **A job**, on plan 04's substrate | If the substrate slips, a `to_thread` with a hard timeout and a small feature cap is an acceptable interim — say so on screen |
| 6 | Do tenant-authored **custom rails** ship at all, or only the toggles + denylist + PII entities? | **Templates only, in the first cut.** Toggles, denylist and extra PII entities cover most real need with none of the ReDoS/RCE surface | If a jury question about "can a tenant write their own policy" is expected, the template picker is the answer to have ready |
| 7 | Does a platform admin get **act-as-tenant** (support impersonation)? | **No, not for the hackathon.** It is genuinely useful, and it is an audit and consent problem that needs more thought than it will get | If demo-day debugging across tenants proves painful, a read-only act-as with a loud banner and a mandatory audit row is the safe shape |
| 8 | Is the settings catalogue **complete** at launch or grown incrementally? | **~20 keys covering agent mode, tools, model defaults, guardrail toggles and seats**, with the bijection test from day one | A larger catalogue is more impressive and much more tedious; the test is what stops it rotting either way |

---

# Appendix A — the probes, reproducible

All read-only. No DDL was run against `taif`.

**Probe 1 — routes and their guards** (produced the table in §0.2):

```bash
python3 - <<'EOF'
import re
src = open('backend/src/app/api/routes.py').read().splitlines()
for i, l in enumerate(src):
    m = re.match(r'@router\.(get|post|put|patch|delete)\((.*)', l)
    if not m: continue
    j, buf = i, l
    while buf.count('(') > buf.count(')'):
        j += 1; buf += ' ' + src[j].strip()
    path = re.search(r'"([^"]+)"', buf)
    guards = set()
    for k in range(j + 1, min(j + 30, len(src))):
        guards.update(re.findall(r'Depends\((\w+)\)', src[k]))
        if src[k].startswith(') ->'): break
    print(f"{m.group(1).upper():6} {path.group(1):38} {','.join(sorted(guards)) or 'PUBLIC'}")
EOF
```
→ 57 routes; `require_platform_admin` appears twice.

**Probe 2 — row counts** (§0.4):

```bash
for t in users tenants audit_log prompt_versions usage_ledger budgets approvals memory_fact chunks; do
  printf "%-18s " $t; psql -U yrevash -d taif -At -c "select count(*) from $t;"
done
```
→ `audit_log 46`; every other table `0`.

**Probe 3 — which tables carry `tenant_id`** (§0.5):

```bash
psql -U yrevash -d taif -At -c \
  "select table_name from information_schema.columns
   where table_schema='public' and column_name='tenant_id' order by 1;"
psql -U yrevash -d taif -At -c \
  "select column_name from information_schema.columns
   where table_name='chunks' order by ordinal_position;"
```
→ 13 tables, exactly matching `rls.py:103-121`. `chunks` = `id, doc_id, persona, content,
embedding, meta` — **no `tenant_id`**. The ten `lightrag_*` tables are absent from the first
list.

**Probe 4 — which API-client functions have a call site** (§0.3):

```bash
grep -rn "createBudget\|postApproval\|assignUserRole\|createUser\|createTenant" web/src \
  | grep -v "lib/api/client.ts"
```
→ `createBudget`: no hits. `createUser` / `createTenant`: not defined at all.

---

# Appendix B — sources

**Repository** (all `[SOURCE]`, read during this work):
`web/src/lib/portal.ts` · `web/src/app/app/[role]/[section]/page.tsx` ·
`web/src/lib/api/client.ts` · `web/src/components/{admin,cache,shared,console,memory}/*` ·
`backend/src/app/api/routes.py` · `backend/src/app/api/schemas.py` ·
`backend/src/app/agent/deps.py` · `backend/src/app/guardrails/__init__.py` ·
`backend/src/app/adapter/memory_spec.py` · `backend/src/app/data/models.py` ·
`backend/src/app/retrieval/pipeline.py` · `backend/src/app/mcp/server.py` ·
`aegis/src/aegis/governance/{types,rls}.py` · `aegis/src/aegis/agent/{deps,harness}.py` ·
`aegis/src/aegis/guardrails/pipeline.py` · `aegis/src/aegis/memory/{recall,stores,crud}.py` ·
`aegis/src/aegis/ops/{models,registry,config}.py` · `aegis/src/aegis/ml/{model,spec}.py` ·
`aegis/src/aegis/redteam/{battery,runner}.py` · `aegis/src/aegis/retrieval/{types,lightrag_backend}.py`

**Sibling plans, built on and not re-planned:**
[`plans/04-enterprise-substrate.md`](04-enterprise-substrate.md) (jobs, health, DB page,
request tracking) · [`plans/05-modularity-scale.md`](05-modularity-scale.md) (module contract,
API versioning) · [`phase-03-ingestion-v2.md`](../phase-04-ingestion.md) ·
[`phase-04-multi-agent.md`](../phase-05-multi-agent.md) ·
[`phase-05-console.md`](../phase-06-console.md) ·
[`phase-06-admin-surfaces.md`](../phase-07-control-planes.md) ·
[`backlog-post-hackathon.md`](../backlog-post-hackathon.md)

**External** (all `[EVIDENCE]`, retrieved 2026-08-17):
- Grok's mode switch — Auto / Fast / Expert / Heavy, with "Heavy = team of experts":
  [Grok's Heavy, Expert, Fast and Auto Modes: A Practical Guide](https://mundobytes.com/en/What-are-the-heavy--expert--fast--and-auto-modes-in-Grok-used-for/) ·
  [Model Selection — Grok AI UX Case Study](https://aiuxplayground.com/gallery/grok-model-selection/) ·
  [Thinking Mode and Expert Mode — Grokipedia](https://grokipedia.com/page/Thinking_Mode_and_Expert_Mode)
- ChatGPT's Auto router and the quota asymmetry (auto escalation is free, manual is charged):
  [ChatGPT 5 Modes: Auto, Fast (Instant), Thinking, Pro — TTMS](https://ttms.com/chatgpt-5-modes-auto-fast-instant-thinking-pro-which-mode-to-use-and-why/) ·
  [Updates to the model picker in ChatGPT — OpenAI Help Center](https://help.openai.com/en/articles/6825453-chatgpt-release-notes) ·
  [ChatGPT just made it easier to pick the right model — TechRadar](https://www.techradar.com/ai-platforms-assistants/chatgpt/chatgpt-just-made-it-easier-to-pick-the-right-model-just-like-gemini-does-heres-when-to-use-instant-thinking-or-pro)
- Claude's three-settings picker (model · effort · extended thinking) with tools on a separate
  surface:
  [Change the model, effort, and thinking settings — Claude Help Center](https://support.claude.com/en/articles/8664678-change-the-model-effort-and-thinking-settings) ·
  [Claude's extended thinking — Anthropic](https://www.anthropic.com/news/visible-extended-thinking)

**Evidence quality, stated honestly:** the three products' UI details come from documentation
and secondary write-ups, not from operating the products during this work. The *shape* of the
convergence — an Auto router plus a preserved manual override, strategy separated from model,
tools on their own surface — is corroborated across all three independent sources and is what
the design in Part 2 rests on. The finer details (Grok's exact dropdown wording, OpenAI's
exact quota rule) come from single sources each and should be treated as directional rather
than specification.
