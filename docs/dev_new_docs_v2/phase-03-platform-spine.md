# Phase 3 — The platform spine

**Everything in phases 4–9 depends on at least one piece of this. Build it first.**

Six pieces, one theme: Aegis has no substrate for durable work, no per-tenant configuration,
and — measured — no tenants.

Research behind it: [`plans/04-enterprise-substrate.md`](plans/04-enterprise-substrate.md) ·
[`plans/06-dashboards-control.md`](plans/06-dashboards-control.md) ·
[`plans/05-modularity-scale.md`](plans/05-modularity-scale.md)

---

## What is actually wrong

### 1. There is no job substrate

One job pattern exists — `aegis/src/aegis/memory/consolidate.py:983-1005` — and it is
SELECT-then-guarded-UPDATE: N+1 round trips, N−1 losers per batch. It has **no lease, no
heartbeat, no `claimed_at`, no reaper**. A worker killed mid-job strands its row in `RUNNING`
forever, matched by no sweeper and retried by nothing — the same shape as the `RESUMING` hazard
Phase 1 fixed in approvals.

`attempts` is incremented at `consolidate.py:1005` and **read nowhere**, so a poison job that
crashes the worker every time is invisible.

Ingestion (Phase 4) needs durable, resumable, observable jobs. There is nothing to build on.

### 2. Every run is forgotten

Three tracking mechanisms exist — OTel spans, Phoenix, the SSE stream — and **none is durable
and tenant-scoped**. Phoenix is an ephemeral deep-dive. The SSE stream is gone when the socket
closes. `latency_summary` is a per-process RAM deque that resets on restart.

Five later features all need the same thing and would otherwise each invent it: the harness,
replay, per-agent inspection, audit depth, per-tenant LLMOps evidence.

### 3. There is no per-tenant configuration

The "0 code change from the dashboard" goal has nowhere to store a per-tenant guardrail, a model
default, a feature toggle or a capability. Every such feature would invent its own storage.

### 4. There are no tenants

Measured against live `taif`, 2026-08-17:

| Table | Rows |
|---|---|
| `users` | **0** |
| `tenants` | **0** |
| `budgets` | **0** |
| `prompt_versions` | **0** |
| `usage_ledger` | **0** |
| `audit_log` | 46 |

Every login today falls back to `_DEMO_USERS`. **Nothing per-tenant has ever run with a
tenant** — so none of the isolation Phase 1 built has been exercised end to end.

### 5. The browser cannot tell the two admin tiers apart

`LoginResponse` never sends `fine_role`. `ROLES` carries four coarse values. Every per-tenant
control in phases 6 and 7 depends on that distinction reaching the client, and it does not.

### 6. The client cannot ask a question

```ts
// web/src/lib/portal.ts:282
client: ['dashboard', 'savings', 'forecast', 'risk', 'simulation'],
```

No `console` entry. **The role the product exists for cannot use the product.** Nothing tests
that a role can reach the surfaces it is supposed to have.

### 7. An AI integrator fails on line one

`aegis/README.md:62` tells an integrator to call `aegis.require()`. **It does not exist** — it
is `aegis.core.require`. Verified: `hasattr(aegis, 'require')` is `False`.

The adapter modules claim "piece 2 of 5", "3 of 5", "4 of 5" and "**6 of 6**" in the same
directory. There are **eight** modules plus `corpus/` and `skills/`, and `roster.py` and
`skills/` appear in no checklist.

`aegis` has no `py.typed`, so every annotation is invisible to a type checker — including an
integrator's.

---

## Why this exists — the real workloads, not a demo checkbox

The substrate is not enterprise theatre. It is what makes ingestion and agent work *correct*.
Every item below is real work that today either does not exist, or runs as a fire-and-forget
`asyncio.create_task` that dies with the process.

### Ingestion (Phase 4) is the primary consumer

A single document is a **multi-stage pipeline that cannot live in an HTTP request**:

