# Phase 3 — The platform spine

> **Kept as a record, 2026-08-23.** This phase shipped. It survives the documentation
> clean-up because `aegis/jobs/stages.py` and `backend/src/app/config.py`
> cite it for the Temporal sandbox boundary and the job substrate's measurements. The rest of the v2 plan — the
> master plan, the roadmap, the other phases, six research plans and five technology
> surveys — was deleted and is in git history (last full set: `2d8b84d`). **Links from
> here into `plans/` and `research/` are therefore dead**; the bodies are intact, only
> the cross-references are broken. See [`README.md`](README.md).



**Everything in phases 4–9 depends on at least one piece of this. Build it first.**

Six pieces, one theme: Aegis has no substrate for durable work, no per-tenant configuration,
and — measured — no tenants.

Research behind it: `plans/04-enterprise-substrate.md` ·
`plans/06-dashboards-control.md` ·
`plans/05-modularity-scale.md`

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
`research/job-framework-survey.md`.

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

## Where the code goes

The Module Contract decides this: `aegis` is the importable core, `backend` composes it.

| Path | Holds | Why here |
|---|---|---|
| `aegis/src/aegis/jobs/__init__.py` | Public exports | The module's surface |
| `aegis/src/aegis/jobs/models.py` | `JobRun`, `Document` ORM | Registers on `aegis.data.AegisBase`, like every other module's models |
| `aegis/src/aegis/jobs/scope.py` | `@tenant_activity` decorator | The isolation guarantee belongs with the core, not the host |
| `aegis/src/aegis/jobs/stages.py` | `StageSpec`, the stage contract | Portable subset — a driver swap later touches only the runner |
| `aegis/src/aegis/jobs/admission.py` | Admission cap + budget pre-auth | Tenant policy is a core concern |
| `backend/src/app/jobs/client.py` | Temporal client singleton | Needs `app.config` |
| `backend/src/app/jobs/worker.py` | Worker bootstrap, both launch modes | Host wiring |
| `backend/src/app/jobs/reconcile.py` | The reconciler sweep | Needs both the client and the session factory |

**`aegis.jobs` must not import `temporalio`.** The core declares the contract — stage specs, the
scope decorator, the models. The host runs it. That keeps `aegis` importable by a consumer who
orchestrates differently, and it is what makes the fallback substrate a drop-in if §3.0 fails.

---

## Tasks

| # | Task | Days |
|---|---|---|
| 3.0 | Temporal spike on the real Windows box | 0.25 |
| 3.1 | Record tables — `documents`, `job_runs` | 0.75 |
| 3.2 | Temporal wiring — client, worker, scope decorator, stage contract | 1.0 |
| 3.3 | Idempotent activities and the reconciler | 0.5 |
| 3.4 | Admission control, budget pre-authorisation, cancellation | 0.5 |
| 3.5 | Temporal Schedules — re-index cadence with debounce | 0.25 |
| 3.6 | `run_events` and the `runs` header | 0.75 |
| 3.7 | The settings catalogue | 1.0 |
| 3.8 | The two-tenant seed | 0.5 |
| 3.9 | `fine_role` on the wire | 0.25 |
| 3.10 | The client console + route-coverage test | 0.25 |
| 3.11 | `py.typed` and the four documentation lies | 0.25 |

**Total: 6.0 days.**

---

### 3.0 — The spike — **DONE on macOS 2026-08-18; Windows leg outstanding**

Run rather than argued. Temporal CLI **1.8.2 (Server 1.31.2, UI 2.50.1)** — the identical
version already verified on the target Windows box — and `temporalio` **1.31.0** installed into
`backend/.venv` as the `jobs` extra: **+3 packages** (`temporalio`, `nexus-rpc`,
`types-protobuf`), and **both suites still pass** (685 backend / 1270 aegis).

**Resumability — measured, not assumed.** A workflow with our six real stage names, worker
hard-killed (`SIGKILL`) while `embed` was in flight, then a fresh worker process started:

```
parse    pid=8377        ### HARD-KILL
chunk    pid=8377        embed    pid=8610   ← replayed: it never completed
enrich   pid=8377        index    pid=8610
embed    pid=8377        graph    pid=8610

stages that re-ran after the kill: ['embed']   ← only the in-flight one
```

`parse`, `chunk` and `enrich` **did not re-run**. That is the resumability claim, proved.

**And it is the empirical case for task 3.3.** The in-flight activity *did* replay — so an
activity that is not idempotent will double-write on every crash. This is not a theoretical
requirement.

