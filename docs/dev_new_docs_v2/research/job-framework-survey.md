# Job/worker/scheduler framework survey — build vs buy, re-opened

**Written 2026-08-18.** Commissioned because the prior evaluation
([`plans/04-enterprise-substrate.md`](../plans/04-enterprise-substrate.md) §1.1) looked at seven
options — Celery, RQ, arq, procrastinate, APScheduler, `pg_cron`, Windows Task Scheduler — and
concluded "build it on Postgres". That is a slice of the field, not the field. The user's
objection was correct and this document widens the survey to 24 candidates across five
categories, including the two that matter most and were never named: **Temporal** and the
**Postgres-native library generation that already exists** (pgqueuer, DBOS, Chancy).

**Verdict in one paragraph.** The build-it decision survives, but **not for the reason the plan
gives**. "No new infrastructure on a Docker-less Windows box" turned out to be the *weakest*
argument — measured, Temporal installs in one 42 MB zip with zero dependencies and 123 MB of
RAM, which is cheaper than several things Aegis already installs. The decision survives on one
constraint only: **every candidate's job table is invisible to `rls.py`'s catalog read-back**,
because not one of the 24 has a `tenant_id` column, and the diagnostic built to catch exactly
that failure would report healthy while four-to-eight tables of tenant work sat ungoverned.
Phase 3 is the right call and is *not yet* SOTA in five specific ways this survey names, all
stealable from the frameworks it beats.

---

## How to read the claims here

| Marker | Means |
|---|---|
| **[MEASURED]** | Run on this machine, in a throwaway venv at `scratchpad/jobsurvey`, or against a real binary I downloaded and executed. The exact commands are in Appendix A and you can re-run them. |
| **[SOURCE]** | Read in this repository or in the installed package's own source, file and line given. |
| **[DOC]** | From the project's own current documentation. Cited. |
| **[THIN]** | Evidence I could not verify to my own satisfaction. Said so rather than rounded up. |

The venv is CPython **3.11.11** (the uv-managed interpreter this repo already uses), isolated in
the scratchpad. `backend/.venv` and `aegis/.venv` were not touched. No application code was
written; no plan or phase file was modified.

**One method note that carries most of the Windows evidence.** I could not run Windows here, so
instead of hand-waving I resolved every candidate *against Aegis's real dependency set, for the
Windows target*:

```
uv pip compile backend/pyproject.toml <candidate> --all-extras \
   --python-version 3.11 --python-platform x86_64-pc-windows-msvc
```

That is a full resolution of the actual 243-package graph plus the candidate, for
`win_amd64`/cp311. It proves wheels exist and that the candidate does not force a downgrade. It
does **not** prove the thing runs correctly on Windows — where that distinction matters I say
so.

---

## 1. The verdict table

Baseline for the "adds" column: **243 packages** resolve for Aegis's `--all-extras` on
Windows/py3.11 today **[MEASURED]**.

### Brokers

| Candidate | Installs on Windows, no Docker? | Multi-tenant story | Async | Scheduler | Operational cost | Verdict |
|---|---|---|---|---|---|---|
| **RabbitMQ** | Yes, but: 64-bit **Erlang/OTP installed first, as an administrator**, ASCII-only install path, `.erlang.cookie` copy from `%SystemRoot%\system32\config\systemprofile`, 8+ firewall ports, and env-var changes require *re-installing* the service, not restarting it **[DOC]** | None. Queue state lives outside Postgres | Via aio-pika | No | Two daemons (Erlang VM + broker), a second failure domain, a second thing to explain to a jury | **No** — failed *operational cost*. It is not un-installable, it is the most expensive install in the survey for capabilities we do not need |
| **Redis Streams via Memurai** | **Already installed.** Memurai 4 is Redis **7.4.9**-compatible and streams are a supported type **[DOC]** — so consumer groups (`XADD`/`XREADGROUP`/`XACK`) are available | None | Yes (`redis-py` asyncio) | No | **See the Memurai finding in §3 — the Developer edition shuts down after 10 days of uptime and is licensed non-production** | **No** — failed *durability + tenancy*. Job state in Memurai, business data in Postgres: a job and its result can never commit together |
| **NATS / JetStream** | **Yes, and it is the cheapest broker install in the survey.** Single `nats-server.exe`, native Windows-service support built into the binary (`sc.exe` / `--signal`), JetStream enabled with one `-js` flag **[DOC]** | Accounts give real isolation, but they are a *second* tenancy model to keep in sync with `users.tenant_id` | Yes (`nats-py`, +2 pkgs) | No (JetStream has no cron) | One extra process, one port, one config file | **No** — failed *tenancy*, not Windows. **This corrects the prior plan's implied reasoning**: "brokers are expensive on Windows" is true of RabbitMQ and Kafka and false of NATS |
| **Kafka** | **No.** Kafka on Windows lacks POSIX semantics it relies on; the standing community and vendor guidance is WSL2 or Docker **[DOC]**, and both are excluded | — | — | — | JVM + broker + topic admin | **No** — failed the *no-Docker/no-WSL* constraint outright |
| **Redpanda** | **No.** No native Windows build; Linux/Docker only **[THIN — inferred from the absence of Windows artefacts, not from a vendor statement]** | — | — | — | — | **No** — failed *Windows* |

