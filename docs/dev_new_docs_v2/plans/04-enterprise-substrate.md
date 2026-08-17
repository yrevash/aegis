# Plan 04 — the enterprise substrate

**Research output for requirements A1–A6 of [`01-V2-ADDITIONS.md`](../01-V2-ADDITIONS.md).**
Jobs and workers (A4) · end-to-end request tracking (A3) · pipeline health and audit (A2) ·
the admin database-query page (A1) · pipeline structure and module docs (A5) · what
"enterprise scale" honestly means here (A6).

This is research, not a phase file. It ends with a dependency-ordered sequence that the
phase files absorb.

---

## How to read the claims in this document

Three kinds of statement appear here, and they are marked so you can check them:

| Marker | Means |
|---|---|
| **[SOURCE]** | Read in this repository. File and line are given. If it is not marked, I did not read it. |
| **[MEASURED]** | Run against a live PostgreSQL 14.18 and asyncpg 0.31 in a throwaway venv and a scratch database. The exact probe is in Appendix A and you can re-run it. |
| **[EVIDENCE]** | External. Cited. Where the evidence is thin I say so instead of rounding up. |

Nothing here is asserted from memory about how Aegis behaves. Every behavioural claim was
opened and read, because the standing failure mode in this repo is documentation that says the
opposite of the code.

---

## The recommendations, up front

| # | Question | Recommendation |
|---|---|---|
| A4 | Job substrate | **One `jobs` table on Postgres**, claimed with a single-statement `UPDATE … FROM (SELECT … FOR UPDATE SKIP LOCKED) … RETURNING`, with a **lease + reaper**, retry with jittered backoff, a dead-letter status, idempotency keys, priority and cooperative cancellation. Not Celery, not RQ, not arq, not procrastinate — and §1.1 says exactly what would change my mind. |
| A4 | Worker process | **One worker implementation, two launch modes.** In-process asyncio task inside the API (what runs today, and what runs on demo day) and `python -m aegis.jobs.worker` as a standalone process. Identical code path; the DB is the only coordination. On Windows, **NSSM** for the always-on case. |
| A4 | Scheduler | **A `job_schedules` table plus a materialiser step inside the worker loop.** Not APScheduler (4.0 is still pre-release in Aug 2026 and says so in its own docs), not `pg_cron` (needs `shared_preload_libraries` and an `nmake` build on Windows), not Task Scheduler (a second place where work is defined, and it swallows errors). |
| A3 | Durable request record | **`run_events` exactly as [`plans/02`](02-agentic-core-console.md) §2.2 specifies**, plus three additions: `trace_id`/`span_id` columns, a `job_id` column, and a `runs` header row. **No fourth mechanism.** Phoenix stays the ephemeral deep-dive; `run_events` is the durable, tenant-scoped, replayable record. |
| A2 | Health page | **An aggregation, not a subsystem.** Aegis already has honest per-component truth in five places; the page joins them and adds three missing probes. `unknown` is a distinct state from `down`. |
| A1 | Admin DB page | **Build the hardened read-only execution path first. Put a schema browser and saved parameterised queries on it. Ship free-form SQL on the *same* path behind a platform-admin toggle.** If anything gets cut it is the free-form box, never the path. Full security reasoning and five measured findings in §4. |
| A5 | Pipeline structure | **A declared `PipelineSpec` per pipeline, with the runtime, the API, the console and the docs all reading the one declaration** — the pattern `/agent/topology` already proves. Three pipelines, not sixteen. Do not rewrite `docs/teaching/`. |
| A6 | Enterprise scale | Three honest columns in §6: needed now · must not be foreclosed · premature. The premature list is named, so nobody starts one. |

---

# Part 0 — What is actually there today

Everything in this section was read. It is short because the point is the gaps, not a tour.

### 0.1 The one job pattern

`aegis/src/aegis/memory/consolidate.py` **[SOURCE]**. `enqueue_consolidation` (line 784)
commits a `PENDING` row inside the request. `sweep_pending` (line 972) selects up to `limit`
pending rows, then claims each with a guarded update (lines 997–1010):

```python
claim = (update(MemoryConsolidationJob)
    .where(MemoryConsolidationJob.id == job.id,
           MemoryConsolidationJob.status == ConsolidationStatus.PENDING)
    .values(status=ConsolidationStatus.RUNNING,
            attempts=MemoryConsolidationJob.attempts + 1))
res = await session.execute(claim)
if (res.rowcount or 0) == 0:   # lost the race to another sweeper
    continue
```

The guard is correct. The *substrate* around it is not, and the gaps are exactly the ones A4
names. Against `MemoryConsolidationJob` (`aegis/src/aegis/memory/stores.py:201–225`
**[SOURCE]**) — columns are `id, tenant_id, subject_id, session_id, status, attempts, error,
created_at, updated_at`:

| Missing | Consequence today |
|---|---|
| No lease / visibility timeout | A worker killed mid-job leaves the row `RUNNING` **forever**. Nothing reclaims it. `attempts` is incremented but never consulted. |
| No `run_after` | Cannot schedule, cannot back off, cannot delay. |
| No `max_attempts`, no dead-letter | `ERROR` is terminal on the first failure. A transient network blip destroys the work permanently; a poison payload would loop forever if retry were ever added. |
| No idempotency key | Two `enqueue_consolidation` calls for the same `(subject, session)` create two jobs that both run. |
| No priority | Everything is FIFO by `created_at`. |
| No cancellation | Nothing can stop a job. |
| No payload column | The job's inputs are two bespoke columns, so this queue can only ever carry this one job type. |
| `SELECT` without `FOR UPDATE SKIP LOCKED` | Every concurrent sweeper reads the same N rows and then loses N−1 races. `_supports_skip_locked` exists at `backend/src/app/data/approvals.py:62` and is used by `sweep_expired` (line 352) — but the memory sweeper does not use it. |

**Conclusion: `consolidate.py` is a correct claim, not a job substrate.** It is the right
precedent for *guarded transitions* and the wrong precedent for *everything else*. Building
one more of these per feature is how you end up with five half-queues.

### 0.2 How background work starts

`backend/src/app/main.py` **[SOURCE]**. Two long-lived asyncio tasks are created in the
lifespan (lines 220–235), gated on `settings.stores_enabled`:

- `run_sla_sweeper` (`backend/src/app/data/approvals.py:372`) — 30 s default period.
- `_run_memory_sweeper` (`main.py:93`) — 60 s default period, batch 10.

`_supervise` (line 66) attaches a done-callback that logs at ERROR if either stops. That is
good and rare; keep it. What does not exist: any way to *see* from outside the process that
they are alive, any record of when they last did work, and any coordination if there is ever
more than one process. `scripts/start.ps1:34` runs `uvicorn app.main:app --port 8000` with
**no `--workers`** **[SOURCE]** — so single-process is the only reason two sweepers per class
do not exist today. That is an accident of the launcher, not a design.

### 0.3 Health

`aegis/src/aegis/core/health.py` **[SOURCE]** has three real probes — `probe_redis` (51),
`probe_postgres` (78), `probe_vector_store` (103). Each returns
`DependencyStatus(name, status: 'up'|'down', detail)` and never raises. They are honest.

They are called from exactly one place: `AegisSettings.resolve_mode` (`core/config.py:120`),
i.e. **once at boot, and only when `AEGIS_MODE=auto`.** There is:

- **no `/readyz` endpoint** — the docstrings at `health.py:3`, `:73` and `:133` all reference
  one; `grep -rn readyz backend/src aegis/src web/src` returns only those three comments
  **[SOURCE]**. A documented endpoint that does not exist is the exact class of defect the
  audits keep finding.
- **no Neo4j probe** and no gateway probe.
- `GET /health` (`routes.py:749`) returns a static `{"status":"ok", product, version}` and
  deliberately touches nothing. That is correct for a liveness probe and useless for A2.

`GET /stack` (`routes.py:830`, `app/platform/stack.py`) resolves *installed versions* from
`importlib.metadata` — genuinely honest, and orthogonal to reachability.

`aegis/src/aegis/observability/latency.py` **[SOURCE]** aggregates real measured node
timings — into a per-process `deque` that **resets on restart**, which its own docstring
states plainly. So "p95 latency" today is "p95 since the last restart, in this process."

### 0.4 Tracing

`aegis/src/aegis/observability/otel.py` **[SOURCE]** launches a local in-process Phoenix and
registers the tracer provider, falling back to a console exporter when Phoenix is absent.
`current_trace_id()` (line 124) already returns the active 32-hex trace id. The gateway emits
`gen_ai.*` spans through `OtelObservabilitySink`.

`run_events` is **proposed and not built** — `plans/02` §2.2 (line 226) and
`backlog-post-hackathon.md` line 198. `grep -rn run_events` finds only documentation
**[SOURCE]**. `run_summary()` is a projection over an in-memory list that dies with the socket.

### 0.5 Tenancy

`aegis/src/aegis/governance/rls.py` **[SOURCE]**: 13 registered tenant-scoped tables
(lines 103–121), `ENABLE` + `FORCE` + a `tenant_isolation` policy per table, a catalog
read-back before and after the DDL, and a serving-role bypass audit. The predicate
(lines 195–199) is **fail-open on an unset scope**, documented as a deliberate choice because
login and the platform-admin listings read before a tenant is known.

That fail-open branch is load-bearing for background work and for A1, and §1.11 and §4.6
below are the two places it becomes a design constraint rather than a footnote.

No migration tool: `backend/pyproject.toml:36` documents the deliberate absence of Alembic
**[SOURCE]**. Schema comes from `create_all` plus an additive column reconciler. Every schema
proposal below is therefore constrained to **new tables and nullable/defaulted columns**.

---

# Part 1 — A4, the job substrate

Do this first. A3, A2 and half of A5 are consumers of it.

## 1.1 The decision, and the honest case against it

**The prior in `01-V2-ADDITIONS.md` is right. I am confirming it, with three corrections and
one condition that would reverse it.**

### The measurement

The objection to a Postgres queue is always throughput. So I measured it rather than argue it
**[MEASURED]** — 20 000 jobs, a partial index on `(priority, run_after, id) WHERE
status='pending'`, PostgreSQL 14.18, asyncpg, on this laptop:

| Workers | Claim batch | Throughput | Duplicate claims |
|---|---|---|---|
| 4 | 1 job per statement | **20 877 claims/s** | 0 |
| 4 | 20 jobs per statement | **128 085 claims/s** | 0 |
| 8 | 50 jobs per statement | **113 708 claims/s** | 0 |

