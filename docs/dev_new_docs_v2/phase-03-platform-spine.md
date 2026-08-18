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

## Build vs buy — settled, and why

24 frameworks were surveyed after the first draft of this phase was challenged for having
compared only seven. Full report: [`research/job-framework-survey.md`](research/job-framework-survey.md).

**The decision stands. The original reasoning did not**, and two of its three arguments were
wrong in the over-conservative direction this project keeps correcting:

- **The pgmq rejection was factually wrong.** It ships a SQL-only install — one
  `psql -f pgmq.sql`, no compiler, no `CREATE EXTENSION`. Right conclusion, wrong reason.
- **"No new infrastructure on Windows" is not the argument.** Measured: Temporal is a **42.2 MB
  zip, zero dependencies, 123 MB RSS, ready in 0.2 s**, +3 packages against Aegis's 243-package
  graph with no conflicts. NATS is one `.exe` with Windows-service support. Install cost was
  never the binding constraint.

### The one criterion that decided it

`aegis/src/aegis/governance/rls.py:327` classifies a table with **no tenant column** as *not
tenant-scoped* and `continue`s past it **before any gap is recorded**.

So a queue whose tables carry no `tenant_id` is not merely unprotected — it is **invisible to
the diagnostic built to catch exactly that**. Verified by grep in a throwaway venv: procrastinate
0 tenant hits (4 tables, 39 migrations), pgqueuer 0, DBOS 0, SAQ 0. No library ships a column for
someone else's tenancy model, and adding one means forking a schema we do not own.

That is the same failure class as "RLS is inert under a superuser": the check reads healthy while
the thing it protects is off.

### Temporal, measured and then declined

The strongest candidate, and the test was run rather than argued: a 5-activity workflow named
after our stages, worker **hard-killed mid-run**, restarted in a new process. `parse`, `chunk`
and `embed` did **not** re-run; only the in-flight `index` replayed, then `graph` finished. That
is this phase's hardest definition-of-done item and Phase 4's resumability requirement, with
zero substrate code.

Declined on architecture, not cost. **Workflow state lives inside Temporal, not inside
Postgres**, which means:

| | |
|---|---|
| No row for RLS to protect | Phase 1 made tenant isolation provable; Temporal has namespaces, not `tenant_id` |
| Nothing to join to `budgets` | Phase 9 needs budget context on every model-calling job |
| Invisible to the admin DB page | The requirement is "view the full db", and half the platform's work would not be in it |
| No row for the tenant's live log | Ingest progress must be tenant-scoped and queryable |

**And it does not replace the `jobs` table** — admission control, budget pre-authorisation,
cancellation and the tenant-visible log all still need tenant-scoped rows. Adopting it means
operating **two** substrates, which breaks the one-mechanism rule and splits every question
("what is queued? what did it cost? who owns it?") across two systems.

Stated plainly: **if Aegis were single-tenant, Temporal beats this phase and the recommendation
would be to use it.** Multi-tenancy decides it.

**Therefore §3.2b exists.** The stage machine is designed as the *portable subset*, so a future
Temporal adoption is a driver swap rather than a rewrite.

---

## Tasks

### 3.1 — The `jobs` table and the claim (1.0d)

One table. Claim with a **single statement**:

```sql
UPDATE jobs
   SET status='running',
       lease_until = now() + make_interval(secs => $2),
       attempts = attempts + 1,
       worker_id = $3
  FROM (SELECT id FROM jobs
         WHERE status='pending' AND run_after <= now()
         ORDER BY priority DESC, run_after
           FOR UPDATE SKIP LOCKED
         LIMIT $1) AS c
 WHERE jobs.id = c.id
RETURNING jobs.*;
```

Measured in the research: **20,877 claims/s** single, **128,085/s** batched, zero duplicate
claims across every configuration, on a partial index. The workload is tens of LLM-bound jobs
per minute.

**Do not copy `consolidate.py`'s shape.** SELECT-then-UPDATE is N+1 round trips and N−1 losers.

Columns the substrate needs, and why each: `idempotency_key` (unique — a re-enqueue must not
double-charge) · `priority` · `run_after` (backoff and scheduling in one column) · `attempts` ·
`max_attempts` · `lease_until` · `worker_id` · `tenant_id` (RLS) · `payload` · `result` ·
`error` · `cancel_requested`.