### Python task frameworks

| Candidate | Installs on Windows, no Docker? | Multi-tenant story | Async | Scheduler | Operational cost | Verdict |
|---|---|---|---|---|---|---|
| **Celery 5.6.3** | Resolves (+9 pkgs) **[MEASURED]**, but the project's own current FAQ still says: *"No. Since Celery 4.x, Windows is no longer supported due to lack of resources. But it may still work and we are happy to accept patches."* **[DOC]** | None | Still second-class | Beat, single-instance | Broker + worker + beat, three processes | **No** — failed *Windows*, by the maintainers' own words, in 2026. The reputation has not improved because the support has not |
| **Dramatiq 2.2.0** | Yes. Windows is supported as of 2.x **[DOC]**, `watch` extra excepted; +1 pkg **[MEASURED]** | None | Only via an `AsyncIO` middleware that runs coroutines on a **separate event-loop thread** (`dramatiq/middleware/asyncio.py:25`) **[SOURCE]** — not native | No (needs APScheduler or cron) | Needs RabbitMQ or Redis | **No** — failed *tenancy* and *async*. The async story is an adapter, and this stack is async end to end |
| **Huey 3.3.4** | Yes, +1 pkg **[MEASURED]** | None | Partial (`huey/contrib/asyncio.py`) | Yes, built-in periodic tasks | Redis (or SQLite via `sql_huey`) | **No** — failed *tenancy*. Also the smallest feature set here: no dead-letter, no lease |
| **SAQ 0.26.4** | Yes, +2 pkgs **[MEASURED]**, and it has a **Postgres backend** — this was worth a serious look | **None, and worse than the others**: the job is a `BYTEA` blob (`saq/queue/postgres_migrations.py:17`) **[SOURCE]**, so the queue is not queryable at all, let alone tenant-filterable | Native asyncio — genuinely good | Cron jobs, yes | Uses **psycopg**, a second Postgres driver alongside asyncpg; coordinates with `pg_try_advisory_lock` (`saq/queue/postgres.py:155,365`) **[SOURCE]** | **No** — failed *tenancy* and *observability*. An opaque blob defeats A2, A3 and the whole "one place to look" argument |
| **TaskIQ 0.12.4** | Yes, +3 pkgs **[MEASURED]**. Broker ecosystem includes `taskiq-redis`, `taskiq-nats`, `taskiq-aio-pika`, **`taskiq-pg`** (asyncpg) **[MEASURED — all resolve on PyPI]** | None | **Async-native by design** — the best async pedigree in this row | Yes, `TaskiqScheduler` | Broker of your choice; FastAPI integration exists | **No** — failed *tenancy*. Closest in spirit to what we want and still cannot answer "which tenant owns this job" to a Postgres policy. `taskiq-pg` last released 2025-03 **[MEASURED]** |

### Postgres-native

| Candidate | Installs on Windows, no Docker? | Multi-tenant story | Async | Scheduler | Operational cost | Verdict |
|---|---|---|---|---|---|---|
| **procrastinate 3.9.0** | Yes, +3 pkgs **[MEASURED]** | **None. Verified independently of the prior claim**: `grep -ci tenant procrastinate/sql/schema.sql` → **0**, over 4 tables, with **39** migration files shipped **[MEASURED]** | Native | Periodic tasks | 39 migrations into a repo that deliberately has no Alembic (`backend/pyproject.toml:36`) **[SOURCE]** | **No** — the prior finding is **confirmed by re-measurement**. Best-designed loser in the survey |
| **pgqueuer 1.3.2** | Yes, +3 pkgs **[MEASURED]**. asyncpg-native, actively released (2026-07-27) **[MEASURED]** | **None** — `grep -ric tenant pgqueuer/` → **0** **[MEASURED]** | Native | Yes — a `schedules` table with crontab expressions **[SOURCE]** | 4 tables, 3 enum types, a trigger firing `pg_notify` on *every* insert/update/delete **[SOURCE]** | **No** — failed *tenancy*. **The prior research never evaluated it, and it is the closest existing thing to what Phase 3 proposes** — which is strong external validation of the design. See §6 |
| **pgmq** | **Yes — and the prior plan's stated reason for rejecting it is wrong.** pgmq ships a **SQL-only install**: `psql -f pgmq-extension/sql/pgmq.sql`, no compiler, no `CREATE EXTENSION` **[DOC]** | None, and structurally hostile: a **table per queue**, so "a queue per tenant" means unbounded dynamically-created tables that `_TENANT_SCOPED_TABLES` (a static tuple) can never enumerate | Client-side | No | The SQL-only path is *unversioned and fresh-install only* — no upgrade path **[DOC]** | **No** — but **for the right reason now**: tenancy plus an unversioned schema, not "it needs a compiler." See §3 |
| **pg-boss** | **No Python client exists.** `pypi.org/pypi/pgboss` and `/pg-boss` both return **404** **[MEASURED]** | — | — | — | — | **No** — failed *language*. Node-only |
| **River** | The Python package `riverqueue` 0.7.0 describes itself as an **"insert-only client for River"** **[MEASURED]** — you can enqueue from Python; workers must be Go | — | — | — | A Go binary to write and supervise | **No** — failed *language*. Insert-only is not a job substrate |
| **Chancy 0.25.1 / Hyrex 0.10.23** | Yes | None | Yes | Yes | Small | **No** — failed *tenancy*, and both are last-released 2025 (2025-10 / 2025-12) **[MEASURED]**, i.e. ~9 months stale. Named so nobody thinks they were missed |