Aegis's jobs are LLM-bound and take seconds each. The realistic peak is *tens* of jobs per
minute during a live ingest. The claim mechanism is four orders of magnitude clear of the
requirement, and the ceiling everyone worries about is not in this system's future.

For calibration outside this box: 37signals runs **20 million jobs a day** — about 230/s — on
Postgres via Solid Queue's `FOR UPDATE SKIP LOCKED`, and Rails 8 ships it as the *default*
backend, displacing Redis **[EVIDENCE]**. `SKIP LOCKED` has been in Postgres since 9.5 and is
the shared mechanism behind Solid Queue, pg-boss, River, Oban, GoodJob and procrastinate. This
is not a clever trick; it is the mainstream 2025–26 answer.

### The three corrections to the prior

**Correction 1 — do not generalise `consolidate.py`'s claim shape.** It is a `SELECT`, then a
per-row guarded `UPDATE`: N+1 round trips, and N−1 losers per contended batch. Use one
statement (§1.3). Same guarantee, one round trip, and it works over the extended protocol —
which matters more than it looks (§4.2).

**Correction 2 — `SKIP LOCKED` is the claim, not the substrate.** It stops two live workers
taking the same row. It does nothing about a worker that *dies* holding one: the transaction
ends, the lock is released, and the row is left `RUNNING` with no one working it. That is the
bug in `consolidate.py` today. The substrate is **claim + lease + reaper**, and the reaper is
the half people skip. **[EVIDENCE]** — this is the same lease-not-lock distinction SQS makes
with visibility timeout; the guidance is to set the lease above p99 job duration, heartbeat it
for variable work, and bound total renewals so a deadlocked job cannot stay invisible forever.

**Correction 3 — "no new infrastructure" is a good reason but not the deciding one.** The
deciding reason is **one tenant-scoped, RLS-governed table that the health page, the console
and the audit trail can all query**. If the tenancy requirement did not exist, I would
recommend adopting a library instead of writing one, and §1.1's next section is why.

### The honest evaluation of the alternatives

| Option | Verdict |
|---|---|
| **Celery** | No. Needs a broker daemon (Redis/RabbitMQ). Memurai could serve as the broker, but Celery's asyncio story is still second-class and it puts job state *outside* the database that holds the data the job mutates — so a job and its result cannot commit together. Two failure domains for zero gain at this scale. |
| **RQ** | No. Redis-only, fork-per-job on POSIX; the Windows story is poor. |
| **arq** | No. Redis-only. Genuinely good asyncio design, but same split-brain: job state in Memurai, data in Postgres. |
| **pgmq** | No. Postgres-native and well designed, but it is a **C extension** — `CREATE EXTENSION` plus a build on native Windows. That is the container-shaped dependency the environment constraints exclude, for a feature we can express in one table. |
| **procrastinate** | **The serious contender, and the one I nearly recommended.** Postgres-backed, asyncio-native, `LISTEN/NOTIFY` + `FOR UPDATE SKIP LOCKED`, retries, periodic tasks, job locks, worker heartbeats, abort requests. Actively maintained (3.9.0, mid-2026) **[EVIDENCE]**. It is a better queue than we will write. |

**Why procrastinate is still not the answer here.** I installed 3.9.0 in a throwaway venv and
read its shipped schema **[MEASURED]**:

- 4 tables (`procrastinate_jobs`, `procrastinate_workers`, `procrastinate_events`,
  `procrastinate_periodic_defers`), **18 stored functions**, 7 triggers, 3 enum types, and a
  tree of **40 migration files**.
- `grep -ci tenant` over `schema.sql`: **0**. `procrastinate_jobs` is
  `(id, queue_name, task_name, priority, lock, queueing_lock, args jsonb, status,
  scheduled_at, attempts, abort_requested, worker_id)`.

Three consequences, in descending order of seriousness:

1. **The tenant story would be "it's inside `args`."** No `tenant_id` column means no RLS
   policy is *possible* — and because there is no `tenant_id` column, `rls.py`'s catalog
   read-back would not even *report* them as a gap (`_plan_rls` classifies a table with no
   tenant column as "not tenant-scoped", `rls.py:327`). Four tables of tenant work would be
   silently ungoverned, and the one diagnostic built to catch that would read healthy. That is
   precisely the failure mode this project keeps finding and fixing.
2. **40 migrations into a repo with no migration tool.** `backend/pyproject.toml:36`
   deliberately has no Alembic. Adopting procrastinate means adopting its migration CLI, or
   hand-applying its schema — either way a second, differently-managed schema lifecycle inside
   the same database.
3. **A2 and A3 fragment.** The pipeline-health page would read one shape for jobs and another
   for runs; per-tenant job visibility would be bespoke filtering rather than a policy. The
   whole "one mechanism used well" argument evaporates.

**What we take from it anyway:** its design. Worker registration with a heartbeat, a stalled-
worker pruner, an abort-request flag, and `LISTEN/NOTIFY` wakeup are all in the schema above,
and all four are in the design below. Adopt the design, not the schema.

**The condition that reverses this decision:** if job volume ever needs a broker's semantics —
sustained thousands/second, fan-out to non-Python workers, or cross-service queues — revisit.
Record the trigger, do not pre-build for it (§6).

## 1.2 The schema

Two new tables. Both tenant-scoped, both registered in `rls.py`. Everything below is a `CREATE
TABLE` or a nullable/defaulted column, so `create_all` + the additive reconciler covers it and
no migration tool is needed.

```sql
CREATE TABLE jobs (
  id             bigserial   PRIMARY KEY,
  tenant_id      integer     NULL,          -- RLS anchor. NULL = platform-owned work.
  kind           text        NOT NULL,      -- 'memory.consolidate', 'ingest.document', …
  payload        jsonb       NOT NULL DEFAULT '{}'::jsonb,
  status         text        NOT NULL DEFAULT 'pending',
                 -- pending | running | succeeded | failed | dead | cancelled
  priority       smallint    NOT NULL DEFAULT 100,   -- lower runs first
  run_after      timestamptz NOT NULL DEFAULT now(), -- schedule + backoff, one column
  attempts       integer     NOT NULL DEFAULT 0,
  max_attempts   integer     NOT NULL DEFAULT 5,
  lease_expires_at timestamptz NULL,        -- the visibility timeout
  locked_by      text        NULL,          -- worker identity, for the health page
  cancel_requested boolean   NOT NULL DEFAULT false,
  idempotency_key text       NULL,          -- see the partial unique index below
  -- provenance, so a job is never an orphan
  run_id         uuid        NULL,          -- the run that enqueued it (A3 join key)
  trace_id       text        NULL,          -- OTel trace of the enqueueing request
  parent_job_id  bigint      NULL,
  user_id        integer     NULL,
  -- outcome
  last_error     text        NULL,
  result         jsonb       NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  started_at     timestamptz NULL,
  finished_at    timestamptz NULL,
  CONSTRAINT jobs_attempts_bounded CHECK (attempts <= max_attempts + 1)
);

-- The claim index. Partial, so it only covers claimable rows and stays small
-- however large the completed-job history grows.
CREATE INDEX jobs_claim_idx ON jobs (priority, run_after, id)
  WHERE status = 'pending';

-- The reaper index.
CREATE INDEX jobs_lease_idx ON jobs (lease_expires_at)
  WHERE status = 'running';

-- Idempotency: at most one LIVE job per key. Completed jobs do not block a re-enqueue,
-- which is what makes the key mean "don't double-run", not "never run again".
CREATE UNIQUE INDEX jobs_idempotency_live_idx
  ON jobs (tenant_id, kind, idempotency_key)
  WHERE idempotency_key IS NOT NULL
    AND status IN ('pending', 'running');

-- The console/health read path.
CREATE INDEX jobs_tenant_created_idx ON jobs (tenant_id, created_at DESC);
CREATE INDEX jobs_run_idx ON jobs (run_id) WHERE run_id IS NOT NULL;
```

```sql
CREATE TABLE job_schedules (
  id             bigserial   PRIMARY KEY,
  tenant_id      integer     NULL,
  name           text        NOT NULL,        -- unique per tenant
  kind           text        NOT NULL,        -- the job kind to enqueue
  payload        jsonb       NOT NULL DEFAULT '{}'::jsonb,
  interval_seconds integer   NULL,            -- simple period; NULL when cron_expr is set
  cron_expr      text        NULL,            -- optional, evaluated in Python
  enabled        boolean     NOT NULL DEFAULT true,
  next_run_at    timestamptz NOT NULL DEFAULT now(),
  last_run_at    timestamptz NULL,
  last_job_id    bigint      NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX job_schedules_name_idx ON job_schedules (tenant_id, name);
CREATE INDEX job_schedules_due_idx ON job_schedules (next_run_at) WHERE enabled;
```

**Decisions worth defending:**

- **`status` is `text`, not a native Postgres enum.** Every existing enum in this schema
  (`user_role`, `budget_scope`, `approval_status`, `memory_consolidation_status`) is a native
  type, and `models.py:107` already carries a comment explaining the manual `ALTER TYPE …
  ADD VALUE` dance needed to widen one. With no migration tool, a `text` column plus a `CHECK`
  is the honest choice for a status set that will grow. **This is a deliberate deviation from
  the surrounding convention and this sentence is the reason.**
- **No separate `dead_letter` table.** Dead-lettering is a *status*, not a location. A
  separate table means a second schema, a second read path, and a redrive that moves rows
  between tables. `status='dead'` keeps the whole history in one place with one query, and
  redrive is `UPDATE … SET status='pending', attempts=0`.
- **No separate `job_attempts` history table.** `attempts` + `last_error` is enough for the
  health page; per-attempt history is what `run_events` (§2) already gives us for anything
  that matters. Adding it now would be the third table for a feature nobody has asked to see.
- **`run_after` does double duty** as the schedule time and the backoff time. One column
  cannot disagree with itself.
- **`parent_job_id` but no DAG engine.** One nullable column buys the provenance chain
  (ingest → chunk → embed) for free. Dependency resolution is explicitly out of scope (§6).

**RLS registration.** Two lines in `_TENANT_SCOPED_TABLES` (`rls.py:103`) — `"jobs"` and
`"job_schedules"` — and the boot read-back covers them. Both have an `integer` `tenant_id`, so
`is_protectable` (`rls.py:264`) is satisfied and a policy will actually compile. **[SOURCE]**

## 1.3 Claim semantics

One statement. Atomic. Extended-protocol safe.