**The sandbox trap, characterised precisely.** The prior research reported "side effects at
import fail validation". That is too broad, and the correct boundary matters because it decides
the module layout:

| At import time | Result |
|---|---|
| `time.time()`, `os.environ.get(...)` | **accepted** |
| `asyncio.run(...)` | **REJECTED** — `RuntimeError: Failed validating workflow` |

So the rule is narrower and sharper than "keep it pure": **a workflow module must not run an
event loop at import.** Ordinary module-level constants are fine. `backend/src/app/jobs/flows/`
exists to keep workflow definitions away from the modules that do `asyncio.run()` at import.

**Dev server RSS: 135 MB** on macOS (123 MB previously measured on the same class of machine).

**Still outstanding — the Windows leg**, which cannot be run from here. Its runbook was
`docs/install/04-verify.md`, deleted on 2026-08-23 when the two install directories were
merged; the verification table it held now lives in
[`docs/install/02-bootstrap.md`](../install/02-bootstrap.md), and the three measurements
below are recorded here rather than there because they are this phase's evidence:

- Total RSS with Postgres 17 + Neo4j Desktop + Memurai + Temporal all running.
- The same kill test on Windows, confirming behaviour parity.
- `.\scripts\db-roles.ps1` after installing Postgres, or the app connects as superuser and **all
  13 RLS policies are inert** — the exact defect Phase 1 fixed.

Install order on the box: **Postgres → `db-roles.ps1` → Memurai → Neo4j → Temporal.**

### 3.1 — The record tables (0.75d)

**These are ours, and they are the system of record.** Temporal never becomes the place you look
to answer "what does this tenant have".

```python
# aegis/src/aegis/jobs/models.py

class JobStatus(StrEnum):
    """Lifecycle of a durable job, from the record layer's point of view.

    Deliberately *not* a mirror of Temporal's workflow status: this is what a tenant
    sees and what the console renders. RECONCILING is the state a row enters when the
    reconciler finds a workflow it cannot account for — visible, rather than a row
    silently stuck in RUNNING forever.
    """
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECONCILING = "reconciling"


class JobRun(AegisBase):
    """One durable unit of background work, owned by a tenant.

    The ``workflow_id`` is a plain string, not a foreign key: Temporal is a system we
    do not own and must not constrain our schema. It is the only link, and it is
    deliberately one-way — this row is readable, joinable and auditable without
    Temporal being reachable at all.
    """
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(255), default=None)
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus), index=True)
    completed_stage: Mapped[str | None] = mapped_column(String(64), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    cancelled_by: Mapped[str | None] = mapped_column(String(255), default=None)


class Document(AegisBase):
    """A tenant's source document and where its ingestion got to.

    ``content_sha256`` is the idempotency anchor for the whole pipeline: re-uploading
    identical bytes must not re-parse them, and the unique constraint per tenant is
    what makes that structural rather than a check somebody remembers to write.
    """
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int]
    status: Mapped[JobStatus] = mapped_column(SAEnum(JobStatus), index=True)
    completed_stage: Mapped[str | None] = mapped_column(String(64), default=None)
    workflow_id: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    page_count: Mapped[int | None] = mapped_column(default=None)
    chunk_count: Mapped[int | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "content_sha256", name="uq_documents_tenant_sha"),
    )
```

**Register both in `_TENANT_SCOPED_TABLES`** (`aegis/src/aegis/governance/rls.py`). The
boot-time catalog read-back will otherwise report them, which is the diagnostic working
correctly — do not silence it, add the entries.

**Tests required:**

- Both tables appear in `pg_policies` with `tenant_isolation` after `bootstrap_rls`.
- The Phase 1 live isolation test (`backend/tests/integration/test_tenant_isolation_live.py`)
  covers both, driven from the registry, so this is automatic once registered — **confirm it, do
  not assume it.**
- The unique constraint rejects a second identical upload for the same tenant, and **permits**
  the same bytes for a different tenant.

---

### 3.2 — Temporal wiring (1.0d)

#### The scope decorator — the single most important thing in this phase

```python
# aegis/src/aegis/jobs/scope.py

def tenant_activity(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Bind the tenant scope for an activity, and refuse to run without one.

    Phase 1 made tenant isolation provable, and the way that guarantee dies is an
    activity that opens a session without binding a scope: it runs unscoped on the
    serving engine and reads every tenant's rows. A convention cannot prevent that —
    somebody forgets — so this is structural.

    The tenant comes from the activity's own typed argument, never from a contextvar:
    ambient context does not survive a Temporal replay in a fresh worker process, and
    an activity that *silently* loses its scope on replay is the worst version of this
    bug.

    Raises:
        MissingTenantScopeError: If the activity's argument carries no ``tenant_id``
            field. Raised at call time, and covered by a test that a decorated
            activity without one cannot run.
    """
```