### Durable execution / workflow

| Candidate | Installs on Windows, no Docker? | Multi-tenant story | Async | Scheduler | Operational cost | Verdict |
|---|---|---|---|---|---|---|
| **Temporal** | **Yes, and cheaply — measured, not assumed.** `temporal_cli_1.8.2_windows_amd64.zip`, **42.2 MB**, no installer, no runtime deps **[MEASURED via the GitHub releases API]**. SDK: `temporalio-1.31.0-cp310-abi3-win_amd64.whl` exists, **+3 packages** against Aegis's full graph with **zero version conflicts** **[MEASURED]** | **Namespaces**, and they work on the dev server (`temporal operator namespace create --namespace tenant-a` succeeded) **[MEASURED]**. But: workflow state lives in Temporal's own store, **not in a Postgres table `rls.py` can see or a console can join to `budgets`** | Native, first-class | Yes — Temporal Schedules | Dev server measured at **123 MB RSS at start, 155 MB after a run**, ready in **0.2 s** **[MEASURED]**. Production needs the separate server binary + a real datastore + schema tooling **[DOC]** | **No, for this deadline** — but it is the only candidate that loses on *architecture fit* rather than on cost, and it is the one to revisit. Full argument in §4 |
| **DBOS 2.29.0** | Yes, **+2 packages** (`dbos`, `psycopg-binary`) **[MEASURED]** — durable execution as a *library*, no server at all | **None** — `grep -ri tenant dbos/` → **0** over its whole system schema **[MEASURED]** | Native async workflows and steps (`dbos/_core.py:1237 start_workflow_async`) **[SOURCE]** | Yes, `@DBOS.scheduled` cron | Its own `dbos` schema with **47+ internal migrations** (`dbos/_migration.py`) **[SOURCE]**, and it drags **psycopg** in as a second Postgres driver beside asyncpg | **No** — failed *tenancy*. **The strongest candidate nobody in this project had named**: it is Temporal's guarantees with none of Temporal's server. Recovery is executor-scoped at startup (`dbos/_recovery.py:36`) **[SOURCE]**, so cross-worker recovery needs coordination we would supply anyway. Its queue design is the single richest steal-list in §6 |
| **Prefect 3.8.3** | Yes — native Windows, no Docker, SQLite by default, Postgres for production, **NSSM recommended**, port 4200, antivirus exclusions advised **[DOC]** | **None in OSS.** Workspaces are a Cloud feature | Yes | Yes, strong | **+40 packages** **[MEASURED]**, including `docker`, `pydocket`, `burner-redis`, `griffe`; forces `packaging` 26.3→26.2 **[MEASURED]**. Plus a second server, a second UI, a second database schema | **No** — failed *over-complexity* and *tenancy*. It duplicates the entire A2/A3 surface Phase 3 is building, with no tenant model to hang it on |
| **Dagster 1.13.18** | Resolves (+18 pkgs) but forces **protobuf 7.35→6.33** **[MEASURED]**, and conda-forge lists win-64 as unsupported since 1.0.2 **[THIN]** | None | Partial | Yes | A data-asset orchestrator, not a job queue | **No** — wrong tool. Aegis has jobs, not materialised data assets |
| **Windmill** | **No.** The v1.791.0 release ships `windmill-amd64` (Linux) and `windmill-ee.exe` — **the Windows binary is enterprise-only** **[MEASURED via the GitHub releases API]** | Workspaces | — | Yes | Rust server + Postgres | **No** — failed *Windows* for the OSS build |
| **Hatchet** | **No.** Self-hosting is Docker Compose (Postgres + RabbitMQ, or Postgres-as-broker); "Hatchet Lite" is *a single Docker image* **[DOC]**. No Windows binary | Tenants are a **first-class concept** — the only candidate in the survey that has one | Yes | Yes | Docker | **No** — failed *no-Docker*. Painful, because its tenancy model is the one we want |
| **Restate 1.7.3** | **No.** Release assets cover `aarch64/x86_64-apple-darwin` and `*-unknown-linux-musl` only — **no Windows artefact of any kind** **[MEASURED via the GitHub releases API]** | Virtual objects | Yes (`restate-sdk` 1.0.4, released 2026-08-14) | Yes | Single binary | **No** — failed *Windows*. Included because it is the newest credible Temporal competitor and someone will ask |