```sql
WITH claimed AS (
  SELECT id FROM jobs
   WHERE status = 'pending'
     AND run_after <= now()
     AND NOT cancel_requested
   ORDER BY priority, run_after, id
   FOR UPDATE SKIP LOCKED
   LIMIT $1
)
UPDATE jobs j
   SET status = 'running',
       attempts = j.attempts + 1,
       locked_by = $2,
       started_at = coalesce(j.started_at, now()),
       lease_expires_at = now() + ($3 || ' seconds')::interval
  FROM claimed c
 WHERE j.id = c.id
RETURNING j.*;
```

**Why this shape:**

- **`now()` is the *database's* clock, never the worker's.** Two worker processes have two
  clocks; a job substrate that trusts either of them has an intermittent bug that only appears
  when they drift. Every timestamp in this design — `run_after`, the lease, the reaper cutoff
  — is evaluated server-side.
- **One statement means no partial claim.** There is no window where a row is selected but not
  yet marked.
- **It uses bind parameters, so it goes over the extended query protocol.** Verified
  **[MEASURED]**: asyncpg's `execute()` with no arguments uses the *simple* protocol and will
  happily run several semicolon-separated statements; with arguments it uses the extended
  protocol and refuses (`PostgresSyntaxError: cannot insert multiple commands into a prepared
  statement`). Keeping every substrate statement parameterised keeps the whole job path on the
  protocol that structurally cannot execute a smuggled second statement. Same discipline as §4.
- **`ORDER BY priority, run_after, id`** matches `jobs_claim_idx` exactly, so the claim is an
  index scan, not a sort.
- **`FOR UPDATE SKIP LOCKED` inside the CTE, not on the UPDATE.** The lock has to be taken
  during selection or the ordering is not what gets locked.

Zero duplicate claims across all three concurrency configurations in the probe **[MEASURED]**.

## 1.4 Lease, heartbeat, reaper

The lease is the half `consolidate.py` is missing, and it is the difference between a queue
and a queue-shaped table.

- **Claim sets `lease_expires_at = now() + lease_seconds`.** Default 300 s; per-kind override,
  because a Docling ingest and a cheap-model consolidation have different p99s.
- **Long jobs heartbeat.** `UPDATE jobs SET lease_expires_at = now() + interval WHERE id = $1
  AND locked_by = $2 AND status = 'running'` on a timer inside the worker. The `locked_by`
  predicate means a worker that already lost its lease cannot silently take it back.
- **The reaper is a scheduled job on the substrate itself.** Every 30 s:

  ```sql
  UPDATE jobs
     SET status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
         locked_by = NULL, lease_expires_at = NULL,
         run_after = now() + backoff(attempts),
         last_error = coalesce(last_error, 'lease expired: worker died or stalled')
   WHERE status = 'running' AND lease_expires_at < now();
  ```

  Dogfooding is the point: the reaper being a `jobs` row means the health page shows the
  reaper's own last run, and there is no privileged out-of-band mechanism to reason about
  separately.
- **Bound the renewals.** A job that has heartbeated past `max_lease_total` is killed and
  dead-lettered rather than renewed forever — otherwise a deadlocked job stays invisible
  indefinitely, which is the documented failure mode of naive heartbeating **[EVIDENCE]**.

**Migration note:** the existing `memory_consolidation_job` rows stuck in `RUNNING` are
unreachable today. When consolidation moves onto this substrate (§1.12), the cutover job must
sweep them, not silently abandon them.

## 1.5 Retry, backoff, dead-letter

**Backoff:** `delay = min(base * 2^(attempts-1), cap) * (0.5 + random()*0.5)` — exponential
with full-width jitter, `base = 5 s`, `cap = 1 h`. Jitter is not decoration: without it, N
jobs that fail against the same downstream all retry in the same instant and re-create the
outage they are recovering from **[EVIDENCE]**.

**Classify failures before counting them.** A retry policy that treats every exception the
same is what turns one bad payload into a million log lines:

| Class | Examples in Aegis | Policy |
|---|---|---|
| **Transient** | gateway 429/5xx, connection reset, Neo4j unavailable, lease lost | Retry with backoff. |
| **Permanent** | malformed payload, unknown `kind`, guardrail refusal, a tenant that no longer exists | **Zero retries.** Straight to `dead`. Retrying a validation error is pure waste. |
| **Budget** | `BudgetExceededError` | Do not retry, do not dead-letter — `status='failed'` with the reason, so the console can show "this tenant is out of budget" rather than "a job crashed". A distinct outcome, honestly labelled. |
| **Cancelled** | `cancel_requested` observed | `status='cancelled'`. Not a failure. |

The handler raises a typed exception; the worker maps it. **A bare `except Exception` that
retries everything is the design mistake here** — `consolidate.py:1024` currently catches
`Exception` and marks `ERROR` terminally, which is the mirror-image mistake.

**Dead-letter** is `status='dead'` and it is a **first-class UI state**, not a log line: the
health page shows a dead count per kind, per tenant, and offers redrive. A DLQ nobody looks at
is a data-loss mechanism with extra steps.

## 1.6 Idempotency

`idempotency_key` plus the partial unique index in §1.2. Enqueue is
`INSERT … ON CONFLICT DO NOTHING RETURNING id`; a `None` result means an identical live job
already exists, and the caller gets that job's id rather than an error.

Key derivation is the enqueuer's job and must be **deterministic from the work, never from the
clock**:

| Kind | Key |
|---|---|
| `memory.consolidate` | `f"{tenant_id}:{subject_id}:{session_id}:{max_turn_index}"` |
| `ingest.document` | the document's content hash |
| `evals.run` | `f"{prompt_version_id}:{dataset_version}"` |

**At-least-once is the delivery guarantee and the handlers must be written for it.** The
lease/reaper design means a job whose worker dies *after* the side effect but *before* the
status update will run again. Exactly-once is not achievable without distributed transactions;
idempotent handlers are the achievable and correct answer **[EVIDENCE]**. Where the side
effect is a database write in the *same* Postgres, the handler can make it genuinely
transactional by committing the result and the terminal status together — which is the
property Celery/RQ/arq structurally cannot offer and is the strongest single argument for this
design.

## 1.7 Priority, fairness, admission control

- **`priority smallint`, lower first.** Three named bands, not a free-for-all:
  `10 = interactive` (a user is watching an ingest progress bar), `100 = default`,
  `500 = maintenance` (retention, reaper, prune).
- **Per-tenant fairness is deliberately NOT built** (§6). One tenant enqueuing 10 000 ingests
  can starve another under strict priority ordering. The trigger to build it: more than one
  tenant doing bulk ingestion concurrently. Until then it is a solution to a problem this
  deployment does not have.
- **Admission control IS built, and this is something the requirements did not ask for.**
  A queue with no depth limit converts a bad afternoon into an outage: enqueue refuses with
  `429` when a tenant already has more than `max_pending_per_tenant` (default 1 000) live
  jobs, and the refusal is a *visible, audited* refusal — not a silent drop. Backpressure that
  is invisible is the same defect as a silent fallback.

## 1.8 Cancellation

Two layers, and they are different things:

1. **Queued jobs** — `UPDATE jobs SET status='cancelled' WHERE id=$1 AND status='pending'`.
   Guarded, so it can never race a claim.
2. **Running jobs** — set `cancel_requested = true`. The worker checks it at each heartbeat
   and at defined yield points, then raises a `JobCancelled` that maps to `status='cancelled'`.
   Cooperative, because Postgres cannot reach into a Python process.

**Reconcile with `plans/02` P0.4**, which specifies a process-wide `CancellationRegistry` for
*runs* mirroring the existing `ApprovalRegistry` (`aegis/src/aegis/agent/approvals.py`)
**[SOURCE]**. These are not competing mechanisms and must not become two:

- A **run** is a live SSE-attached agent execution in one process. In-process registry. Right.
- A **job** is a durable row that may be claimed by any process. DB flag. Right.
- **The bridge:** `POST /runs/{run_id}/cancel` sets the in-process token *and* issues
  `UPDATE jobs SET cancel_requested = true WHERE run_id = $1 AND status IN ('pending','running')`.
  One user action, both layers, because `jobs.run_id` exists.

## 1.9 Wakeup: `LISTEN/NOTIFY` with a polling floor

Polling alone adds latency equal to half the poll interval. `NOTIFY` alone loses wakeups
across a reconnect. Do both, and be explicit about which one is the correctness mechanism:

- Enqueue issues `NOTIFY aegis_jobs, '<kind>'`. Postgres delivers it **on commit**, so a
  worker can never be woken for a job it cannot yet see.
- The worker holds one dedicated asyncpg connection on `LISTEN aegis_jobs` and treats a
  notification as "wake up and claim now".
- **The poll is the floor, not the fallback**: every `poll_interval` (default 5 s) the worker
  claims regardless. This covers a dropped notification, a restart, and — the case polling is
  actually *required* for — a `run_after` in the future, which no `NOTIFY` will ever announce.

Correctness rests entirely on the poll. `NOTIFY` is a latency optimisation, and saying so
prevents someone later "simplifying" the poll away.

## 1.10 What executes jobs, and how it stays up on Windows

**One worker implementation. Two launch modes. The database is the only coordination.**

```
aegis/src/aegis/jobs/
  models.py      # Job, JobSchedule (AegisBase)
  queue.py       # enqueue / claim / heartbeat / finish / fail / cancel / reap  — pure SQL
  worker.py      # JobWorker: the claim→execute→finish loop, handler registry, LISTEN
  scheduler.py   # the materialiser (§1.13)
  handlers/      # one module per kind
backend/src/app/jobs/__main__.py   # `python -m app.jobs` — the standalone worker
```

| Mode | How | When |
|---|---|---|
| **In-process** | `asyncio.create_task(JobWorker(...).run(stop))` in `main.py`'s lifespan, beside the two existing sweepers, with the same `_supervise` callback (`main.py:66`) | Default. Demo day. Dev. Zero new processes, zero new install steps — matches how everything else in this stack starts. |
| **Standalone** | `python -m app.jobs --concurrency 4 --kinds ingest.*` | When ingestion should not compete with request latency, and for the "we can scale workers out" answer. |

Because claiming is a database operation, **N workers in M processes is safe by construction**
and needs no leader election. That is the property that makes both modes the same code.

**Staying up on Windows without systemd.** Three options, one recommendation:

| Option | Verdict |
|---|---|
| **In-process only** | **This is the default and it is fine for the hackathon.** The worker lives as long as uvicorn does. If uvicorn is up, work drains; if it is down, nothing is being served anyway. Say so plainly rather than building a supervisor nobody starts. |
| **NSSM** (Non-Sucking Service Manager) | **The recommended production path.** Registers `python -m app.jobs` with the Windows Service Control Manager: starts before login, survives logout, auto-restarts on crash with a throttle, and redirects stdout/stderr to rotating log files **[EVIDENCE]**. A single `nssm install` line in `scripts/`, no daemon of its own, no container. |
| **Windows Task Scheduler** | **No, for the worker.** It is built for run-and-exit tasks; it swallows errors and gives almost no observability when a job dies **[EVIDENCE]**. Worse, using it for *scheduling* would put "what runs when" in a second place outside the database — which is precisely what §1.13 refuses. |

**Ship the NSSM script and default to in-process.** Documenting the service path is a
Production-Roadmap point that costs one script; requiring it on demo day is a new failure mode
for zero demo value.

## 1.11 The tenancy trap — non-negotiable

This is the part that will be silently wrong if it is not written down.

**A worker claiming jobs must not have a tenant scope bound.** The claim spans all tenants. If
the worker bound one, it would only ever drain that tenant's queue.

**A job handler must run with its job's tenant scope bound.** Otherwise the handler reads and
writes under the fail-open branch of `_TENANT_ISOLATION_PREDICATE` (`rls.py:195`) and the
database stops isolating it. The pattern is not optional:

```python
async with sessionmaker() as s:          # claim: unscoped
    job = await claim_one(s, worker_id)

async with sessionmaker() as s:          # execute: scoped, always
    await set_tenant_scope(s, job.tenant_id)     # rls.py:124
    await handler(s, job)
```

**Two separate sessions**, because `set_tenant_scope` uses `set_config(..., is_local=true)`
which is transaction-scoped by design (`rls.py:148–162` explains why session-level `SET`
leaks across a pooled connection) **[SOURCE]**.

**Which engine.** The claim should run on the **admin/owner engine** (`get_admin_engine`,
`session.py:166`), because it is cross-tenant by nature; the handler runs on the **serving
engine**, subject to RLS, with the scope bound. That split is already built and is exactly
what it is for.

**The cross-item dependency, stated now so it is not discovered later.** Today the unscoped
claim works because the predicate fails *open* on an unset scope. `backlog-post-hackathon.md`
("RLS that fails closed on an unset scope", ~4d) names the SLA sweeper and the memory
consolidation sweeper as readers that will go silently empty when that flips. **The job
claim is the third, and A1's SQL console is the fourth (§4.6).** When that hardening lands,
the claim must move to a role that is deliberately cross-tenant. Put `jobs` on that
enumeration list now.

## 1.12 What moves onto the substrate, and what does not

| Work | Move? | Reason |
|---|---|---|
| Memory consolidation (`memory_consolidation_job`) | **Yes**, as `kind='memory.consolidate'` | It is the pattern being generalised. Keep the old table read-only for one release, sweep its stragglers, then drop. Retire `_run_memory_sweeper` (`main.py:93`). |
| Approval SLA expiry (`sweep_expired`) | **Yes**, as a scheduled `kind='approvals.sweep_sla'` | Correct today, but invisible: nobody can see when it last ran. On the substrate it gets a row, a duration, an error, and a health tile. Retire `run_sla_sweeper`. |
| Document ingestion (Phase 3) | **Yes** — the single biggest win | Docling parsing is minutes of CPU. Today it would have to run inside a request or a fire-and-forget task. As a job it is durable, restartable, progress-reportable, and cancellable — which is exactly what the "watch it ingest live" demo needs. `parent_job_id` gives the parse → chunk → embed → graph chain. |
| Eval / LLM-Ops runs (`aegis.ops`) | **Yes**, scheduled | Long, retryable, nobody is waiting. |
| Retention / partition maintenance (§2.5) | **Yes**, scheduled | Dogfooding. |
| Report generation (Phase 6 CSV) | **Not yet** | Current exports are small and synchronous. Move when one takes >5 s. Named so it is a conscious choice. |
| **The live agent run itself** | **No, and this matters** | A run is an interactive SSE stream with a human-in-the-loop interrupt. Putting it behind a queue would break the money shot. Runs stay in-process; jobs are the *deferred* work runs spawn, joined by `jobs.run_id`. |

## 1.13 The scheduler

**Recommendation: `job_schedules` + a materialiser inside the worker loop.** ~120 lines.

Every worker tick, before claiming, one worker materialises due schedules — atomically, using
the same primitive, so two workers cannot double-fire:

```sql
WITH due AS (
  SELECT id FROM job_schedules
   WHERE enabled AND next_run_at <= now()
   ORDER BY next_run_at
   FOR UPDATE SKIP LOCKED
   LIMIT 20
)
UPDATE job_schedules s
   SET next_run_at = <computed>, last_run_at = now()
  FROM due d WHERE s.id = d.id
RETURNING s.*;
```

Then insert one `jobs` row per returned schedule, with
`idempotency_key = f"sched:{schedule_id}:{fire_time.isoformat()}"` — so even a pathological
double-materialisation produces one job.

**Why not the alternatives:**

| Option | Verdict |
|---|---|
| **APScheduler 4.x** | **No.** Its own documentation states the 4.0 series is a pre-release that "may change in a backwards incompatible fashion without any migration pathway, so do NOT use this release in production" — still true in August 2026 **[EVIDENCE]**. Adopting an alpha for a national final is the wrong kind of not-conservative. |
| **APScheduler 3.x + `SQLAlchemyJobStore`** | **No.** Stable, but its job stores are not designed for several schedulers sharing one store — that redesign is exactly what 4.x is *for*. It would work today only because we run one process, which is the assumption §0.2 already flagged as accidental. |
| **`pg_cron`** | **No.** Needs `shared_preload_libraries` and a server restart, and on Windows an `nmake` build under the VS native tools prompt; the maintained Windows story is a community fork **[EVIDENCE]**. And it schedules *SQL*, not Python — so agent-shaped work would need a shim table anyway, i.e. this design with extra infrastructure. |
| **Windows Task Scheduler** | **No.** A second place where work is defined, poor error visibility **[EVIDENCE]**, and no tenant story. |

`job_schedules` also gives A/C3 ("almost zero code change from the dashboard") something real:
a tenant admin can enable, disable and re-time a schedule from a form, because a schedule is a
row.

Cron parsing: `croniter` is the small, obvious dependency; `interval_seconds` covers every
schedule this system currently needs, so **start with intervals and add `cron_expr` when
something actually needs "02:00 daily"**.

## 1.14 Batching

Batching is a property of a handler, not a feature of the queue. Two mechanisms, both already
implied by the schema:

- **Claim batching** — `LIMIT $1` in the claim. Measured 6× throughput at batch 20
  **[MEASURED]**. Set per worker, default 10.
- **Work batching** — a handler that processes a *set*. The one that matters is embedding:
  `kind='embed.chunks'` with `payload = {"chunk_ids": [...]}`, because embedding 64 texts in
  one call is dramatically cheaper than 64 calls. The chunker enqueues one job per batch.

**Do not build a generic "batch these jobs together" collector.** It is the third mechanism
where two suffice, and the only real batching case is already served by putting a list in a
payload.

---

# Part 2 — A3, end-to-end request tracking

> *"every query end to end logged and saved"*

## 2.1 There are already three mechanisms. Do not add a fourth.

| Mechanism | What it is today | Durable? | Tenant-scoped? |
|---|---|---|---|
| **OTel → Phoenix** | In-process Phoenix, `gen_ai.*` spans, full prompt/completion payloads, span tree (`observability/otel.py`) | No — dies with the process | No |
| **`run_summary()`** | A pure projection over the in-memory stamped event list | No — dies with the socket | N/A |
| **`audit_log`** | Durable, tenant-stamped, RLS-governed — but it records **decisions and admin actions**, not the anatomy of a run | Yes | Yes |

`plans/02` §2.2 already proposes the missing piece and I am not going to redesign it. **The
durable record is `run_events`, exactly as specified there.** My contribution is three
additions and the operational policy that proposal deliberately left to this plan.

## 2.2 The three additions

```sql
run_events (
  run_id     uuid,  seq int,  ts timestamptz,
  tenant_id  int,   user_id int,  session_id uuid,
  agent_id   text null,  type text,  payload jsonb,
  -- additions from this plan:
  trace_id   text null,   -- ADDITION 1
  span_id    text null,   -- ADDITION 1
  job_id     bigint null, -- ADDITION 2
  PRIMARY KEY (run_id, seq)
)
```

**Addition 1 — `trace_id` / `span_id`.** This is the whole reconciliation with OTel, and it
costs one function call: `aegis/src/aegis/observability/otel.py:124` already exposes
`current_trace_id()` **[SOURCE]**. Stamp it at `emit()`.

With it, the two systems stop competing and become two views of one run:

| | `run_events` | Phoenix / OTel |
|---|---|---|
| **Purpose** | The durable, replayable, tenant-scoped record of *what happened* | The deep-dive into *why it was slow or wrong* |
| **Lifetime** | Retention policy (§2.5) | Process lifetime; ephemeral by design |
| **Audience** | Tenants, auditors, the console, the jury | Us, while debugging |
| **Contains payloads?** | **No** — summaries, counts, ids, decisions | Yes — full prompts and completions |
| **Sampled?** | **Never** | Sampling is legitimate here |
| **Governed?** | RLS on `tenant_id` | Not tenant-aware |

Injecting the trace id into the durable record so you can jump from one to the other is the
standard correlation practice, not an invention **[EVIDENCE]**. A row in the console gets a
"open trace" link; that link is the *only* integration needed, and one column carries it.

**Addition 2 — `job_id`.** Deferred work spawned by a run (ingest, consolidation) emits events
too. Without this column, the durable record of a query stops at the moment the socket closes,
which is exactly half of "end to end". With `jobs.run_id` on one side and `run_events.job_id`
on the other, the join goes both ways.

**Addition 3 — a `runs` header row.**

```sql
runs (
  run_id uuid primary key, tenant_id int, user_id int, session_id uuid,
  started_at timestamptz, finished_at timestamptz, status text,
  query_preview text, model text, depth text,
  prompt_tokens int, completion_tokens int, cost_usd numeric(12,6),
  event_count int, trace_id text
)
```

I am adding a second table against my own "one mechanism" instinct, and here is the
justification, because it should not be waved through: the run list is the most frequently
loaded view in the console and in the health page, and building it by scanning events means
either a `GROUP BY` over the largest table in the schema on every page load, or a
`DISTINCT ON` that gets slower every day. `runs` is a **projection maintained by the same
sink** — two extra writes per run (start, terminal), no new subsystem, no new code path, and
it can be rebuilt from `run_events` at any time. A projection you can regenerate is not a
second source of truth.