```
parse (Docling, CPU-bound, ~1.1 s/page)
  → chunk → enrich → embed (batched, rate-limited, billed)
  → index (vector + FTS) → graph build
```

A 200-page PDF is minutes of work. What the substrate gives it, and nothing else does:

| Need | Why it must be a durable job |
|---|---|
| **Resumability** | A failure at the graph stage must not re-parse 200 pages. Stage progress is on the row. |
| **Serialisation** | Docling is CPU-bound and single-process. Two concurrent parses on a 16 GB box contend; the queue is what serialises them without dropping work. |
| **Batching** | Embedding calls batch across chunks *and across documents in the queue* — real API round-trip and cost savings, only possible if the work is queued rather than per-request. |
| **Multi-document upload** | A tenant dropping ten files gets ten queued jobs with visible positions, not ten timeouts. |
| **The live log** | The tenant watching ingestion is reading job progress. Without a job row there is nothing to read. |
| **Re-indexing on a cadence** | Yash's own requirement: *"re indexing pipeline can be structured in a way that it runs in set duration and in the meantime user own db take care."* That is the scheduler, and it is why the scheduler is in this phase and not deferred. |
| **Cost control** | Ingest spend is metered against the tenant budget *before* the job runs, not discovered afterwards. |

### Agent work is the second consumer

The multi-agent fan-out itself is live and in-request (`asyncio.gather` inside one node — Phase
5). But the work *around* it belongs on the substrate:

| Workload | Today | Why it needs a job |
|---|---|---|
| **Parked-run resumption** after approval | In-process `ParkedRunRegistry` + TTL eviction | A run parked before a restart is currently only recoverable because the checkpoint survives. The resume itself should be a durable job, not a handle in RAM. |
| **Memory consolidation** | `asyncio.create_task`, own weaker claim, **no lease** | Makes live billed model calls. A killed worker strands it forever today. |
| **Post-run trace eval** | Fire-and-forget task | Silently lost on restart; it is the evidence behind per-tenant quality claims. |
| **Report generation** | Does not exist | Forecast/audit/tenant reports over large ranges are not request-shaped. |
| **Red-team battery** | Does not exist as a triggerable run | Long-running, and the infra profile is meant to initiate it. |
| **Model-call concurrency** | Nothing | Five users × four agents is twenty concurrent gateway calls. A shared limiter is what stops rate-limit failures on stage, and it lives with the worker pool. |

`backend/src/app/main.py` currently starts **four** `asyncio.create_task` loops — the SLA
sweeper, the memory sweeper, and the ML warm — each of which its own docstring at `main.py:69`
identifies as *"a silent-failure seam"*. The substrate replaces the pattern, not just the
individual tasks.

### What this means for how it is built

Because ingestion and agent work are the consumers, the substrate is **not** allowed to be the
minimum thing that passes a test:

- **Stage-level progress on the job row**, so a resumed ingest restarts at the failed stage.
- **Batching is a first-class feature**, not an optimisation — embedding cost depends on it.
- **Two separate concurrency numbers**, because one is not enough. `concurrency` is per job
  *type* (Docling parses serialise at 1; embed calls do not) and `worker_concurrency` is how many
  slots a single worker process runs. DBOS validates one against the other; we should too, or a
  worker with 8 slots quietly ignores a type limit of 1.
- **Budget context travels with the job** (Phase 9 hardens this), because every consumer above
  spends money.

---

---

---

## Build vs buy — decided: Temporal orchestrates, Postgres records

24 frameworks surveyed. Full report:
[`research/job-framework-survey.md`](research/job-framework-survey.md).

**Decision: adopt Temporal for execution, keep our own tenant-scoped tables as the system of
record.** An earlier draft of this phase rejected Temporal. That rejection was an architecture
error and is corrected here.

### Verified, not assumed