### Async-native / scheduler-only

| Candidate | Verdict |
|---|---|
| **arq 0.28.0** | **No** — failed *tenancy* + *split-brain*. Resolves (+2) **[MEASURED]**; the prior finding stands unchanged |
| **APScheduler** | **No** — and the plan's claim **verified**: PyPI's latest stable is **3.11.3**; the entire 4.0 line is `4.0.0a1`…`4.0.0a6`, still alpha as of 2026-08-18 **[MEASURED]**. 3.x jobstores are not multi-scheduler safe |
| **`pg_cron`** | **No** — needs `shared_preload_libraries` and a Windows build, and it schedules SQL, not Python. Prior finding stands |
| **Windows Task Scheduler** | **No** — a second place where work is defined, and it swallows errors. Prior finding stands |

---

## 2. The one criterion that eliminated 22 of 24

Every framework above except Hatchet (Docker-only) and Temporal (namespaces, outside Postgres)
failed the same test, and it is worth stating precisely because it is *not* the test people
usually apply.

`aegis/src/aegis/governance/rls.py` registers 13 tenant-scoped tables and runs a catalog
read-back that reports any tenant-scoped table lacking a policy. The trap is in `_plan_rls`
(`rls.py:327`) **[SOURCE]**: a table is only *considered* tenant-scoped if it **has a tenant
column**. Read the loop —

```python
if not table.is_tenant_scoped:
    if name in wanted:
        stale.append(name)
    continue
```

— a table with no `tenant_id` is `continue`d before any gap can be recorded. So adopting any of
these libraries does not merely leave their tables unprotected; it leaves them unprotected **and
invisible to the one diagnostic built to notice**. procrastinate would add 4 such tables,
pgqueuer 4, DBOS 5+, SAQ 2. The health page would read green over ungoverned tenant work.

That is the same defect class as "RLS policies inert because the app ran as a superuser" and "a
budget test green while asserting the reverse of reality" — the two most expensive bugs this
project has found. It is the deciding constraint, and no library on the market satisfies it,
because no library ships a column for *your* tenancy model.

**Could we add the column?** Only by forking the library's schema, at which point its migration
tree and ours diverge permanently and we own the fork without owning the code. Rejected.

---

## 3. Three corrections to `plans/04` §1.1

The instruction was to check the prior work honestly, so:

**Correction 1 — the pgmq rejection reason is factually wrong.** §1.1 says pgmq *"is a C
extension — `CREATE EXTENSION` plus a build on native Windows. That is the container-shaped
dependency the environment constraints exclude."* pgmq's own `INSTALLATION.md` documents a
**SQL-only installation**: run one `pgmq.sql` file, which creates a `pgmq` schema with all
objects and no compiled artefact **[DOC]**. No compiler, no `shared_preload_libraries`, no
Windows build. The conclusion (reject) is right; the reason is not. The correct reasons are (a)
no `tenant_id`, (b) a table per queue, which a static `_TENANT_SCOPED_TABLES` tuple cannot
enumerate, and (c) the SQL-only path is explicitly *unversioned and fresh-install only*, with no
`ALTER EXTENSION` upgrade path — in a repo that already has no migration tool, that is a
one-way door.

**Correction 2 — "Memurai as broker" was dismissed too fast, and then rescued by a worse
problem.** Memurai 4 is Redis **7.4.9**-compatible and does support streams **[DOC]**, so
"Memurai can't do consumer groups" would have been an invalid objection. But the FAQ contains
something more serious for this project: the **Developer edition is licensed for non-production
use and has a maximum uptime of 10 days, after which it shuts down and must be restarted**
**[DOC]**. That is a hard finding about a component **Aegis already depends on** for the memory
semantic cache — it is a scheduled outage with a 10-day fuse, and it is independent of this
survey's question. Flagged here because I found it here; it belongs in the risk register
whatever Phase 3 decides.

**Correction 3 — "no new infrastructure on Windows" is not the argument it looks like.** The
prior plan leans on the difficulty of installing a broker. Measured, that premise only holds for
RabbitMQ (Erlang, admin rights, cookie file, ASCII paths) and Kafka (WSL2/Docker). It is false
for NATS (one `.exe` with built-in Windows-service support) and false for Temporal (one 42 MB
zip, zero dependencies, 123 MB RSS, ready in 0.2 s). Continuing to lead with "installation cost"
would be exactly the over-conservative error `01-V2-ADDITIONS.md` warns about — the same shape
as "the VLM pipeline is impossible at 16 GB", which measurement disproved. **The plan should
delete that argument and lead with tenancy.** The conclusion survives; the reasoning needs
rewriting.