Test it as one: a test that replays a run's events and asserts the rebuilt header equals the
stored one. That test is what keeps the header honest.

## 2.3 The write path

Take `plans/02` P0.3's design unchanged — bounded `asyncio.Queue`, one background drain task,
batched multi-row `INSERT`, drop-oldest with a **counted** warning, never blocking the stream
— with two clarifications:

- **The drop counter is surfaced, not just logged.** `run_events_dropped_total` appears on the
  health page. A lossy audit trail that looks lossless is worse than no audit trail. If the
  count is ever non-zero the page says "this run's log is incomplete", because that is true.
- **Terminal events flush synchronously.** Everything else may be dropped under pressure; the
  terminal event may not, or a run can end with no recorded ending and the header is wrong.

## 2.4 How a tenant sees only their own runs

Three layers, in the order they should be trusted:

1. **RLS** — register `run_events` and `runs` in `_TENANT_SCOPED_TABLES` (`rls.py:103`). Both
   get integer `tenant_id`, both get the `tenant_isolation` policy, and the boot-time read-back
   reports it if they do not.
2. **App-level scoping** — the read endpoints go through `_scope_tenant` (`routes.py:436`),
   the same helper the audit endpoint uses, so a tenant-admin cannot widen its scope with a
   query parameter.
3. **`user_id` for the per-user view.** The requirement is *"not for whole, per user"*
   (`en_1_v2.0.md` line 24). `WHERE user_id = …` for a normal user; a tenant-admin may select
   any user within its tenant; a platform admin may select any tenant. That is a filter on an
   indexed column, not a new permission model.

**What must never be written:** raw prompts, raw completions, retrieved passage bodies, tool
arguments containing PII. `run_events.payload` carries ids, counts, decisions, durations,
scores and short labels. The full text lives in Phoenix, which is local, ephemeral and
operator-only. This is not caution for its own sake — a durable, exportable, per-tenant table
containing every prompt every user ever typed is a data-protection liability that the CSV
export in Phase 6 would then hand to anyone with the admin role.

## 2.5 Retention

`audit_log` already grows forever with no policy — `backlog-post-hackathon.md` names it.
`run_events` will grow far faster (a team run emits 150–400 events, per `plans/02` P0.3), so
shipping it without a policy repeats a known defect at ten times the rate.

- **Partition `run_events` by month on `ts`** (`PARTITION BY RANGE`). Declared **at creation**,
  because converting a large heap table to partitioned later needs a migration and there is no
  migration tool. This is the one place where a scaling decision must be made now (§6).
- **A scheduled `kind='retention.sweep'` job** drops partitions older than the retention
  window and deletes matching `runs` rows. Dogfooding again.
- **Default window: 90 days for `run_events`, indefinite for `runs`.** The header is tiny; the
  bodies are not. So a run stays listed and costed forever while its blow-by-blow expires.
- **Per-tenant override** in `job_schedules.payload`, so retention is a dashboard setting.
  This is the honest answer to "what happens after a year" and it takes an afternoon.

**One caveat, stated rather than buried:** RLS and partitioning interact. Policies are per
table, and a partitioned parent's policy applies to its partitions — but the `rls.py` catalog
read-back filters on `relkind IN ('r','p')` (`rls.py:231`), which includes both partitioned
tables (`p`) and ordinary tables (`r`), so partitions will appear individually in the scan.
**Verify the read-back reports partitions correctly before shipping**, or the coverage report
becomes noisy and gets ignored — which is how a real gap hides.

---

# Part 3 — A2, pipeline health and audit

> *"full pipeline audits logs and checking if all working with logs of their own component"*

## 3.1 This is an aggregation, not a subsystem

Aegis already computes honest per-component truth in five places, and every one of them was
built under "measured, never claimed":

| Existing | Gives |
|---|---|
| `aegis/core/health.py` probes | Real reachability for Redis / Postgres / vector store |
| `RlsEnforcement` (`rls.py:584`) + the bootstrap read-back | Whether tenant isolation is actually on, and which tables are not covered |
| `GET /security/posture` (`routes.py:2594`) | Per-threat `enforced` / `partial` / `not_covered`, derived from live wiring |
| `GET /stack` (`platform/stack.py`) | Installed versions, `None` when genuinely absent |
| `latency_summary()` (`observability/latency.py`) | Real measured percentiles, with an honest `empty` state |

**The health page is a join over these plus three things that do not exist.** Anyone who
proposes a new metrics subsystem here should be shown this table first.

## 3.2 The three missing probes

1. **Neo4j.** There is no `probe_neo4j` **[SOURCE]**. Add it beside the other three: a driver
   `verify_connectivity()`.
2. **The LLM gateway.** There is no probe, and there should not be a synthetic one — a probe
   that spends money to prove it can spend money is a bad trade on $100 of credit. **Derive
   health from work already done:** last successful call timestamp and error rate over the
   last N calls, read from `usage_ledger`, which is already tenant-stamped and RLS-governed.
   Evidence of work beats a fabricated ping, and it matches the house rule.
3. **`/readyz`.** Documented in three docstrings, absent from `routes.py` **[SOURCE]**. Build
   it: run every probe concurrently, return 200 only if every *required* dependency is `up`,
   503 otherwise, with the per-component detail in the body.

## 3.3 What "healthy" means, per component

A status word with no definition is decoration. Each row below defines the verdict and the
consequence, so a red tile means something specific.

| Component | Signal | `healthy` | `degraded` | `down` — what breaks |
|---|---|---|---|---|
| Postgres | `SELECT 1` | answers | — | Everything. Fail the readiness probe. |
| Redis / Memurai | `PING` | answers | — | Semantic cache is off; every query costs full price. Not fatal. |
| Vector store | `list_collections()` on the path | answers | — | Retrieval and memory recall have no ANN arm. |
| Neo4j | `verify_connectivity()` | answers | — | Graph arm of RRF is absent; retrieval degrades to vector+BM25 and **must say so on the answer**. |
| LLM gateway | `usage_ledger` recency + error rate | success < 5 min ago, errors < 5% | errors 5–25% | No model calls. Nothing works. |
| **Job substrate** | oldest `pending` age; `running` past lease; `dead` count | oldest pending < 60 s, 0 over-lease | oldest pending > 5 min **or** any dead | Nothing deferred completes; ingestion silently never finishes. |
| **Workers** | `max(heartbeat)` per `locked_by` | a worker heartbeated < 60 s ago | some workers stale | No worker → the queue only grows. This is the alert nobody has today. |
| RLS | `audit_rls_enforcement()` (`rls.py:630`) + read-back shortfall | not bypassed, 0 uncovered | uncovered tables > 0 | Tenant isolation is inert. **Red, always, no exceptions.** |
| Run-event sink | queue depth + `dropped_total` | 0 dropped | any drops | The audit trail is lossy and must say so. |
| Phoenix | provider is the Phoenix one, not the console fallback | — | fallback in use | Traces are not being collected. Degraded, never fatal. |
| Guardrails | rails loaded (NeMo present) | loaded | programmatic only | Input/output rails weaker than claimed — must be visible, not silent. |

**Two states this page must have that most do not:**

- **`unknown` ≠ `down`.** A probe that timed out is not the same fact as a dependency that
  answered "no". Rendering the first as the second is a lie in the safe direction, and this
  project has already been bitten by lies in the safe direction.
- **Degraded is loud.** The house rule is no silent fallbacks. If Neo4j is down, retrieval
  keeps working *and the answer carries a banner saying the graph arm was unavailable*. The
  degradation is a property of the answer, not only of an admin page nobody has open.

## 3.4 "Logs of their own component" — and what I refuse to build

The literal request is per-component logs. The wrong response is a log-aggregation stack
(Loki, Seq, an ELK) on a 16 GB Windows box with no Docker. That is a whole second platform to
operate for a demo surface.

**Three real sources, honestly labelled:**

| Source | Covers | Honest limits |
|---|---|---|
| **`run_events`, filtered** | Every request-path component: guardrails, retrieval, agents, tools, memory, gateway | Durable, tenant-scoped, complete. This is the main answer. |
| **`jobs` rows** | Every background component: attempts, last error, duration, worker | Durable, tenant-scoped, complete. |
| **A bounded in-process structured-log ring buffer** | Everything else — boot, sweepers, driver warnings | **Per-process and volatile.** Labelled exactly like `latency_summary` labels its window (`source`, `window_capacity`), because that module already got this right and the pattern should be copied, not reinvented. |

The ring buffer is ~60 lines: a `logging.Handler` writing dicts into a `deque(maxlen=2000)`
with `trace_id` attached, exposed at `GET /platform/logs?component=…`, platform-admin only.

**Say plainly in the docs and on the page: this is not a log store.** If persistent logs across
restarts are needed later, the answer is stdout → NSSM's file redirection → a real store —
not a home-grown one. Naming the boundary is what stops it growing into one.

## 3.5 The endpoint and the page

```
GET /platform/health          -> [ComponentHealth]   admin/devops
GET /readyz                   -> 200 | 503           unauthenticated
GET /platform/jobs            -> queue depth by kind/status, oldest pending, dead, workers
GET /platform/logs?component= -> the ring buffer     platform admin
```

`ComponentHealth` is one shape for every component:
`{key, name, category, status: up|degraded|down|unknown, detail, measured_at, evidence}`.
**`evidence` is a required field** — the query or probe that produced the verdict — because a
status with no provenance is exactly the kind of claim the audits keep catching.

The page lands in the `devops` portal beside `stack` / `security` / `latency`
(`web/src/lib/portal.ts:281` **[SOURCE]**), with the job/worker tiles mirrored into `admin`.

---

# Part 4 — A1, the admin database-query page

> *"a page for aegis admin to check all users and other types of data… view full db not like
> to go in code or db checking"*

The highest-risk item in the whole set, and the one where the requirement and the safe design
genuinely diverge. So this section leads with what I measured, not with an opinion.

## 4.1 The threat model

The attacker is not a stranger — the page is platform-admin-only behind JWT. The realistic
threats are:

1. **A compromised or coerced admin session** (stolen token, XSS on the console, an admin
   pasting a query someone sent them). Read-only enforcement is what makes this survivable.
2. **Prompt injection reaching the query box.** If an Aegis MCP client or agent is ever given
   this capability — and `backlog-post-hackathon.md` explicitly wants an admin MCP client that
   "asks questions of the platform in natural language" — then a model-generated string
   reaches `execute()`. This is not hypothetical for this codebase; it is on the roadmap.