| | |
|---|---|
| Licence | **MIT** — server, Python SDK and CLI all three (GitHub API, 2026-08-18) |
| Windows install | one **42.2 MB** zip, no installer, no runtime dependencies |
| Dev server | **0.2 s** to accepting connections, **123 MB** RSS (155 MB after a run) |
| Python SDK | `temporalio` win_amd64 wheel exists; **+3 packages**, zero conflicts against Aegis's 243-package graph |
| Resumability | **Measured**: worker hard-killed mid-run; `parse`/`chunk`/`embed` did **not** re-run, only the in-flight activity replayed |
| Server maturity | 22.4k stars, used in production by companies operating far past our scale |

### The error the earlier draft made

It assumed Temporal *replaces* our tables and concluded that job state would sit outside RLS.
**Temporal is not a database, and its own documentation says not to use it as one.** The standard
pattern — the one every multi-tenant adopter uses — is:

> **Temporal owns execution state. Postgres owns business state.**

```
documents      (tenant_id, status, completed_stage, workflow_id)   ← ours · RLS · joinable
ingest_runs    (tenant_id, workflow_id, cost_usd, started_at)      ← ours · joins to budgets
      │
      └── workflow_id ─▶ Temporal: retries · timers · resumability · cancellation
```

The workflow id encodes the tenant (`ingest:{tenant_id}:{document_id}`), activities run with
`set_tenant_scope` bound and write to **our** tables, and Temporal stores only the execution
record. Every objection the earlier draft raised dissolves: RLS protects our rows, the console
joins them to `budgets`, the admin DB page sees them, and the tenant's live log reads
`completed_stage` off a tenant-scoped row.

Temporal can also use **the same Postgres server** for its own persistence, so this is one
database server with two schemas, not two databases.

### What survives as a real cost

- **Two places to look for "what happened".** Our `run_events` carries the business narrative;
  Temporal's Web UI carries execution detail. Arguably a feature — that UI is a free replay
  surface this phase would otherwise hand-build.
- **The workflow sandbox re-imports the defining module.** A module doing side-effectful work at
  import fails validation. Workflow definitions live in import-safe modules; this is a real
  ergonomic tax on a codebase that does side-effectful imports, and §3.0 exists to hit it early.
- **One more process on the demo box**, at 123 MB.
- **No `win_arm64` wheel** — only matters if the laptop is ARM. Confirm this.

### What was rejected, and on what grounds

| Candidate | Failed on |
|---|---|
| procrastinate, pgqueuer, DBOS, SAQ, pgmq | **Zero `tenant_id` columns.** `rls.py:327` classifies a table with no tenant column as *not tenant-scoped* and continues **before recording a gap** — so their tables would be invisible to the very diagnostic built to catch that |
| Celery | Its own FAQ still says Windows is unsupported |
| APScheduler 4.0 | Still **alpha** (`4.0.0a6`); 3.x jobstores are not multi-scheduler safe |
| RabbitMQ | Needs Erlang; and a broker cannot commit a job with the data it touches |
| Redis Streams via Memurai | Job state in Redis, business data in Postgres — they can never commit together |
| `pg_cron` | Schedules SQL, not Python |
| Restate | **No Windows artefact of any kind** |
| Hatchet | Docker-only |
| Windmill | Windows binary is enterprise-only |
| Dagster, Prefect | Data-asset orchestrators — wrong tool for jobs |

**Temporal is the only candidate that lost on architecture rather than cost — and re-examining
that architecture is what reversed the decision.**

---

## Tasks

| # | Task | Days |
|---|---|---|
| 3.0 | Temporal spike on the real Windows box | 0.25 |
| 3.1 | The record tables — `documents`, `job_runs`, tenant-scoped and RLS-registered | 0.75 |
| 3.2 | Temporal wiring — worker bootstrap, tenant-scoped activities, the stage contract | 1.0 |
| 3.3 | Idempotent activities and the reconciler | 0.5 |
| 3.4 | Admission control, budget pre-authorisation, cancellation | 0.5 |
| 3.5 | Temporal Schedules — re-index cadence with debounce | 0.25 |
| 3.6 | `run_events` and the `runs` header | 0.75 |
| 3.7 | The settings catalogue | 1.0 |
| 3.8 | The two-tenant seed | 0.5 |
| 3.9 | `fine_role` on the wire | 0.25 |
| 3.10 | The client console + route-coverage test | 0.25 |
| 3.11 | `py.typed` and the four documentation lies | 0.25 |