---

## 4. The Temporal question

Ingestion — parse → chunk → enrich → embed → index → graph, resumable at the stage, minutes
long, expensive to redo — is the canonical durable-execution workload. Temporal is the canonical
answer. It deserved a real test, not a paragraph, so I ran one.

### What I actually did **[MEASURED]**

Downloaded Temporal CLI v1.8.2, started `temporal server start-dev --db-filename dev.db`, wrote
an 8-line workflow with five activities named after our real stages, started it, **hard-killed
the worker process mid-run**, then started a fresh worker in a new process:

```
parse ran pid=5516      chunk ran pid=5516      embed ran pid=5516      index ran pid=5516
### worker HARD-STOPPED
index ran pid=5523      graph ran pid=5523
result: parse,chunk,embed,index,graph
```

`parse`, `chunk` and `embed` were **not re-run**. Only `index` — the activity in flight when the
process died — replayed, and then the workflow completed in the new process. That is exactly the
Phase 3 definition-of-done item *"a killed worker's job is reclaimed by the reaper and retried —
tested by actually killing a worker mid-job"*, plus the Phase 4 requirement *"a failure at the
graph stage must not re-parse 200 pages"*, achieved with **zero lines of substrate code**.

Costs measured in the same session:

| Thing | Measured |
|---|---|
| Windows install | one 42.2 MB zip, no installer, no dependencies |
| Dev server startup | **0.2 s** to accepting connections |
| Dev server RSS | **123 MB** at start, **155 MB** after the run (frontend + history + matching + worker + Web UI, one process) |
| Python SDK footprint | `import temporalio` → **52 MB** RSS |
| Dependency impact | **+3 packages** (`temporalio`, `nexus-rpc`, `types-protobuf`), **no version conflicts** against Aegis's 243-package Windows graph |
| Namespaces | `temporal operator namespace create --namespace tenant-a` → registered, listed |

I also hit the classic Python-SDK trap, and it cost me a real iteration: the **workflow sandbox
re-imports the module that defines the workflow**, so a module with `asyncio.run()` at import
time fails validation with `RuntimeError: Failed validating workflow`. Workflow definitions must
live in their own import-safe module. That is a genuine ergonomic tax on a codebase whose
modules do side-effectful things at import.

### The case *for* adopting it

- It solves the hardest thing in Phase 4 for free, correctly, today. Stage resumability is not
  an optimisation here — a 200-page Docling parse is ~3.7 minutes of CPU.
- Retries, timeouts, heartbeats, backoff, cancellation, signals, child workflows, `continue-as-new`
  and Schedules are all built and battle-tested. Phase 3 budgets **3.25 days** to hand-build a
  strictly weaker subset.
- The Web UI is a working replay/inspection surface on day one — which is a meaningful chunk of
  A2 and A3, and a jury-visible artefact.
- The install cost is genuinely trivial, and I verified that rather than assuming it.
- One engineer *can* operate `temporal server start-dev`. It is one process.

### The case *against*, here

1. **The tenancy constraint, which is the whole reason Phase 1 existed.** Workflow state lives
   in Temporal's store. There is no `tenant_id` column for `rls.py` to protect, no row for the
   console to join against `budgets` or `usage_ledger`, and no way for the admin DB page (A1) to
   show a tenant their jobs. Namespace-per-tenant gives real isolation — Temporal's own guidance
   calls it manageable below ~50 tenants **[DOC]** — but it moves tenancy into a *second*
   authority that must be provisioned, credentialed and kept in sync with `users.tenant_id`. The
   project's standing rule is one mechanism used well.
2. **A job and its result can no longer commit together.** Today's substrate proposal writes the
   job row and the data it produced in one Postgres transaction. With Temporal, workflow progress
   commits to Temporal and the data commits to Postgres — two-phase reality, reconciled by
   idempotent activities. That is a correct pattern and it is *more* work than the thing it
   replaces, not less.
3. **It does not remove the `jobs` table; it adds to it.** Admission control (per-tenant queued
   cap → 429), budget pre-authorisation, cooperative cancellation tied to a user closing a tab,
   and the tenant-visible live log all still need tenant-scoped Postgres rows. Realistically we
   would run Temporal **and** a jobs/runs table, then spend Phase 6 reconciling two sources of
   truth in the console. Two substrates is the over-complexity failure, in its textbook form.
4. **`start-dev` is documented as not for production** **[DOC]**. A real deployment means the
   separate server binary, a Cassandra/MySQL/Postgres datastore, and `temporal-sql-tool` schema
   setup. For a hackathon that is fine — but the demo would then be running the thing its own
   docs say not to run, and the "enterprise scale" claim (A6) would be a step *weaker*, not
   stronger, than a Postgres table.