3. **Accidental self-harm.** `DELETE FROM users;` pasted at 2am the night before a final.
4. **Resource exhaustion.** A cartesian join that pins the one Postgres the whole demo needs.
5. **Bulk exfiltration.** Even perfectly read-only, this page can dump every tenant's data to
   CSV. Read-only is not the same as harmless.

## 4.2 Five measured findings that decide the design

Every one of these was run against a live Postgres. They are reproducible in Appendix A.

**Finding 1 — asyncpg's `execute()` with no arguments runs multiple statements, silently.**

```
await conn.execute("SELECT 1; SET default_transaction_read_only = off; SELECT 2;")
  -> returns 'SELECT 1'          # the status tag of the FIRST statement only
  -> default_transaction_read_only is now 'off'
```

The injected statement executed and the return value gave no hint. **[MEASURED]** Any SQL
console built on the obvious `await conn.execute(user_sql)` is multi-statement by default and
its result value will not tell you.

**Finding 2 — the extended protocol refuses multi-statement.** `fetch()`, or `execute()` with
any bind parameter, raises `PostgresSyntaxError: cannot insert multiple commands into a
prepared statement` **[MEASURED]**. So *how you send the query* is itself a control, and it is
free.

**Finding 3 — `SET LOCAL ROLE` is not a security boundary.**

```
BEGIN; SET LOCAL ROLE aegis_ro;   -- current_user = aegis_ro
RESET ROLE;                        -- current_user = yrevash  (the superuser)
SELECT count(*) FROM pg_authid;   -- 16
```

**[MEASURED]** `RESET ROLE` is a single legal statement, so even the extended protocol permits
it. **This kills the design where the console runs on the application's own connection with a
role temporarily assumed.** The console must use a separate login role over its own DSN and
pool. Nothing else is a boundary.

**Finding 4 — `default_transaction_read_only` is user-settable, so it is a guard rail, not a
boundary.** The read-only role turned its own setting off in a fresh session **[MEASURED]**.
What actually stopped the write was the absent grant: `permission denied for table t`. **The
boundary is the privilege, not the setting.** Keep the setting — it turns a mistake into a
clean error instead of a write — but never rely on it.

**Finding 5 — column-level grants work, and `information_schema` respects them for free.**

```
GRANT SELECT (id, username, role) ON users TO aegis_ro;   -- password_hash withheld
SELECT * FROM users;                        -> ERROR: permission denied for table users
SELECT id FROM users WHERE password_hash LIKE 'a%';  -> ERROR: permission denied
SELECT column_name FROM information_schema.columns WHERE table_name='users';
                                            -> id, username, role      (hash absent)
```

**[MEASURED]** Three things at once: `SELECT *` is refused, predicate-based inference is
refused, and **the catalog automatically hides the ungranted column** — so the schema browser
gets the correct column list from `information_schema` with no denylist to maintain and no way
for the two to drift apart. The permission model *is* the browser's source of truth.

## 4.3 The recommendation

**Build the hardened read-only execution path first. Put a schema browser and a saved-query
library on it. Then ship free-form SQL on that same path behind a platform-admin-only toggle.**

Not "browser instead of SQL". **One execution path, two front doors.** Reasoning:

- **The schema browser covers the stated need.** *"Check all users and other types of data,
  view full db"* is browsing — list tables, list columns, page rows, filter, sort, follow a
  foreign key, export. Every one of those is a *generated, parameterised* query. Zero
  user-authored SQL, and it is the better product for the actual task: it cannot typo, it
  paginates correctly, and it renders `jsonb` and enums properly.
- **Free-form SQL is what was asked for and it is genuinely more powerful.** Refusing it
  because it *sounds* dangerous is the conservative failure mode this project has already
  made twice. It is not dangerous once the path is right — every control in §4.4 applies
  identically to a generated query and a typed one.
- **The ordering is the whole recommendation.** Building the box first and hardening later
  means shipping the attack surface before the mitigation. Building the path first means the
  free-form box is a *UI change*, not a security change.
- **The cut order follows from that:** if something has to drop, drop the free-form box. The
  path and the browser are the demo; the box is the power tool.

**The honest tension:** a curated browser cannot answer "which tenants have budget rows with no
matching usage in the last 30 days" — an ad-hoc join a competent operator writes in twenty
seconds. Saved queries close *most* of that gap and close it better, because a saved query is
named, reviewed, parameterised and re-runnable. They do not close all of it. Free-form exists
for the remainder, and it is a small remainder — which is precisely why it should not be the
thing built first.

**Precedent worth knowing.** Metabase — a mature product whose entire business is this
problem — **disables native SQL queries for any database with row/column security
restrictions, because it cannot parse SQL well enough to know which tables a query touches**
**[EVIDENCE]**. Aegis has RLS on 13 tables. That is a serious product's considered verdict
that free-form SQL and row-level security do not compose safely through string analysis. It is
why §4.4 puts every control in the *database role*, and never in a parser or a regex.

## 4.4 The controls

Ordered by how much they actually carry.

| # | Control | Why, given the findings |
|---|---|---|
| 1 | **A dedicated login role `aegis_readonly`, `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, owning nothing, with `GRANT SELECT` only** — and column-level grants that withhold `users.password_hash` | Finding 4: the privilege is the boundary. Finding 5: withheld columns disappear from the catalog too. Mirrors `SERVING_ROLE`'s provisioning (`rls.py:543`, `scripts/sql/aegis-app-role.sql`) — a third `.sql` file beside it, not a new pattern. |
| 2 | **Its own DSN and its own engine/pool.** Never `SET ROLE` on the app connection | Finding 3. This is not negotiable. |
| 3 | **Every query sent over the extended protocol** — via `fetch()`, or `execute()` with at least one parameter | Finding 2 turns multi-statement injection into a syntax error at the driver, for free. A regex over the string is the control this replaces, and regexes over SQL lose. |
| 4 | `ALTER ROLE aegis_readonly SET statement_timeout = '10s'` | Verified: `pg_sleep(10)` → `canceling statement due to statement timeout` **[MEASURED]**. Role-level so it applies even if the app forgets. |
| 5 | `SET idle_in_transaction_session_timeout = '30s'` | A console tab left open must not hold a transaction and block VACUUM. |
| 6 | `ALTER ROLE aegis_readonly SET default_transaction_read_only = on` | Belt, not boundary (Finding 4). Turns a mistake into a clean error. |
| 7 | **Row cap and byte cap.** Wrap as `SELECT * FROM (<query>) _q LIMIT 1001` — 1000 shown, the 1001st proves truncation — and abort past ~5 MB serialised | Threat 5. "Read-only" does not mean "cannot exfiltrate". |
| 8 | **`EXPLAIN` pre-flight.** Run `EXPLAIN` (not `ANALYZE`) first; refuse above a cost ceiling and show the plan | Turns "your query timed out after 10 s" into "this would scan 40M rows, here's why". Better product *and* cheaper failure. |
| 9 | **An audit row per query, always** — `db.query.execute` with the SQL text, parameters, row count, bytes, duration, verdict, and `via: 'browser'|'saved'|'freeform'` | Phase 6 already establishes that an export of the audit trail must itself be audited (`phase-06`, task 6.4). Same principle. Write the row **before** execution and update it after, so a query that kills the process still leaves a trace. |
| 10 | **`require_platform_admin`, not `require_admin`** | `require_admin` admits both tiers (`phase-06` §3 **[SOURCE]**). A tenant admin must never reach this page. |
| 11 | **Rate limit** — N queries/minute/admin | There is no rate limiting anywhere in `backend/src` today (`backlog-post-hackathon.md`). This page is the worst place for that to remain true. |
| 12 | **Kill switch** — `ADMIN_SQL_CONSOLE=off` disables it entirely, default off outside dev | A feature that can be turned off is a feature a procurement reviewer can accept. |

**What is deliberately NOT a control: a SQL parser, a keyword denylist, or a regex.** They are
bypassable (comments, casing, unicode, nested CTEs), they create false confidence, and every
threat they aim at is already closed by controls 1–3 at the layer that cannot be tricked.
`01-V2-ADDITIONS.md` said "a separate Postgres role, not a regex over the string" and that is
exactly right.

## 4.5 The schema browser, concretely

- **Catalog from `information_schema`**, executed *as* `aegis_readonly` — so it shows exactly
  what that role can read (Finding 5).
- **Table view**: server-side pagination with keyset pagination on the primary key (not
  `OFFSET`, which degrades on large tables), sort, per-column filter. Every one a
  parameterised query built from a validated identifier against the catalog — identifiers are
  **matched against the catalog list**, never string-escaped, which is the same discipline
  `_SAFE_ROLE_NAME` uses at `rls.py:551` **[SOURCE]**.
- **Foreign-key navigation** from `information_schema.referential_constraints`. This is what
  actually makes "view full db" pleasant: click a `tenant_id`, land on the tenant.
- **Row-count estimates from `pg_class.reltuples`** for the table list, exact counts only on
  request. An exact `count(*)` on every table on page load is how this page becomes the
  slowest thing in the product.
- **Saved queries**: a `saved_queries` table (name, sql, `params jsonb`, owner, `tenant_id`,
  `created_at`), executed through the same path with the same controls, parameters bound —
  never interpolated.
- **CSV export** through the same cap and the same audit row.

## 4.6 RLS and an admin who is *meant* to see across tenants

This is the subtlest part and it must be written down or it will be got wrong.

`aegis_readonly` is `NOBYPASSRLS` and the console binds **no** tenant scope. Under the current
predicate (`rls.py:195`), an unbound scope means the policy does not restrict — so the admin
sees every tenant's rows. **That is the desired behaviour, and it works today only because of
the deliberate fail-open branch.**

Two consequences:

1. **Do not give this role `BYPASSRLS`.** It is unnecessary today, and it would make the page
   permanently invisible to the isolation model — the one place we least want a blind spot.
2. **When the backlog's "RLS that fails closed on an unset scope" hardening lands, this page
   goes silently empty.** That item already enumerates `list_recent_audit`, the LLM-Ops cache
   warm, and both sweepers as readers that break. **Add the admin SQL console and the job
   claim (§1.11) to that list now.** The fix at that time is an explicit, audited
   "cross-tenant read" mode — a `SECURITY DEFINER` path or a role granted a deliberate
   cross-tenant policy — not a quiet re-grant of `BYPASSRLS`.

**And the feature this unlocks, which is better than free-form SQL for the jury:** a
**tenant-impersonation toggle**. Bind `app.tenant_id` to a chosen tenant and re-run the same
query. The admin sees, live, exactly what that tenant's connection can see — and demonstrating
row-level security by *watching rows disappear when you bind a scope* is the most convincing
thirty seconds of the isolation story available. One `set_config` call.

---

# Part 5 — A5, pipeline structure per task, and module docs

> *"for eg rag or agent — everything should have a proper flow pipeline and all components…
> clear docs of how each module is implemented"*

## 5.1 What already exists — and it is a lot

Assessed before proposing anything, because the brief asks for the gap and not a rewrite:

| Asset | State |
|---|---|
| `docs/teaching/` | **16 module folders**, each with `10-guide.md`, `40-diagrams.md`, `50-interview.md`, plus foundations, a README with a reading order, a `STYLE.md` writing contract, and generated HTML **[SOURCE]**. Current and accurate; commit `bb387d4` says writing it found and fixed three bugs. |
| `docs/module/MODULE_REFERENCE.md` | The Module Contract (3 pillars), the module map, the honest debt list **[SOURCE]**. |
| `GET /agent/topology` | **Reads the real node/edge shape off the compiled LangGraph** via `graph_topology()`, and its docstring records why: the console's hand-written DAG had drifted to 9 nodes against a real 15 and hung the approval branch off the wrong step **[SOURCE]**. |
| `GET /stack`, `GET /platform/capabilities` | Live, resolved-at-request-time module and version manifests. |

**Anyone proposing to rewrite the teaching course is proposing to destroy the most complete
asset in the repository.** The gap is elsewhere.

## 5.2 The actual gap

`/agent/topology` exists for the agent **because the agent graph is declared** (LangGraph
nodes and edges) and can therefore be read back. **Retrieval and ingestion have no such
declaration.** Their pipelines are real — `aegis/retrieval/pipeline.py`, `fusion.py`,
`reranker.py`, `query_rewrite.py`, `spotlight.py`, `chunker.py`, `graph_extract.py`
**[SOURCE]** — but the *shape* exists only as control flow inside a method. So:

- Any diagram of the RAG pipeline is hand-drawn, and hand-drawn diagrams drift. **The exact
  failure `/agent/topology` was built to fix is currently live for retrieval.**
- The console cannot render "which stages ran, which were skipped, how long each took" without
  restating the pipeline in TypeScript — the second copy that drifts.
- There is nowhere to attach per-stage health (A2) or per-stage timing (A3).

## 5.3 The recommendation: one declaration, four consumers

Declare each pipeline once as data. Everything else reads it.

```python
@dataclass(frozen=True)
class Stage:
    key: str                    # 'rerank'
    name: str                   # 'Cross-encoder rerank'
    tech: str                   # honest underlying tech — the branding rule
    module: str                 # 'aegis.retrieval.reranker'
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    optional: bool              # can be skipped, and the run says whether it was
    skip_reason_when_off: str   # what breaks — the "no silent fallback" string