**Total: 6.0 days** — up from 5.25, because the tables and the reconciler are real work even
though lease/reaper/fencing/scheduler are now Temporal's problem. The trade is **~2.5 days of the
most bug-prone code in the phase** for ~0.75 days of integration.

### 3.0 — The spike, on the actual Windows box (0.25d)

Everything measured so far was on macOS. Before anything is built on it:

- Temporal dev server running **alongside** Postgres, Neo4j Desktop and Memurai. Record total RSS.
- **Hit the sandbox import trap deliberately.** Define a workflow in a module that imports
  something side-effectful and confirm it fails; then confirm the import-safe layout works. This
  is the known ergonomic tax and it should cost an hour now, not a day in Phase 4.
- One ingestion-shaped workflow writing to a tenant-scoped table, killed mid-run, resumed.
- **Confirm the laptop is x64, not ARM** — there is no `win_arm64` wheel.

If the spike fails, the fallback is the hand-rolled substrate the earlier draft specified. It is
written down in git history and remains buildable.

### 3.1 — The record tables (0.75d)

**These are ours and they are the system of record.** Temporal never becomes the place you look
to answer "what does this tenant have".

| Table | Carries |
|---|---|
| `documents` | `tenant_id`, `status`, `completed_stage`, `workflow_id`, source metadata |
| `job_runs` | `tenant_id`, `workflow_id`, `job_type`, `cost_usd`, timings, `error` |

**Both registered in `_TENANT_SCOPED_TABLES`**, both with an RLS policy, both covered by the
live isolation test from Phase 1. The `workflow_id` column is the only link to Temporal, and it
is a string — no foreign key into a system we do not own.

`job_runs` is what the pipeline-health page (Phase 7) and the tenant's live log read. It is also
what joins to `budgets` for Phase 9.

### 3.2 — Temporal wiring (1.0d)

**Worker bootstrap.** One worker process type, launched in-process for the demo and standalone
via `python -m aegis.jobs.worker`. Temporal handles the pool; we handle the wiring.

**Tenant scope on every activity.** The workflow id is `{job_type}:{tenant_id}:{entity_id}`, and
**every activity opens its session with `set_tenant_scope` bound from the workflow argument** —
not from ambient context, which does not survive a replay in a new process.

> This is the single most important rule in the integration. An activity that forgets the scope
> is an activity running unscoped on the serving engine, and Phase 1 exists to make that
> impossible to do accidentally. Make it structural: one decorator that binds scope and refuses
> to run without a tenant argument.

**The stage contract.** A job type declares its stages as an ordered tuple:

```python
INGEST_STAGES = ("parse", "chunk", "enrich", "embed", "index", "graph")
```

Each stage is one Temporal activity. The activity writes its output **and** bumps
`documents.completed_stage` in **one transaction**. Temporal gives resumability across stages;
that single transaction is what makes each stage individually correct.

**Task queues carry the concurrency policy.** A `docling-parse` queue with a worker configured
for one concurrent activity is how CPU-bound parses serialise, while an `embed` queue runs many.
This is the "two separate numbers" requirement, and Temporal expresses it natively.

### 3.3 — Idempotent activities and the reconciler (0.5d)

**The one genuinely new problem this architecture introduces.** An activity can commit to
Postgres and then die before Temporal records its completion — so on replay it runs again.

**Therefore every activity must be idempotent**, keyed on `(workflow_id, stage)`. Writing chunk
rows for a document that already has them is an upsert, not an insert. This is Temporal's
standard requirement and it is well-understood, but it must be stated as a rule rather than
discovered per activity.