5. **Architecture risk I could not close: `temporalio` publishes `win_amd64` only — there is no
   `win_arm64` wheel** **[MEASURED]**. On a Snapdragon/ARM Windows laptop the SDK would need a
   Rust toolchain to build from source. See the open question in §8.
6. **Determinism is a real constraint on this specific codebase.** Workflow code is replayed;
   it may not do I/O, may not use wall-clock time, and its defining module is re-imported in a
   sandbox. Aegis's agent code is LangGraph-heavy with import-time side effects. Everything real
   becomes an activity, which is fine — but the discipline is new, it is unforgiving, and the
   failure mode (non-determinism errors on replay) appears *after* a restart, which is the worst
   time to meet it.

### The decision

**No Temporal for this build.** Not because it is heavy — it isn't; measurably it is one of the
lightest things in this stack — but because it puts the tenant story outside Postgres, and
tenancy is the axis this entire platform is judged on. Adopting it would trade a constraint we
have already satisfied (RLS-enforced, mutation-tested isolation) for a capability we can
approximate in a day (stage resumability), and it would leave two job systems in the same
product.

**And the part that is not politeness:** if Aegis were single-tenant, or if tenants were
namespaces rather than rows, I would recommend Temporal over Phase 3 without hesitation, and I
would say so in this sentence. The measurement above is not a case against Temporal. It is a
case against Temporal *and* thirteen RLS tables *and* one engineer *and* one deadline.

---

## 5. The honest steel-man against building it

What we lose, specifically, on day one. None of these is speculative; each is something I
watched a surveyed framework do.

| We lose | Who has it | What it costs us |
|---|---|---|
| **A working job UI on day one** | Temporal Web (bundled in the 42 MB zip), Prefect, Dagster, pgqueuer's dashboard | Phase 6 must build a console screen before anyone can see a stuck job. Until then, debugging is `psql`. This is the single largest real loss |
| **Stage-level resumability as a primitive** | Temporal, DBOS, Restate | Phase 3 says *"stage progress is on the row"* but **specifies no mechanism** — no stage column, no idempotent-stage contract, no dispatch loop. This is the biggest actual gap in the phase file (§6, steal #1) |
| **Chaining, groups, DAGs** | Dramatiq (`composition.py`), Celery canvas, Temporal child workflows | Ingestion is a chain today, so we can defer this — but "embed 10 documents, then rebuild the graph once" is a fan-in we will want in Phase 4 and will hand-roll badly under time pressure |
| **Rate limiting as a queue property** | DBOS (`rate_limited`, global vs per-worker concurrency), Dramatiq (`dramatiq/rate_limits/`) | Phase 3's *"model-call concurrency: five users × four agents is twenty concurrent gateway calls"* is exactly this, and the phase gives it one bullet with no design |
| **Battle-tested edge cases** | All of them | Clock skew, `LISTEN/NOTIFY` drops on reconnect, poison payloads that kill the worker before `attempts` is committed, a lease renewed by a process that is swapping, duplicate `NOTIFY` storms. Every framework here has closed bugs we have not met yet |
| **Community knowledge** | Celery/Temporal especially | Nobody has ever Googled our substrate's error message |
| **Free maintenance** | All | Every bug is ours, forever |

**The counter-weight, stated fairly.** All seven of those are recoverable in a codebase we
control, and none of them is the thing the jury or the tenant model tests. The one thing that is
*not* recoverable is a queue whose rows carry no tenant — that requires a fork. Build-vs-buy
here is not "convenience vs correctness"; it is "convenience vs the one property the product is
about."

---

## 6. If we build it — what Phase 3 should steal

This is the part that answers *"is our phase 3 best sota?"* honestly: **the decision is SOTA;
the specification is not yet.** `SKIP LOCKED` + lease + reaper + jittered backoff + dead-letter
+ idempotency is precisely the shape pgqueuer, procrastinate, Solid Queue, River, GoodJob and
Oban converged on — external validation, not invention. But every one of them ships things Phase
3 does not name. Six, in priority order.

**1. A declared stage machine — the biggest gap, and the one Temporal exposed.**
Phase 3 §"What this means for how it is built" says *"stage-level progress on the job row"* and
then never defines it. Steal Temporal's and DBOS's shape without their runtime: a job kind
declares an ordered list of stages; each stage is an **idempotent** async function; the worker
loop persists `stage` + `stage_state` after each one and resumes at the recorded stage on a
retry. Two columns and a dispatch loop, ~50 lines. Note what my Temporal run proved: **the
in-flight stage re-ran** after the kill. Stages must be idempotent whichever engine runs them —
that requirement is not Temporal's, it is physics. Writing it down now is what makes a later
Temporal migration a driver swap rather than a rewrite.

**2. Global concurrency *and* per-worker concurrency, as separate numbers.**
DBOS's queue takes both `concurrency` and `worker_concurrency`, and validates
`worker_concurrency <= concurrency` (`dbos/_queue.py:71-129`) **[SOURCE]**. That is exactly
Phase 3's stated requirement — *"Docling parses must serialise while embed calls should not"* —
and Phase 3 words it as a single "concurrency limit per job type", which is the weaker thing.
Steal both numbers, and steal the validation.

**3. Separate `dedupe_key` from `idempotency_key`.**
pgqueuer carries `dedupe_key` with a partial unique index scoped to live rows only:

```sql
CREATE UNIQUE INDEX ... ON queue (dedupe_key)
  WHERE status IN ('queued','picked') AND dedupe_key IS NOT NULL
```
**[SOURCE]** — the same "live-only" trick Phase 3's `jobs_idempotency_live_idx` already uses,
which is good. What Phase 3 lacks is DBOS's **debounce** (`debounce_deadline_epoch_ms`,
`is_debounced`) **[SOURCE]**: collapse a burst of "re-index this tenant" requests into one
firing after quiet. Yash's own re-indexing requirement is a debounce, not a schedule. Cheap:
one nullable timestamp.