**Register `jobs` in `_TENANT_SCOPED_TABLES`** or the boot-time catalog read-back reports it as
an unprotected tenant-scoped table.

### 3.2 — Lease, reaper, retry, dead-letter (0.75d)

**SKIP LOCKED is the claim, not the substrate.** It stops two *live* workers taking a row; it
does nothing about a worker that dies holding one.

- **Reaper**: a periodic sweep returning `running` rows whose `lease_until` has passed to
  `pending`, incrementing nothing (the claim already counted the attempt).
- **Heartbeat**: a long job extends its own lease. A job that cannot heartbeat is a job the
  reaper will correctly reclaim.
- **Retry**: jittered exponential backoff written into `run_after`. Jitter matters — without it
  N failures retry simultaneously against a recovering dependency.
- **Dead-letter is a status, not a table.** One place to look.
- **Consult `attempts`.** A job at `max_attempts` goes to `dead` with its last error. The
  current code increments a counter nothing reads, which is why a poison job is invisible today.

**Fix `memory_consolidation_job` in the same change** — migrate it onto the substrate rather
than leaving a second, weaker job system beside the new one.

### 3.2b — The stage machine (0.75d) — **the biggest gap in the first draft**

The first draft said "stage progress is on the row" and never defined the mechanism. Every
surveyed framework has one; this is what makes a job *resumable* rather than merely *retried*.

**A job type declares its stages as an ordered tuple:**

```python
INGEST_STAGES = ("parse", "chunk", "enrich", "embed", "index", "graph")
```

The row carries `completed_stage`. A retry resumes at the first stage **after** it — so a
failure in `graph` does not re-parse 200 pages, which at ~1.1 s/page is a four-minute penalty
for a ten-second bug.

**Three rules that make it correct rather than decorative:**

- **A stage is committed with its own output.** The `completed_stage` bump and whatever that
  stage produced are one transaction. A stage that "finished" but whose output was rolled back
  is the bug this design exists to prevent.
- **Stages are declared, not inferred.** The tuple is the contract; the health page and the
  console read it, so a new stage appears in the UI by declaration.
- **Design it as the portable subset.** Stage names, order, and "resume after the last committed
  stage" are exactly what a durable-execution engine gives. Keeping the shape compatible means
  swapping the driver later, not rewriting the pipeline.

### 3.2c — `lease_epoch`, the fencing token (0.25d)

`worker_id` + `lease_until` tell you *when* a lease expired. They do not tell you **who is
allowed to write the result.**

The window: worker A's lease expires, the reaper requeues, worker B claims and starts — then
worker A wakes from a slow network call and writes its result. Two workers, one job, last write
wins.

**The fix:** an integer `lease_epoch` incremented on every claim. Every write carries the epoch
it was claimed under, and `WHERE lease_epoch = $n` makes a stale writer's update affect zero
rows. It then logs and exits rather than pretending it succeeded.

**No test that does not kill a process will find this**, which is exactly why it is a named task
rather than something to notice later.

### 3.3 — Idempotency, priority, admission, cancellation (0.5d)

- **Idempotency key** unique per tenant. Re-enqueueing the same logical work is a no-op that
  returns the existing job.
- **Priority** as an integer, ordered before `run_after`.
- **Admission control**: a per-tenant cap on queued jobs, returning a visible **429**.
  Backpressure that is invisible is the same defect as a silent fallback.
- **Cooperative cancellation**: `cancel_requested` is a flag the job body checks at its own
  safe points. A user closing a tab should stop the spend.

### 3.4 — The worker, and how it runs on Windows (0.5d)

**One implementation, two launch modes:**

1. An in-process asyncio task inside the API — what the two existing sweepers already do, and
   what runs on demo day.
2. `python -m aegis.jobs.worker` standalone.

Identical code path. **The database is the only coordination**, so N workers in M processes is
safe by construction with no leader election, ever. NSSM documented for the always-on case.

**Wakeup:** `LISTEN/NOTIFY` with a **polling floor**. The floor is what survives a dropped
notification — a queue that only wakes on notify silently stalls.

**The tenancy trap, non-negotiable.** Claim runs **unscoped on the admin engine**; the job body
runs with `set_tenant_scope` on the **serving engine**. A worker that claims with a tenant bound
sees an empty queue.