**The reconciler** covers the opposite skew: a `documents` row stuck in a stage whose workflow no
longer exists (server wiped, workflow terminated externally). A periodic sweep asks Temporal for
the workflow's status and either resumes, fails the row with a reason, or leaves it alone. It is
small, and without it a stuck row is invisible — the same silence the reaper existed to break.

### 3.4 — Admission control, budget pre-auth, cancellation (0.5d)

**Still ours, because these are tenant policy, not execution mechanics.**

- **Admission control** — a per-tenant cap on in-flight workflows, enforced before starting one,
  returning a **visible 429**. Invisible backpressure is the same defect as a silent fallback.
- **Budget pre-authorisation** — estimated cost checked against the tenant's remaining budget
  *before* the workflow starts. Phase 9 hardens the per-call path; this is the gate at the door.
- **Cancellation** — a Temporal cancellation signal, surfaced as a button. Our `job_runs` row
  records who cancelled and when; Temporal stops the work.

### 3.5 — Temporal Schedules, with debounce (0.25d)

Re-indexing on a cadence is a **Temporal Schedule**, not a table we maintain.

**Debounce is ours and is not the same as idempotency.** Idempotency says "this exact work is
queued, return it". Debounce says "work of this kind is already pending for this tenant; fold
this request in and push the run time out". Ten documents uploaded in a minute produce **one**
re-index. Temporal expresses this with a workflow id per tenant plus
`WorkflowIDReusePolicy`, so the second start joins the first rather than queueing behind it.

### 3.6 — `run_events` and the `runs` header (0.75d)

Exactly as [`plans/02`](plans/02-agentic-core-console.md) §2.2 specifies, plus `trace_id`,
`span_id`, `job_id`, and a `runs` header row.

**Partition by month at creation.** This is the one irreversible decision in the roadmap:
converting a large heap table later needs a migration, and `backend/pyproject.toml` deliberately
has no Alembic.

The `runs` header is a second table against the one-mechanism rule, so it earns its place
explicitly: it is a **regenerable projection**, and it ships with a test that rebuilds it from
events. If the two ever disagree, events win.

Phoenix stays the ephemeral deep-dive. `run_events` is the durable, tenant-scoped, replayable
record.

### 3.7 — The settings catalogue (1.0d)

A `settings` table scoped platform / tenant / user, plus a **`SettingSpec` catalogue** declaring
`key`, `type`, `bounds`, `writable_by`, `readable_by`, `merge`.

**Generalise the existing pattern, do not invent a second.** `_KNOB_SPECS` / `harness_config()`
already does exactly this, with a bijection test that makes a knob impossible to add without a
UI control appearing.

`resolve(key, tenant, user)` returns `(value, source)` so a screen can always say *where* a
value came from.

**`merge: tighten_only` is the load-bearing part.** It makes the tenant-safety rules executable
configuration rather than prose: the resolver **cannot compute a value weaker than the platform
default**. That is the mechanism behind "a tenant may add a guardrail but never weaken one", and
it is why Phase 7's fifteen forbidden controls are catalogue entries rather than fifteen
hand-written checks.

### 3.8 — The two-tenant seed (0.5d)

Two tenants, a tenant admin and two users in each, budgets, and a few documents per tenant.
Idempotent, runnable as `python -m app.seed`.

Without it, Phase 1's isolation is untested end to end and every per-tenant screen in phases 6
and 7 has nothing to render.

**Delete the `_DEMO_USERS` fallback in the same change**, or the seed is optional and nobody
runs it.

### 3.9 — `fine_role` on the wire (0.25d)

Add `fine_role` to `LoginResponse` and thread it to the client. This is the smallest change that
unblocks the most rows in Phase 7's gap list.

### 3.10 — The client console, and the test that stops this recurring (0.25d)