Every activity signature therefore takes a typed argument carrying `tenant_id`:

```python
@dataclass(frozen=True, slots=True)
class ActivityInput:
    tenant_id: int | None
    workflow_id: str
    ...
```

#### The stage contract

```python
# aegis/src/aegis/jobs/stages.py

@dataclass(frozen=True, slots=True)
class StageSpec:
    """One stage of a multi-stage job — the portable subset of durable execution.

    Stage names, their order, and "resume after the last committed stage" are exactly
    what a durable-execution engine provides. Declaring them here rather than encoding
    them in Temporal decorators means the console, the health page and the docs read
    one source, and a future orchestrator swap touches only the runner.
    """
    name: str
    timeout_seconds: int
    max_attempts: int
    task_queue: str          # concurrency policy lives on the queue
```

```python
INGEST_STAGES: tuple[StageSpec, ...] = (
    StageSpec("parse",  timeout_seconds=1800, max_attempts=2, task_queue="aegis-cpu"),
    StageSpec("chunk",  timeout_seconds=300,  max_attempts=3, task_queue="aegis-default"),
    StageSpec("enrich", timeout_seconds=300,  max_attempts=3, task_queue="aegis-default"),
    StageSpec("embed",  timeout_seconds=900,  max_attempts=5, task_queue="aegis-io"),
    StageSpec("index",  timeout_seconds=600,  max_attempts=3, task_queue="aegis-default"),
    StageSpec("graph",  timeout_seconds=1800, max_attempts=2, task_queue="aegis-cpu"),
)
```

**`parse` and `graph` sit on `aegis-cpu`, whose worker runs `max_concurrent_activities=1`.**
That is how CPU-bound Docling parses serialise while embed calls on `aegis-io` run wide — the
"two separate concurrency numbers" requirement, expressed natively rather than hand-built.

#### The stage commit rule

> **Each activity writes its output *and* bumps `completed_stage` in ONE transaction.**

Temporal gives resumability *across* stages. That single transaction is what makes each stage
individually correct — a stage that "finished" but whose output rolled back is precisely the bug
this design exists to prevent.

#### Worker bootstrap

```python
# backend/src/app/jobs/worker.py — one implementation, two launch modes
```

In-process as an asyncio task in the lifespan (what runs on demo day), and
`python -m app.jobs.worker` standalone. **Identical code path.** Config:
`TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUES`.

**Workflow definitions live in their own import-safe module** — `backend/src/app/jobs/flows/`.
Nothing in that package may do side-effectful work at import; the sandbox re-imports it.

**Tests required:**

- A decorated activity called without a tenant argument **raises**, and the test asserts the
  exception rather than the absence of one.
- An activity called with `tenant_id=7` sees only tenant 7's rows — against live Postgres, as a
  non-superuser, extending the Phase 1 fixture.
- `aegis.jobs` does **not** import `temporalio` — an import-isolation test in the style of
  `aegis/tests/core/test_core_is_dep_free.py`.
- Two activities on `aegis-cpu` never run concurrently.

---

### 3.3 — Idempotent activities and the reconciler (0.5d)

**The one genuinely new problem this architecture introduces.** An activity can commit to
Postgres and then die before Temporal records its completion — so on replay, it runs again.

**Every activity is idempotent, keyed on `(workflow_id, stage)`:**

- Writing chunks for a document that already has them is a **delete-then-insert within the
  transaction**, or an upsert. Never a bare insert.
- The `completed_stage` bump is `UPDATE … WHERE completed_stage IS DISTINCT FROM :stage`, so a
  replay is a no-op rather than a double count.

**The reconciler** (`backend/src/app/jobs/reconcile.py`) covers the opposite skew — a row whose
workflow no longer exists, because the server was wiped or the workflow terminated externally.
It runs on a Temporal Schedule, and for each `RUNNING` row older than a threshold it asks
Temporal for the workflow's status and either lets it be, marks the row `FAILED` with the reason,
or restarts it.

Without it, a stuck row is invisible — **the same silence the reaper existed to break in the
hand-rolled design.**

**Tests required:**

- Invoking each activity **twice** for the same `(workflow_id, stage)` produces one result — a
  parametrised test over the stage tuple, so a new stage cannot skip it.