### 3.4b — The worker registry and an `UNLOGGED` history table (0.25d)

Phase 7's pipeline-health page has a question it currently cannot answer from outside a process:
**is a worker alive?**

- **`workers` registry** — a row per worker with `last_heartbeat`. The health page reads it; the
  reaper uses it to distinguish "lease expired because the job is slow" from "lease expired
  because the process is gone".
- **`job_history`, `UNLOGGED`** — the completed-job archive. `UNLOGGED` because it is a
  diagnostic: losing it on an unclean shutdown costs nothing, and it keeps the hot `jobs` table
  small without a second durable write on every completion.

### 3.5 — The scheduler (0.5d)

A `job_schedules` table plus a materialiser step inside the worker loop, claimed with the same
primitive and **idempotency-keyed on `sched:{id}:{fire_time}`** — so two workers materialising
the same tick produce one job.

**Not APScheduler** — 4.0 is still pre-release as of Aug 2026 and its own docs say not to use it
in production; 3.x jobstores are not multi-scheduler safe. **Not `pg_cron`** — needs
`shared_preload_libraries` and an `nmake` build on Windows, and it schedules SQL, not Python.
**Not Windows Task Scheduler** — a second place where work is defined, and it swallows errors.

**DB clock only, never the worker's.** A worker with a skewed clock must not fire early.

**Debounce is not idempotency, and the re-indexing requirement needs the former.** Idempotency
says "this exact work is already queued, return it". Debounce says "work of this kind is already
queued for this tenant; collapse this request into it and push the run time out". Ten documents
uploaded in a minute should produce **one** re-index, not ten — and an idempotency key cannot
express that, because each upload is legitimately different work.

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

- [ ] `jobs` claim is a single statement, registered in `_TENANT_SCOPED_TABLES`, with an RLS
      policy verified by the live isolation test.
- [ ] A killed worker's job is reclaimed by the reaper and retried — **tested by actually
      killing a worker mid-job**, not by asserting the code path exists.
- [ ] A job at `max_attempts` lands in `dead` with its last error preserved.
- [ ] Re-enqueueing the same idempotency key returns the existing job and does not duplicate
      work.
- [ ] `memory_consolidation_job` runs on the substrate; the old claim path is deleted.
- [ ] A job that fails at stage 4 of 6 resumes at stage 5 — **verified by killing the process at
      stage 4**, not by asserting the code path.
- [ ] A worker whose lease expired **cannot** write its result: the stale write affects zero rows
      and the worker logs and exits.
- [ ] A job type with `concurrency=1` never runs twice at once, even with a worker configured for
      8 slots.
- [ ] Ten re-index requests inside the debounce window produce **one** job.
- [ ] The health endpoint reports a worker as gone within one heartbeat interval of killing it.
- [ ] Two workers in two processes never run the same job — concurrency test, N workers, M jobs,
      every job runs exactly once.
- [ ] A schedule fires once per tick with two workers running.
- [ ] `run_events` is partitioned by month; the `runs` header rebuilds from events in a test.
- [ ] `resolve()` returns `(value, source)`; a tenant cannot set a value weaker than the platform
      default for a `tighten_only` key — tested.
- [ ] The bijection test covers the settings catalogue.
- [ ] `python -m app.seed` produces two tenants; `_DEMO_USERS` is gone.
- [ ] `fine_role` reaches the browser.
- [ ] The route-coverage test passes.
- [ ] `aegis.core.require` is what the README says; the adapter says eight everywhere.
- [ ] Full suites green, ruff clean, `next build` green.

## Demo at the end of this phase

Log in as tenant A's admin and as tenant B's admin. Same screens, different data, and the
`(value, source)` badge showing which settings are platform defaults and which the tenant
changed. Then kill a worker mid-ingest and watch the job get reclaimed and finish.

That is the first time the platform demonstrably has tenants at all.

## Risks

**The substrate is easy to build weakly.** A claim without a lease looks correct in every test
that does not kill a process. The definition of done requires actually killing one.

**`run_events` partitioning is irreversible.** Get it right at creation; there is no migration
tool.

**Deleting `_DEMO_USERS` breaks every existing demo path** until the seed runs. Land both in the
same change and update the runbook in the same commit.

**The settings catalogue can sprawl.** Every key is a UI control and a test. Start with the keys
phases 6 and 7 actually need, not every knob that could exist.