Add `console` to `ROLE_SECTIONS.client`.

Then the part that matters more: a **route-coverage test** asserting every non-public endpoint
is reachable from some portal, and every role can reach every section it is supposed to have.
**It would fail today on at least eight endpoints.** A one-line fix without the test just waits
to happen again.

### 3.11 — `py.typed` and the four documentation lies (0.25d)

- Add `py.typed` to `aegis`. One line, and it makes every existing annotation visible to an
  integrator's type checker.
- Fix `aegis/README.md:62` — `aegis.require()` → `aegis.core.require()`.
- Fix the adapter piece counts. There are **eight** modules plus `corpus/` and `skills/`. Say
  eight everywhere, and add `roster.py` and `skills/` to the checklist.

Small, and it is the difference between an AI integrator succeeding on the first attempt and
hitting `AttributeError` on line one.

---

## Definition of done

- [ ] Temporal dev server runs on the Windows box alongside Postgres, Neo4j and Memurai; total RSS recorded.
- [ ] A workflow killed mid-run resumes **without re-running completed stages** — verified by killing the process, not by asserting a code path.
- [ ] An activity **cannot** run without a tenant argument — the decorator refuses, and there is a test proving it.
- [ ] Two tenants' workflows write to `documents`/`job_runs` and each sees only its own — the Phase 1 live isolation test, extended to both new tables.
- [ ] Every activity is idempotent: running it twice for the same `(workflow_id, stage)` produces one result, tested by invoking it twice.
- [ ] A `documents` row whose workflow no longer exists is reconciled — resumed or failed with a reason, never left stuck.
- [ ] A CPU-bound parse queue with one slot never runs two parses at once.
- [ ] Ten re-index requests inside the debounce window produce **one** run.
- [ ] Exceeding a tenant's admission cap returns a visible **429**, not a silent queue.
- [ ] `run_events` is partitioned by month; the `runs` header rebuilds from events in a test.
- [ ] `resolve()` returns `(value, source)`; a tenant cannot set a value weaker than the platform default for a `tighten_only` key.
- [ ] The bijection test covers the settings catalogue.
- [ ] `python -m app.seed` produces two tenants; `_DEMO_USERS` is gone.
- [ ] `fine_role` reaches the browser; the route-coverage test passes.
- [ ] `aegis.core.require` is what the README says; the adapter says eight everywhere.
- [ ] Full suites green, ruff clean, `next build` green.

## Demo at the end of this phase

Log in as tenant A's admin and as tenant B's admin. Same screens, different data, and the
`(value, source)` badge showing which settings are platform defaults and which the tenant
changed. Then kill a worker mid-ingest and watch the job get reclaimed and finish.

That is the first time the platform demonstrably has tenants at all.

## Risks

**The sandbox import trap is the most likely thing to cost a day.** Aegis has modules that do
side-effectful work at import, and the workflow sandbox re-imports the defining module. §3.0
exists to hit this in the first hour rather than in Phase 4.

**Dual-write skew is real and must be designed for, not discovered.** An activity can commit to
Postgres and die before Temporal records completion. Idempotent activities keyed on
`(workflow_id, stage)` are the answer, and §3.3's reconciler covers the opposite skew. An
activity written non-idempotently will look correct until the first crash.

**Temporal is now a second thing that must be up on stage.** Measured at 123 MB and 0.2 s
startup, so the cost is small — but the demo runbook needs a start step and preflight needs a
probe.

**`win_arm64` has no wheel.** Confirm the laptop is x64 in §3.0. If it is ARM, the fallback is the
hand-rolled substrate specified in the earlier draft of this file, which remains in git history.

**Scope binding is the one place a mistake is silent.** An activity that forgets
`set_tenant_scope` runs unscoped on the serving engine. Make it structural — a decorator that
refuses to run without a tenant argument — rather than a convention every activity must remember.

**`run_events` partitioning is irreversible.** Get it right at creation; there is no migration
tool.