@dataclass(frozen=True)
class PipelineSpec:
    key: str
    name: str
    stages: tuple[Stage, ...]
    edges: tuple[tuple[str, str], ...]
```

Four consumers, one source:

1. **Runtime** — the pipeline emits `stage_started` / `stage_finished` events with the stage
   `key`, so `run_events` carries the pipeline's own vocabulary and A3's per-stage timing is
   free.
2. **API** — `GET /pipelines` and `GET /pipelines/{key}`, alongside `/agent/topology`.
3. **Console** — draws the flow from the spec and overlays the live run. The stage that was
   skipped is greyed with its `skip_reason_when_off`, which is the no-silent-fallback rule
   rendered as UI.
4. **Docs** — a generator writes `docs/teaching/<module>/20-pipeline.md` from the spec, into
   the existing folder shape. Generated, marked as generated, regenerated in CI.

**And a drift test**, which is the part that makes it worth doing: assert that every stage key
declared is emitted at least once by a real end-to-end run of that pipeline, and that no
undeclared stage key is emitted. That test is what makes the diagram *true* rather than
*current*. Without it this is documentation with extra steps.

**Scope: three pipelines.** `rag_query`, `agent_run` (adapts the existing `graph_topology()`
onto the same shape), `ingest_document`. Not sixteen modules — most modules are libraries, not
pipelines, and `docs/teaching/` already covers them properly.

## 5.4 Module docs — the small, honest addition

Do **not** add a fourth file to sixteen folders. Do:

- `20-pipeline.md` in the three pipeline folders only, **generated**.
- One section in `MODULE_REFERENCE.md` generated from the capabilities manifest and the
  installed-distribution resolution `/stack` already does — so the module map cannot claim a
  module that is not installed.
- Extend the existing `STYLE.md` contract with one rule: *a hand-written diagram of anything
  that has a `PipelineSpec` is a bug*. That single sentence prevents the drift returning.

---

# Part 6 — A6, enterprise scale, honestly

> *"scaling system to keep in mind"*

This is a hackathon demo that must *credibly* scale, not a system serving load. Conflating
those is how projects die. Three columns, and I am saying plainly which is which.

## 6.1 Genuinely needed now

| Item | Why now |
|---|---|
| The job substrate (§1) | Without it, ingestion runs inside a request or a fire-and-forget task, and a Docling parse either blocks or is lost. |
| Lease + reaper | Without it, one killed process permanently strands work — the live bug in `consolidate.py` today. |
| Admission control / queue depth cap (§1.7) | One bulk ingest with no ceiling turns into an unbounded queue. |
| `run_events` + `runs` (§2) | A3 *is* this. It is also replay, which is the best demo insurance available. |
| Retention policy + monthly partitioning **declared at creation** (§2.5) | The only genuinely irreversible decision here. Converting a large heap table to partitioned needs a migration, and there is no migration tool. **Decide now or accept a rewrite.** |
| Job/worker health + `/readyz` (§3) | A queue with no observability is a place work goes to disappear. |
| Indexes as specified (§1.2) | Partial indexes are cheap now and a lock-taking `CREATE INDEX` later. |
| **Governance context on every job** | See §6.4. Currently missing and genuinely load-bearing. |

## 6.2 Architecture that must not foreclose scaling — build the seam, not the feature

| Seam | Cost now | What it unblocks |
|---|---|---|
| **Workers coordinate only through the DB** | Zero — it is a consequence of the claim design | Scaling out is starting another process. No leader election, ever. |
| **`kind` is a routing key**; workers can filter by kind pattern | One CLI flag | Dedicated ingestion workers without a second queue. |
| **`priority` column exists, fairness policy does not** | One `smallint` | Per-tenant fairness becomes a `WHERE` clause change, not a schema change. |
| **`parent_job_id` exists, no DAG engine** | One nullable column | Multi-stage ingestion chains and provenance today; a real DAG engine later if ever justified. |
| **Handlers are idempotent by contract** | Discipline, not code | At-least-once stays safe at any concurrency. |
| **`run_events` is append-only and partitioned** | Declared at creation | Archival, export, and a real analytics store later without touching the write path. |
| **The health page is an aggregation with a stable `ComponentHealth` shape** | Zero | Exporting to Prometheus later is one adapter, not a rewrite. |
| **The console runs on its own DB role** | One `.sql` file | Every later hardening is a `GRANT`/`REVOKE`, not application surgery. |

## 6.3 Premature — named, so nobody starts one

| Not building | Why not, and the trigger that would change it |
|---|---|
| A broker (Redis Streams / RabbitMQ / Kafka) | Measured headroom is 20 877 claims/s against a workload of tens per minute **[MEASURED]**. Trigger: sustained >1 000 jobs/s, or non-Python workers. |
| Per-tenant fair scheduling | Trigger: two tenants doing bulk ingest concurrently. |
| A DAG / workflow engine (Temporal, Prefect, Airflow) | A daemon, a UI and a second operational surface for chains three steps long. `parent_job_id` covers it. |
| Multi-process uvicorn (`--workers N`) | 16 GB, one demo user. It would multiply model memory and add nothing. The substrate is already safe for it, which is the point. |
| PgBouncer / external pooling | SQLAlchemy's pool is sufficient at this connection count. And `LISTEN/NOTIFY` does not survive transaction pooling — a real cost for zero gain. |
| Read replicas, sharding, partition-by-tenant | No read pressure exists. Partition-by-month is the one partition decision worth making. |
| A log aggregation stack | §3.4. A whole second platform for a page. |
| A metrics backend (Prometheus/Grafana) | The health page reads live sources. Trigger: needing history across restarts — and then it is one exporter. |
| Job result streaming / progress websockets | `run_events` + SSE already carry progress. |
| Exactly-once delivery | Not achievable without distributed transactions; idempotent handlers are the correct answer **[EVIDENCE]**. |
| Autoscaling, containers, Kubernetes | The deployment target is a Windows laptop. Saying this out loud is worth more than pretending otherwise. |

## 6.4 The thing nobody asked about, and it is the sharpest gap I found

**Background jobs spend money, and nothing currently stops them.**

Budget enforcement runs on the request path through `enforce_governance` and the `usage_ledger`
cap sums. A job that calls the LLM gateway from a background worker is outside that path unless
it is deliberately put back inside it. The memory consolidation sweeper *already* makes cheap-
model calls from a background task (`main.py:105–118` binds the live `complete` and the real
embedder **[SOURCE]**), with `memory_sweeper_batch = 10` every 60 s.

On $100 of gateway credit before a national final, an unbounded background spender is not a
theoretical risk. **Every job that can call a model must carry the enqueueing request's
governance context** — tenant, user, budget scope — and spend through the same enforcer, so a
`BudgetExceededError` in a job is a first-class outcome (§1.5) rather than a surprise on the
invoice. The `set_governance_context` contextvar (`routes.py:922`, per `plans/02` P0.5
**[SOURCE]**) is the existing seam; the job payload carries what is needed to rebind it.

**This is the single most valuable thing in this document that was not on the requirements
list**, and it costs a payload field plus a `with` block.

---

# Part 7 — Sequence, by dependency

No dates. Ordered so nothing is built on something that does not exist.

| # | Work | Depends on | Unblocks |
|---|---|---|---|
| **1** | `jobs` + `job_schedules` tables, RLS registration, `queue.py` (enqueue/claim/heartbeat/finish/fail/cancel/reap), the tenancy pattern of §1.11 | — | everything |
| **2** | `JobWorker` + handler registry + `LISTEN/NOTIFY` + in-process launch in `main.py` | 1 | 3, 4, 5 |
| **3** | Move memory consolidation and the SLA sweep onto it; sweep the stranded `RUNNING` rows; retire both bespoke sweepers | 2 | proves the substrate on real work before anything new rides it |
| **4** | The scheduler materialiser + the reaper and retention as scheduled jobs | 2 | 8 |
| **5** | `run_events` + `runs` + the batched sink + `trace_id`/`job_id` stamping | 2 | A3, replay, per-stage timing, the health page's latency tile |
| **6** | `probe_neo4j`, the gateway-health derivation, `/readyz`, `GET /platform/health`, `GET /platform/jobs` | 2, 5 | A2 |
| **7** | `aegis_readonly` role + `.sql` provisioning + the hardened execution path + audit row | — (independent) | A1 |
| **8** | The schema browser + saved queries on that path | 7 | A1 delivered |
| **9** | Free-form SQL box + `EXPLAIN` pre-flight + tenant-impersonation toggle | 8 | A1 complete |
| **10** | `PipelineSpec` for `rag_query` / `ingest_document`, `/pipelines`, the drift test | 5 | A5 |
| **11** | Generated `20-pipeline.md`, `MODULE_REFERENCE` generated section, the `STYLE.md` rule | 10 | A5 complete |
| **12** | Health page UI, jobs page UI, per-component log view | 6 | A2 delivered |

**7 is independent of 1–6** and can run in parallel; it touches no shared code.

**Cut order if something has to give:** 9 first (free-form SQL), then 11 (generated docs), then
12's log view. **Never cut the lease/reaper from 1, and never cut the audit row from 7** — a
queue without a reaper and a SQL console without an audit trail are the two shapes of this work
that are worse than not doing it.

---

# Part 8 — How each claim gets proved

Because "measured, never claimed" applies to this plan too. Each row is a test, not a
screenshot.

| Claim | Proof |
|---|---|
| Two workers never run the same job | Concurrency test: N workers, M jobs, assert every job ran exactly once. The probe shape in Appendix A, as a `pytest` against a live Postgres. |
| A dead worker's job is recovered | Claim a job, never heartbeat, run the reaper, assert `pending` with `attempts=1` and a backed-off `run_after`. |
| A poison job dies rather than loops | Handler always raises; assert `status='dead'` after exactly `max_attempts`. |
| Idempotency holds | Enqueue the same key twice concurrently; assert one row. |
| A job cannot read another tenant's rows | Live-Postgres test in the shape of Phase 1's isolation test: two tenants, one handler, assert the scoped session sees one tenant's rows. |
| A tenant sees only their own runs | Two tenants, `GET /runs` as each, assert disjoint. |
| The run header matches its events | Replay events, rebuild the header, assert equality with the stored row. |
| The health page never fabricates | Kill Neo4j, assert the component reads `down` with a real detail string and the answer carries the degradation banner. |
| The SQL console cannot write | Attempt `INSERT`/`UPDATE`/`DELETE`/`CREATE` as `aegis_readonly`; assert `permission denied`, not merely a read-only transaction error. |
| The SQL console cannot run two statements | Send `SELECT 1; SELECT 2;`; assert the driver raises. |
| The SQL console cannot escalate | Send `RESET ROLE`; assert `current_user` is still `aegis_readonly` (it is a separate login, so there is nothing to reset to). |
| `password_hash` is unreachable | `SELECT *` and a `WHERE password_hash …` predicate both refused; `information_schema.columns` does not list it. |
| Every query is audited | Run one; assert the newest `audit_log` row is `db.query.execute` with the SQL text. |
| The pipeline diagram is true | The drift test in §5.3. |

---

# Part 9 — Open questions, with my defaults

Where I would not proceed without a decision, and what I do if none comes.

1. **Free-form SQL: ship or hold?** I recommend shipping it, after the path and the browser
   (§4.3). **Default if undecided: build 7 and 8, leave 9 behind an env flag that is off.** The
   capability exists, the risk is not live, and the flag is a one-line demo-day decision.
2. **Run-event retention window.** I propose 90 days for events, indefinite for headers.
   **Default: 90/indefinite, per-tenant overridable from `job_schedules`.**
3. **Does `run_events` ever store prompt text?** I say no (§2.4). If a tenant-facing "show me
   exactly what was sent" feature is wanted later, that is a *separate, opt-in, retention-
   bounded* table — not a widening of this one. **Default: no.**
4. **Standalone worker on demo day?** **Default: no.** In-process only; ship the NSSM script
   and document it. Fewer moving parts on stage.
5. **`croniter` dependency?** **Default: no, initially.** `interval_seconds` covers every
   current schedule. Add it when something genuinely needs a wall-clock time.
6. **Does the admin SQL console ship in the hackathon build at all?** It is a Production
   Roadmap and Innovation point, and §4 makes it defensible. **Default: yes, browser only
   (7 + 8), free-form flagged off.**

---

# Appendix A — the probes, reproducible

Run against PostgreSQL 14.18 and asyncpg 0.31 in a throwaway venv and a scratch database.
Nothing touched `taif`.

**A.1 — Read-only role controls.** Created `aegis_ro` (`LOGIN NOSUPERUSER NOBYPASSRLS`,
`GRANT SELECT` only, `default_transaction_read_only=on`, `statement_timeout=3s`) on a scratch
DB with an RLS-protected table carrying the production `tenant_isolation` predicate.

```
INSERT           -> ERROR: cannot execute INSERT in a read-only transaction
pg_sleep(10)     -> ERROR: canceling statement due to statement timeout
count(*) unscoped                       -> 2      (fail-open branch: sees both tenants)
set_config('app.tenant_id','2'); count  -> 1, 'tenant-two'
pg_authid        -> ERROR: permission denied for table pg_authid
pg_read_file     -> ERROR: permission denied for function pg_read_file
```

**A.2 — `default_transaction_read_only` is user-settable.** In a fresh session the role ran
`SET default_transaction_read_only = off` successfully; the subsequent `INSERT` then failed
with `permission denied for table t`. **The privilege stopped it, not the setting.**

**A.3 — Column-level grants.** `GRANT SELECT (id, username, role)`, `password_hash` withheld:
`SELECT *` → `permission denied for table users2`; `WHERE password_hash LIKE 'a%'` →
`permission denied`; `information_schema.columns` returned `id, username, role` only.

**A.4 — asyncpg protocol behaviour.**

```
execute("SELECT 1; SET default_transaction_read_only = off; SELECT 2;")
    -> returned 'SELECT 1'; setting became 'off'.          # multi-statement, silent