**4. A fencing token on the claim.**
DBOS stamps `owner_xid` on the row at claim (`dbos/_core.py:566`) **[SOURCE]**. Phase 3 has
`worker_id` and `lease_until`, which detects *when* a lease expired but not *who* is allowed to
write the result. Without a fence, a stalled worker that wakes after the reaper reassigned its
job can still write `result` over the new run's. Add `lease_epoch` (bump on every claim and
every heartbeat) and make every completion `UPDATE ... WHERE lease_epoch = :mine`. One integer
column; it closes a real lost-update window that no test which does not kill a process will find.

**5. A `job_log` table, unlogged, for history — and a worker registry.**
pgqueuer keeps completed-job history in a separate **`UNLOGGED`** table (`queue_table_log`) with
status, priority, entrypoint and a JSONB traceback **[SOURCE]**, so the hot claim index never
grows with history. Phase 3 keeps everything in `jobs` and relies on the partial index staying
small — which works, but the table still bloats and vacuum still walks it. `UNLOGGED` for
history that is regenerable evidence, not a system of record, is free throughput.
Separately: procrastinate and pgqueuer both maintain a **worker/heartbeat table** with a stalled-
worker pruner. Phase 3's health page (A2) needs exactly that to answer *"is a worker alive?"*
from outside the process, and §3.4 currently has no answer.

**6. LISTEN/NOTIFY discipline, learned from pgqueuer's mistake.**
pgqueuer installs an `AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE` trigger that `pg_notify`s on
**every** row change **[SOURCE]**. Do not copy that — it taxes every write, including the
reaper's. Phase 3's explicit-NOTIFY-plus-polling-floor is the better design. Keep it, and keep
the polling floor, because a dropped notification on a reconnect is the failure everyone meets
eventually.

Two things Phase 3 already has that the survey confirms are right, worth defending: the
**single-statement claim** (procrastinate, pgqueuer and River all do it in one statement; the
`consolidate.py` SELECT-then-UPDATE shape is the outlier), and **`status` as `text` + CHECK
rather than a native enum** — pgqueuer's own upgrade path is a stack of `ALTER TYPE ... ADD VALUE
IF NOT EXISTS` calls **[SOURCE]**, which is precisely the pain `backend/src/app/data/models.py:107`
warns about.

---

## 7. Recommendation, and what would reverse it

**Build the Postgres substrate as Phase 3 specifies, amended by the six steals in §6.** Rewrite
§1.1's justification: lead with *"one tenant-scoped, RLS-governed table that the health page, the
console, the audit trail and the budget ledger can all query and all join"*, and drop the
installation-cost argument, which measurement does not support.

**Add to Phase 3 explicitly** (these are gaps the survey found, not preferences):

1. The stage machine (steal #1) — it is Phase 4's hardest requirement and currently unspecified.
2. `concurrency` + `worker_concurrency` as separate declared numbers per job kind (steal #2).
3. `lease_epoch` fencing on the claim and on every completion write (steal #4).
4. A worker-registry/heartbeat table, because A2 cannot answer "is a worker alive" without one
   (steal #5).
5. Record the Memurai 10-day-uptime finding in the risk register — it is not this decision's
   problem, but it is somebody's.

**The triggers that reverse this, stated so nobody has to re-litigate it:**

| Trigger | Then |
|---|---|
| Ingestion grows past ~6 stages, or acquires fan-out/fan-in with compensation | Adopt **Temporal** for ingestion only. The stage machine in steal #1 is deliberately the portable subset, so this becomes a driver swap |
| The tenancy model moves from `tenant_id` rows to per-tenant deployments or schemas | The single constraint that eliminated 22 of 24 candidates evaporates. Re-run this survey; **Temporal wins it** |
| Sustained thousands of jobs/second, fan-out to non-Python workers, or cross-service queues | A broker's semantics are needed. **NATS JetStream**, not RabbitMQ — one `.exe`, native Windows service |
| We need a job UI before Phase 6 ships one | Temporal Web is 42 MB and free. That is a real, cheap fallback, and it is the loss in §5 that hurts most |
| A second engineer joins and owns the substrate | Re-evaluate. "One engineer" is load-bearing in this recommendation |

---

## 8. Open questions — where I cannot decide without you

**1. Is the hackathon laptop Windows x64 or Windows on ARM?** It changes two rows of the verdict
table: `temporalio` ships **no `win_arm64` wheel** **[MEASURED]**, so on ARM the Temporal option
needs a Rust toolchain, and several other wheels would want checking.
**My default: assume x64.** It does not change the recommendation, only the size of Temporal's
fallback option.

**2. Memurai Developer vs Enterprise.** The 10-day uptime limit and non-production licence are
documented **[DOC]**; I do not know which edition is installed. **My default: assume Developer**,
and treat "Memurai restarts every 10 days" as a fact the cache design must survive — which it
does today, because `aegis.memory.cache` has a labelled in-memory fallback. It would **not**
survive being a job broker, which is one more reason the Redis-Streams row is a No.

**3. Nothing else.** Every other decision above I am willing to own without asking.

---

## Appendix A — how to reproduce

Throwaway venv, isolated from `backend/.venv` and `aegis/.venv`:

```bash
uv venv --python 3.11 /tmp/jobsurvey/.venv
uv pip install --python /tmp/jobsurvey/.venv/bin/python \
    procrastinate pgqueuer saq dbos temporalio dramatiq taskiq huey arq celery