- A `RUNNING` row whose workflow id does not exist in Temporal is reconciled to `FAILED` with a
  reason, tested with a fake client returning `NotFound`.

---

### 3.4 — Admission control, budget pre-auth, cancellation (0.5d)

**Still ours, because these are tenant policy, not execution mechanics.**

```python
# aegis/src/aegis/jobs/admission.py

async def admit(session, *, tenant_id, job_type, estimated_cost_usd) -> None:
    """Decide whether a tenant may start another job, and say no out loud.

    Two independent gates. The concurrency cap stops one tenant occupying every
    worker slot; the budget pre-check stops a job starting that the tenant cannot
    afford to finish. Both raise rather than queueing silently: invisible
    backpressure is the same defect as a silent fallback, and a 429 a user can see
    beats a job that never runs for reasons nobody can name.

    Raises:
        AdmissionDeniedError: Tenant is at its in-flight cap for this job type.
        BudgetExceededError: Estimated cost exceeds the tenant's remaining budget.
    """
```

Both caps come from the **settings catalogue** (§3.7) — `jobs.max_inflight.{job_type}` — so a
platform admin changes them from a dashboard rather than a deploy.

**Cancellation** is a Temporal cancellation signal. Our row records `cancelled_by` and the
timestamp; Temporal stops the work. The route is `POST /jobs/{id}/cancel`, guarded so a tenant
can only cancel its own.

**Tests required:**

- The `(n+1)`th concurrent job for a tenant raises `AdmissionDeniedError`, and the route returns
  **429** with a reason in the body.
- A job whose estimate exceeds the remaining budget never starts a workflow — asserted by a fake
  Temporal client recording zero `start_workflow` calls.
- A tenant cannot cancel another tenant's job (403).

---

### 3.5 — Temporal Schedules, with debounce (0.25d)

Re-indexing on a cadence is a **Temporal Schedule**, not a table we maintain.

**Debounce is ours, and it is not idempotency.** Idempotency says *"this exact work is already
queued, return it"*. Debounce says *"work of this kind is already pending for this tenant; fold
this request in and push the run time out"*. Ten documents uploaded in a minute must produce
**one** re-index — and an idempotency key cannot express that, because each upload is
legitimately different work.

Implemented with a per-tenant workflow id (`reindex:{tenant_id}`) plus a timer the workflow
resets when a new signal arrives, so the second request joins the first rather than queueing
behind it.

**Test required:** ten signals inside the window produce one execution.

### 3.6 — `run_events` and the `runs` header (0.75d)

The durable, tenant-scoped, replayable record. **Do not add a fourth tracking mechanism** —
OTel spans, Phoenix and the SSE stream already exist; this is the one that survives a restart.

```sql
CREATE TABLE run_events (
    id           bigserial,
    run_id       text        NOT NULL,
    tenant_id    integer     REFERENCES tenants(id),
    seq          integer     NOT NULL,
    ts           timestamptz NOT NULL DEFAULT now(),
    event_type   text        NOT NULL,
    agent_id     text,                       -- Phase 5 writes this
    job_id       bigint      REFERENCES job_runs(id),
    trace_id     text,                       -- reconciles with Phoenix
    span_id      text,
    payload      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);
```

**Partitioned by month, at creation.** This is the **one irreversible decision** in the roadmap:
converting a large heap table later needs a migration, and `backend/pyproject.toml` deliberately
has no Alembic. Create the current and next month's partitions at bootstrap, and a scheduled job
(§3.5) that rolls the next one forward.

**`runs` is a header row, and it is a second table against the one-mechanism rule**, so it earns
its place explicitly: it is a **regenerable projection** — `run_id`, `tenant_id`, `user_id`,
status, timings, token and cost totals — and it ships with a test that **rebuilds it from
events**. If the two ever disagree, events win.

**Retention:** a scheduled prune dropping whole partitions, not `DELETE`. Dropping a partition is
instant; deleting 10M rows is not.

**Tests required:**

- Writing an event outside the current partition's range fails loudly rather than silently
  landing nowhere — the classic partitioning trap.
- The `runs` header rebuilt from events equals the incrementally-maintained header, on a run with
  every event type.
- A tenant reads only its own events — through the Phase 1 live isolation fixture.

### 3.7 — The settings catalogue (1.0d)

**The mechanism behind "0 code change from the dashboard".** Every per-tenant control in phases
6, 7 and 10 is an entry here rather than a bespoke screen.