fetch("SELECT 1; SELECT 2;")
    -> PostgresSyntaxError: cannot insert multiple commands into a prepared statement
execute("SELECT $1::int; SELECT 2;", 1)
    -> PostgresSyntaxError: cannot insert multiple commands into a prepared statement
BEGIN; SET LOCAL ROLE aegis_ro;  current_user = 'aegis_ro'
RESET ROLE;                      current_user = 'yrevash';  pg_authid readable (16 rows)
```

**A.5 — Claim throughput.** 20 000 rows, the §1.2 partial index, the §1.3 claim statement,
`counts` asserted for duplicates:

```
workers=4 batch=1  : 20 000 claimed in 0.96s ->  20 877 claims/s   duplicates=0
workers=4 batch=20 : 20 000 claimed in 0.16s -> 128 085 claims/s   duplicates=0
workers=8 batch=50 : 20 000 claimed in 0.18s -> 113 708 claims/s   duplicates=0
```

**A.6 — procrastinate 3.9.0 schema.** Installed in the throwaway venv; `schema.sql` inspected:
4 tables (`procrastinate_jobs`, `procrastinate_workers`, `procrastinate_events`,
`procrastinate_periodic_defers`), 18 stored functions, 7 triggers, 40 migration files,
**0 occurrences of "tenant"**.

---

# Appendix B — sources

- Postgres queues at scale, `FOR UPDATE SKIP LOCKED` as the mainstream mechanism, and Rails 8
  making Solid Queue the default over Redis (37signals: ~20M jobs/day on Postgres) —
  [rails/solid_queue](https://github.com/rails/solid_queue/),
  [Saeloun](https://blog.saeloun.com/2026/05/26/rails-8-solid-queue-database-backed-active-job/),
  [byteiota](https://byteiota.com/solidqueue-rails-8-postgresql-beats-redis-for-95/),
  [Simple Thread](https://www.simplethread.com/redis-solidqueue/)
- Procrastinate — Postgres task queue for Python, `LISTEN/NOTIFY` + `SKIP LOCKED`, actively
  maintained — [GitHub](https://github.com/procrastinate-org/procrastinate),
  [docs](https://procrastinate.readthedocs.io/)
- Visibility timeout as a *lease*, heartbeat/renewal bounds, DLQ and redrive, retry
  classification, exponential backoff with jitter, at-least-once and idempotency —
  [task-queues.com](https://www.task-queues.com/queue-fundamentals-architecture/visibility-timeout-deep-dive/),
  [OneUptime](https://oneuptime.com/blog/post/2026-07-22-sqs-visibility-timeouts-concurrent-processing/view),
  [Moments Log](https://www.momentslog.com/development/background-job-retry-policy-checklist-how-to-prevent-queues-from-amplifying-production-failures)
- APScheduler 4.0 is a pre-release and its own docs say not to use it in production —
  [PyPI 4.0.0a6](https://pypi.org/project/APScheduler/4.0.0a6/),
  [migration guide](https://apscheduler.readthedocs.io/en/master/migration.html)
- `pg_cron` requires `shared_preload_libraries` and a restart; Windows needs an `nmake` build
  under the VS native tools prompt, with a community fork for compatibility —
  [citusdata/pg_cron](https://github.com/citusdata/pg_cron),
  [pg_cron_windows](https://github.com/hakanrw/pg_cron_windows)
- NSSM as a Windows service supervisor (SCM integration, auto-restart, log redirection) versus
  Task Scheduler (run-and-exit, poor error visibility) —
  [MSSQLTips](https://www.mssqltips.com/sqlservertip/7325/how-to-run-a-python-script-windows-service-nssm/),
  [XDA](https://www.xda-developers.com/nssm-service-automation-windows-pc/)
- Metabase disables native SQL for databases with row/column security because it cannot parse
  SQL to know which tables a query touches; dedicated read-only DB user plus query timeout is
  its recommended posture —
  [Metabase data permissions](https://www.metabase.com/docs/latest/permissions/data),
  [users, roles and privileges](https://www.metabase.com/docs/latest/databases/users-roles-privileges),
  [statement timeouts](https://oneuptime.com/blog/post/2026-01-16-postgresql-statement-timeouts/view)
- Correlating a durable record with traces by carrying `trace_id`/`span_id` —
  [OpenTelemetry logs spec](https://opentelemetry.io/docs/specs/otel/logs/),
  [OneUptime](https://oneuptime.com/blog/post/2026-02-06-inject-trace-ids-application-logs-opentelemetry/view)