```

Windows resolution against Aegis's real graph (the baseline is 243 packages):

```bash
cd backend
uv pip compile pyproject.toml --all-extras \
   --python-version 3.11 --python-platform x86_64-pc-windows-msvc -o base.txt
echo temporalio > x.in
uv pip compile pyproject.toml x.in --all-extras \
   --python-version 3.11 --python-platform x86_64-pc-windows-msvc -o with.txt
```

Windows wheel existence:

```bash
pip download --no-deps --only-binary=:all: --platform win_amd64 \
    --python-version 311 --implementation cp -d dl temporalio
# -> temporalio-1.31.0-cp310-abi3-win_amd64.whl (15.5 MB). No win_arm64 equivalent exists.
```

Tenancy greps that decided the survey:

```bash
grep -ci tenant  .venv/lib/python3.11/site-packages/procrastinate/sql/schema.sql   # 0
grep -ric tenant .venv/lib/python3.11/site-packages/pgqueuer/                      # 0
grep -ri  tenant .venv/lib/python3.11/site-packages/dbos/     | wc -l              # 0
grep -ri  tenant .venv/lib/python3.11/site-packages/saq/      | wc -l              # 0
```

The Temporal kill test: `temporal server start-dev --db-filename dev.db`, then a workflow of
five activities in an import-safe module, started with `client.start_workflow`, run under a
worker stopped hard with `asyncio.wait_for(worker.run(), timeout=3.2)`, then re-run in a fresh
process. Scripts are at `scratchpad/jobsurvey/{wfdef,run}.py`.

## Appendix B — sources

- Celery FAQ, Windows support — https://docs.celeryq.dev/en/stable/faq.html
- Dramatiq installation and changelog — https://dramatiq.io/installation.html · https://dramatiq.io/changelog.html
- RabbitMQ install on Windows — https://www.rabbitmq.com/docs/install-windows
- NATS Windows service and JetStream — https://docs.nats.io/running-a-nats-service/introduction/windows_srv · https://docs.nats.io/jetstream
- Kafka on Windows guidance — https://docs.conduktor.io/learn/getting-started/install-windows-kraft · https://issues.apache.org/jira/browse/KAFKA-14273
- Memurai compatibility and editions — https://www.memurai.com/faq · https://www.memurai.com/blog/meet-memurai-4-a-new-benchmark-for-redis-compatible-windows-native-in-memory-data
- pgmq installation (SQL-only path) — https://github.com/pgmq/pgmq/blob/main/INSTALLATION.md
- Temporal dev server — https://docs.temporal.io/develop/run-a-development-server · releases https://github.com/temporalio/cli/releases
- Temporal multi-tenant patterns — https://docs.temporal.io/production-deployment/multi-tenant-patterns · https://docs.temporal.io/best-practices/managing-namespace
- Prefect on Windows — https://docs.prefect.io/v3/how-to-guides/self-hosted/server-windows
- Hatchet self-hosting — https://docs.hatchet.run/self-hosting · https://docs.hatchet.run/self-hosting/hatchet-lite
- Windmill releases — https://github.com/windmill-labs/windmill/releases
- Restate releases — https://github.com/restatedev/restate/releases
- River Python client — https://pypi.org/project/riverqueue/