```python
# aegis/src/aegis/settings/spec.py

class MergeRule(StrEnum):
    """How a tenant or user value combines with the platform default.

    ``TIGHTEN_ONLY`` is the load-bearing one: it makes the tenant-safety rules
    *executable configuration* rather than prose, because the resolver structurally
    cannot compute a value weaker than the platform default. That is what turns "a
    tenant may add a guardrail but never weaken one" from a policy somebody has to
    remember into arithmetic.
    """
    OVERRIDE = "override"          # last scope wins  (e.g. preferred model)
    TIGHTEN_ONLY = "tighten_only"  # may only become stricter (e.g. gate_min_risk)
    UNION = "union"                # sets accumulate  (e.g. extra guardrails)


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    type_: type
    default: Any
    writable_by: frozenset[str]    # fine roles
    readable_by: frozenset[str]
    merge: MergeRule
    bounds: tuple[Any, Any] | None = None
    description: str = ""          # rendered as the control's help text
```

```sql
CREATE TABLE settings (
    id         bigserial PRIMARY KEY,
    scope      text    NOT NULL CHECK (scope IN ('platform','tenant','user')),
    tenant_id  integer REFERENCES tenants(id),
    user_id    integer REFERENCES users(id),
    key        text    NOT NULL,
    value      jsonb   NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text,
    UNIQUE (scope, tenant_id, user_id, key)
);
```

```python
async def resolve(session, key: str, *, tenant_id, user_id) -> tuple[Any, str]:
    """Return the effective value and the scope it came from.

    The ``source`` half is not decoration: a control that shows a value without saying
    whether it is the platform default, the tenant's choice or the user's own is a
    control nobody can reason about. Phase 6's composer renders it as a badge.
    """
```

**Generalise `_KNOB_SPECS` / `harness_config()`, do not invent a second mechanism** — and inherit
its **bijection test**, which makes a setting impossible to add without a UI control appearing.

**Tests required:**

- A `TIGHTEN_ONLY` key: a tenant setting a *weaker* value than the platform default resolves to
  the platform default, and the write is **rejected with a reason** rather than silently ignored.
- `UNION`: a tenant's extra guardrails append to the platform's; the platform's cannot be removed.
- `resolve()` returns the correct `source` at each of the three scopes.
- The bijection test covers every `SettingSpec`.
- A user cannot write a key whose `writable_by` excludes their fine role (403).

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

---

## How this is built — the standard, not a suggestion

Every task above is implemented to this bar. An agent that cannot meet it should stop and report
rather than lower it.

**Verify, do not assert.** Every claim about current behaviour is grounded in real source that
was opened and read. This repo's standing failure mode is documentation that says the opposite
of the code — RLS inert under a superuser, a budget test green while asserting the reverse of
reality, a console whose every live query returned 400. All three were found by reading, not by
trusting.

**No test is weakened to make something pass.** If a test breaks, either it encoded the old
contract — fix it deliberately and say so in the PR — or the change is wrong. Deleting an
assertion to get green is the one unrecoverable mistake here.

**No bare `except`.** No swallowing. A control that cannot run fails **closed** and says so. A
diagnostic wrapped in a broad `except` is how this repo once shipped a warning that could never
fire for any input.

**Docstrings explain *why*, in Google style, matching the file being edited.** Read the
neighbouring code first — `aegis/src/aegis/governance/rls.py` is the standard for this codebase
and it is unusually well documented.

**Tests prove behaviour, not shape.** The definition of done requires killing a process, running
an activity twice, and exceeding a real budget. `assert func is not None` proves nothing.

**Every new table is registered in `_TENANT_SCOPED_TABLES`** and covered by the live isolation
test. The boot-time catalog read-back reporting your table is the diagnostic working — add the
entry, never silence it.

### Verification, run before any task is called done

```bash
cd /Users/yrevash/aegis/backend && PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest -q
cd /Users/yrevash/aegis/aegis   && PYTHONPATH=src ../backend/.venv/bin/python -m pytest -q
cd /Users/yrevash/aegis/backend && .venv/bin/python -m ruff check ../aegis ../backend
cd /Users/yrevash/aegis/web     && npx tsc --noEmit && npx next lint --dir src && npx next build
```

Baselines to beat, not regress: **685 backend / 1270 aegis passing, ruff clean repo-wide, build
37/37 pages.** New tests add to those numbers.

Postgres is at `localhost:5432`; the app database is `taif`; the serving role is the
non-superuser `aegis_app` and the owner DSN is separate. **Never run destructive DDL against
`taif`** — create a scratch database, verify, drop it.

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
