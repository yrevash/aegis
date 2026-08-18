# Aegis v2 — the integrated roadmap

**Written 2026-08-18.** Phases 1 and 2 are done. This file is what phases 3–9 are, in
dependency order, with the findings from all six research plans folded in.

Supersedes the phase ordering in [`00-MASTER-PLAN.md`](00-MASTER-PLAN.md). The old
`phase-03..06` files stay as reference for their own sections; where they conflict with this
file, this file wins.

**Timing is not a constraint.** Sequence is by dependency: each phase makes the next one
possible.

---

## The table

| # | Phase | What it delivers | Why it is here |
|---|---|---|---|
| **1** | Tenant isolation | ✅ **DONE** `d1822c0` | — |
| **2** | ML out of graph, fiction deleted | ✅ **DONE** `7e21890` | — |
| **3** | **Platform spine** | Job substrate · durable `run_events` · settings catalogue · two-tenant seed · `fine_role` on the wire · client console · `py.typed` + doc fixes | Everything below needs at least one of these. Nothing per-tenant has ever run with a real tenant. |
| **4** | **Ingestion** | Docling → structure → enriched chunks · `chunks.tenant_id` · corpus-wide BM25 · local reranker · gold set + ablation | Needs jobs (P3). Ingestion is the capability that does not exist and 30 Aug requires. |
| **5** | **Multi-agent** | Depth classifier · real concurrent sub-agents · `agent_id` on every event · Tavily · `TOOL_RESULT` rail | Needs `run_events` (P3) for per-agent logs. |
| **6** | **Console** | Chat sessions · composer (mode · model · tools) · result tabs · live agent panel · budget pill · memory panel | Needs P4 and P5 to have something real to show. |
| **7** | **Control planes** | Per-role dashboards · admin DB page · pipeline health · per-tenant guardrails/LLMOps/memory · reports · red-team | Needs the settings catalogue (P3) and the console shell (P6). |
| **8** | **Modularity** | `Aegis` runtime object · `DomainAdapter` · conformance suite · `/v1` · generated clients · `AGENTS.md` | Best done after the surface stops moving. This is the thesis of the product. |
| **9** | **Scale + hardening** | RLS fail-closed · budget on background jobs · vector-store mode seam · connection pools · admission control | Depends on everything above existing to harden. |

---

# Phase 3 — Platform spine

**Nothing above this line works properly without it.** Six pieces, one theme: the platform
currently has no substrate for durable work, no per-tenant configuration, and no real tenants.

### 3.1 · The job substrate

One `jobs` table on Postgres. Claim with a **single statement**:

```sql
UPDATE jobs SET status='running', lease_until=now()+interval '5 min', attempts=attempts+1
FROM (SELECT id FROM jobs
      WHERE status='pending' AND run_after<=now()
      ORDER BY priority DESC, run_after
      FOR UPDATE SKIP LOCKED LIMIT $1) c
WHERE jobs.id=c.id RETURNING *;
```

Measured: **20,877 claims/s** single, **128,085/s** batched, zero duplicates. The workload is
tens of LLM-bound jobs per minute — four orders of magnitude of headroom.

**Not** `consolidate.py`'s shape (SELECT-then-UPDATE: N+1 round trips, N−1 losers per batch).
**Not** Celery/RQ/arq/procrastinate — procrastinate's schema has **zero occurrences of
"tenant"**, so `rls.py`'s catalog read-back would not even report four tables of tenant work as
ungoverned. Absent that, procrastinate would be the right answer.

**SKIP LOCKED is the claim, not the substrate.** It stops two *live* workers taking a row; it
does nothing about a worker that *dies* holding one. Required with it:

| Piece | Why |
|---|---|
| **Lease + reaper** | A killed worker must not strand a row. This is a live bug today — `memory_consolidation_job` has no lease, and `attempts` is incremented and **read nowhere**. |
| Retry with jittered backoff | Thundering herd on a recovering dependency |
| Dead-letter **status**, not a table | Status, not location — one place to look |
| Idempotency keys | A re-enqueued job must not double-charge |
| Priority + per-tenant admission cap | Backpressure that is invisible is the same defect as a silent fallback: a visible 429 |
| Cooperative cancellation | A user closing a tab should stop the spend |
| `LISTEN/NOTIFY` + polling floor | Latency without a busy loop; the floor is what survives a dropped notify |

**Execution:** one worker implementation, two launch modes — in-process asyncio task (what runs
on demo day) and `python -m aegis.jobs.worker` standalone. Identical code path; the database is
the only coordination, so N workers in M processes is safe by construction with no leader
election. NSSM documented for the always-on case on Windows.

**Scheduler:** a `job_schedules` table plus a materialiser inside the worker loop, idempotency-
keyed on `sched:{id}:{fire_time}`. Not APScheduler — 4.0 is still pre-release and its own docs
say not to use it in production. Not `pg_cron` — needs `shared_preload_libraries` and an
`nmake` build on Windows, and schedules SQL not Python.

**The tenancy trap, non-negotiable:** claim runs **unscoped on the admin engine**; execution
runs with `set_tenant_scope` on the **serving engine**. A worker that claims with a tenant bound
sees an empty queue.

**Batching** where it helps and nowhere else: embedding calls (real API round-trip savings),
ingestion pages, consolidation. Not on the request path.

### 3.2 · `run_events` — the durable record

There are already three tracking mechanisms (OTel spans, Phoenix, the SSE stream). **Do not add
a fourth.** `run_events` as `plans/02` §2.2 specifies, plus `trace_id`/`span_id`, `job_id`, and
a `runs` header row.

Phoenix stays the ephemeral deep-dive. `run_events` is the durable, tenant-scoped, replayable
record — and it is the single primitive behind five later features: the harness, replay,
per-agent inspection, audit depth, and per-tenant LLMOps evidence. Five features collapse to
one table plus five queries.

**Partition by month at creation.** This is the one irreversible decision in the roadmap:
converting a large heap table later needs a migration, and there is deliberately no Alembic.

### 3.3 · The settings catalogue — the "0 code change" mechanism

One `settings` table (platform / tenant / user) plus a **`SettingSpec` catalogue** declaring
key, type, bounds, `writable_by`, `readable_by`, `merge`.

Aegis already proves this pattern in `_KNOB_SPECS` / `harness_config()`, with a bijection test
that makes a knob impossible to add without a UI control appearing. **Generalise that one; do
not invent a second.**

`merge: tighten_only` is what makes the tenant-safety rules *executable configuration* rather
than prose — the resolver cannot compute a weaker value than the platform default. That is the
mechanism behind "a tenant may add a guardrail but never weaken one".

### 3.4 · Two-tenant seed

Measured against live `taif`: `users`, `tenants`, `budgets`, `prompt_versions`, `usage_ledger`
all hold **zero rows**. Every login today is a `_DEMO_USERS` fallback. **Nothing per-tenant has
ever run with a tenant.**

Two tenants, an admin and users in each, budgets, documents. Without it, none of the isolation
built in Phase 1 has been exercised end to end, and every per-tenant screen below is untestable.

### 3.5 · `fine_role` on the wire, and the client console

Two one-line-ish fixes that unblock more rows than anything else in the roadmap.

`LoginResponse` never sends `fine_role`, so **the browser cannot tell platform admin from
tenant admin** — every per-tenant control depends on that distinction reaching the client.

`ROLE_SECTIONS.client` is `['dashboard','savings','forecast','risk','simulation']` — **no
console. The role the product exists for cannot ask a question.**

Ship with a **route-coverage test**: every non-public endpoint is reachable from some portal.
It would fail today on at least eight.

### 3.6 · `py.typed` and the four documentation lies

`aegis` has no `py.typed`, so every annotation is invisible to a type checker — including an
integrator's. One line.

The lies, verified: `aegis/README.md` tells an integrator to call **`aegis.require()`, which
does not exist** (it is `aegis.core.require`) — an `AttributeError` on line one. And the adapter
claims "piece 2 of 5", "3 of 5", "4 of 5", "**6 of 6**" in the same directory, when there are
**eight** modules and `roster.py` + `skills/` appear in no checklist.

Four wrong sentences that reproduce the Mumbai back-and-forth exactly.

---

# Phase 4 — Ingestion

Full decision record with reasoning in
[`phase-04-ingestion.md`](phase-04-ingestion.md). The spine:

**Docling standard pipeline** — not the VLM (255× slower at 281 s/page vs 1.10; it uses *less*
memory, so the earlier "impossible at 16 GB" was backwards). **TableFormer `ACCURATE`** — the
cost lands on the ingest clock, which is the cheap one, and a mis-parsed table is a wrong answer
with a confident citation that no reranking recovers.

**Heading hierarchy needs both settings.** Defaults give every heading at level 1; one setting
gives a *silent partial failure* that looks like it worked; both give a real 5-level tree for
+5.6% wall clock.

**`chunks.tenant_id` lands in the same change as the BM25 arm, or the arm does not land.**
Verified: `chunks` has no `tenant_id`, so a corpus-wide lexical query has no predicate to filter
on — re-opening the leak Phase 1 closed, through a path Phase 1 never covered.

**Corpus-wide BM25 on Postgres FTS** — `LightRAGBackend` implements `recall_ranked` but not
`keyword_recall`, so today BM25 re-scores the 20 candidates dense already found. It cannot
surface anything dense missed. BM25 alone (0.644 R@5) beats dense alone (0.587).

**Local ONNX cross-encoder (~250M), API fallback on a loud failure** — +12.1 pp recall@5. The
only local model in the roadmap. It is on the query clock, so its latency is measured, not
assumed.

**Deterministic chunk prefix** — title · type · date · heading path. Context@5 33.3% → 55.0%,
zero model calls.

**Not doing:** document expansion, hypothetical questions, per-chunk summaries, semantic or LLM
chunking. Each is a real technique; none beats connecting BM25 and turning on the reranker.
RAGSmith measured exhaustive optimisation of the whole space at **+3.8%**.

**Ingestion runs on the P3 job substrate** — durable, resumable, with a live log the tenant
watches.

**The eval:** gold truth anchored to a **verbatim answer span**, never a chunk id — chunk ids
are not comparable across arms, which is how most RAG ablations quietly become meaningless.
Metrics at our real k values (recall@20 pool ceiling, recall@6 what the generator reads; the gap
isolates the reranker). On the slide: **n=50 defends a ≥15-point delta and cannot defend a
5-point one.**

---

# Phase 5 — Multi-agent

**The classifier comes first, not the fan-out.** Deterministic-first, escalating to one CHEAP
call only on genuine ambiguity, **defaulting to SINGLE on every failure path**. A broken
classifier that quietly fans out is a broken classifier that quietly spends the balance.

**Concurrency is settled:** `asyncio.gather` inside one node. Probed and confirmed —
`get_stream_writer()` propagates through contextvars into gathered tasks in langgraph 1.2.11, so
concurrent workers emit live interleaved events. Do not reopen it.

**The constraint that falls out, and it is a feature:** no `interrupt()` inside a gathered task.
Sub-agents **propose** HIGH-risk actions; the main graph's single gate executes them. Four
agents, one human gate — multi-agent is *safer* than single-agent here, and that is worth saying
on stage.

`agent_id` on every event, enforced at the injected `stamp` seam. Per-agent timeout as a
**designed state**. Synthesis names contributing **and omitted** agents — "synthesised from 3 of
4" — so a partial failure reads as design, not a bug.

**Tavily** as the real search client. The key in `backend/.env` is spelled `TRAVILY_API_KEY` and
the package is not declared.

**`GuardStage.TOOL_RESULT` is required, not optional.** Tavily pulls arbitrary web content into
an agent's context which then feeds the synthesiser; today's rails cover user input and final
answer only.

**Budget:** ~$0.13–0.20 per 4-agent fan-out → **~650 fan-out queries total on $100**, covering
development, rehearsal and the day. Five concurrent users at 1 query/min burns the balance in
**~2 hours**.

---

# Phase 6 — Console

**Protocol repair first (0.25d, near-free):** `memory`, `reflection` and `routing` are emitted
by the backend today and **silently dropped** by the console. The events exist; they need
rendering.

**`session_id` is never sent** by `liveTransport.ts`, so `recall_memory` and `persist_memory`
both return `{}` on every live run — **the entire memory subsystem is dark in the live
product.** Same shape as the persona bug: built, and inert.

**The composer — three orthogonal axes**, matching what Grok, ChatGPT and Claude have all
converged on:

```
[ Mode: Auto ▾ ]  [ Model: Aegis default ▾ ]  [ Tools: 6 of 9 ▾ ]
◷ $2.14 of $50 today                              [ Send ]
```

Modes: **Auto · Fast · Deep · Team · Custom** — named for what they do to *this* system.
`Fast` and `Deep` both force SINGLE and differ in retrieval depth and model tier, because Aegis
already has both knobs. Depth, model and tools stay **three separate controls** — Claude
separates all three and Grok's mode dropdown contains no tool toggles; fusing them is a mistake.

`effective_depth = user_mode if user_mode != AUTO else classifier_decision`. **Manual wins, and
the `routing` event carries `decided_by` so the screen names who decided.**

**The budget rule worth fighting for:** auto-escalation is free, **manual escalation is charged
and pre-flighted**. It is the only thing between a Team button and a burned balance.

Plus: chat sessions in Postgres, result tabs (sources in the main tab, detail in its own), image
upload, the live agent panel, the memory panel showing **which memories were referenced**, and
the mascot — first to cut.

---

# Phase 7 — Control planes

The "0 code change" goal made concrete. Every control below is a `SettingSpec` entry from P3,
not a bespoke screen.

**The gap list, measured** — duties fully working per role:

| Role | Score |
|---|---|
| Client | **1 / 12** |
| Platform admin | 3 / 22 |
| Tenant admin | 4 / 18 |
| DevOps | 5 / 12 |
| AI team | 7 / 16 |

**The approvals inbox has no screen** — `ApprovalCard` is mounted only in `SimulationView` and
`MoneyShotConsole`. Moving approval ownership to the tenant admin (old Phase 6.2) relocates the
capability into nowhere until the screen exists.

**The admin DB page — build the hardened path first.** Five measured findings drive it:

- asyncpg `execute()` with no args **runs multiple statements silently**, and the return value
  does not say so. The extended protocol refuses them — so *how the query is sent* is a free
  control that replaces a regex.
- **`SET LOCAL ROLE` is not a boundary.** `RESET ROLE` is one legal statement; the probe went
  from `aegis_ro` back to superuser and read `pg_authid`. This kills the "run it on the app
  connection" design.
- `default_transaction_read_only` is **user-settable**. The privilege is the boundary; the
  setting is a guard rail.
- **Column-level grants work and `information_schema` respects them** — withhold
  `users.password_hash` and `SELECT *` fails *and the catalog stops listing the column*. The
  permission model becomes the schema browser's source of truth, with no denylist to drift.

Recommendation: hardened read-only path → schema browser + saved parameterised queries on it →
free-form SQL on the **same path** behind a platform-admin toggle. Cut the box, never the path.
Precedent: Metabase disables native SQL entirely for databases with row/column security, because
it cannot parse SQL to know which tables a query touches.

**Pipeline health is an aggregation, not a subsystem.** Aegis already has honest per-component
truth in five places; the page joins them and adds three missing probes. `unknown` is a distinct
state from `down`. `/readyz` is referenced in three docstrings and **does not exist**; there is
no Neo4j probe.

**Per-tenant guardrails, LLMOps and memory** — with the controls a tenant must **never** have,
enforced by `merge: tighten_only` rather than by prose: weakening a platform guardrail, raising
their own budget cap, raising `gate_min_risk` (the only gating signal), overriding the
guardrails' model role, widening a tool allowlist, setting their own `tenant_id`, or uploading
memory that bypasses `check_input` — **a stored prompt injection is the most patient attack in
the product.**

**Tenant sub-roles: cut.** ~4 days plus schema the additive reconciler refuses, dragging in
Alembic (~2.5 more) — seven days whose demo is "I made a role called Analyst". Replaced with
**named seats**: six capability toggles in the settings table. Same demo sentence, zero new
mechanisms, does not foreclose the real thing.

**The admin forecast page: two panels, two `Source:` lines.** D7 as literally requested cannot
be built — the platform forecast is univariate `statsforecast` and **SHAP does not apply to
it**. SHAP and feature selection belong to `TrustworthyModel`. Merging them into one visual
would be the most damaging thing here.

---

# Phase 8 — Modularity

The thesis of the product, and on 30 Aug it gets tested for real by an AI agent under time
pressure.

**The evidence redirects the obvious approach.** `arXiv 2603.15159` benchmarks exactly our case
— an AI integrating an unseen private library — and complete specs for every API move pass@1 by
only **+5 to +8 pp**. The bottleneck is *invoking*, not *seeing*. The two dominant error classes
are both present in Aegis by name: **omitted operations in a required sequence** (nine ordered
`configure_*` calls, three firing as **import side effects**, two failing *silently* into an
ephemeral in-memory vector store) and **signature misinterpretation** (six of eight `AgentDeps`
fields typed `Callable[..., Awaitable[Any]]`).

So: **delete the sequence, make the contract executable, fail loud at the silent seams.** Docs
are the third lever.

- **`aegis.Aegis` runtime object** — `await Aegis.from_env(adapter="myapp.adapter")` is the one
  supported way up. The globals stay as an implementation detail.
- **`DomainAdapter`, eight members** — the integrator implements exactly one thing.
- **Executable conformance suite** — `pytest --pyargs aegis.conformance`, 10–15 checks, each
  traceable to a defect this repo actually shipped. **It is a jury artifact, not just a dev
  tool.**
- **`/v1` prefix** + an OpenAPI snapshot test failing on removals.
- **Generate the TypeScript client** — 696 hand-written lines currently mirror 1,598 lines of
  Pydantic by hand.
- **Publish the `StreamEvent` union** — the product's most important interface and the only one
  with no machine-readable description.
- `PUBLIC.md` narrowing 427 exported names to ~40 Stable · SemVer from 0.2.0 · `pdoc` reference
  docs, git-ignored · **`AGENTS.md`** (Linux Foundation spec, read from a local checkout — our
  distribution model). **Skip `llms.txt`** — 97% got zero requests in May 2026 and we have no
  docs site.

---

# Phase 9 — Scale and hardening

**Which limit binds first: the model bill, by an order of magnitude.** Not architecture.

The first *architectural* limit: **embedded stores are single-process**, so `--workers 2` is
impossible. Chroma `PersistentClient` is SQLite-locked and reloads the whole HNSW index on every
foreign write; LightRAG's NanoVectorDB is a whole-file JSON rewrite. Fix is
`VECTOR_STORE_MODE=embedded|server` (Chroma's `.server()` already exists) plus refusing to boot
with `--workers>1` while embedded. **That is the one thing foreclosing "scaling later is a
deployment change."**

Measured, 3072-dim float32 brute force: 50k vectors = **614 MB and 13.5 ms per query**, ×3
LightRAG stores, on the event loop.

**Postgres pools are entirely unconfigured** — default 15, then a 30-second stall.

**Budget enforcement on background jobs.** The memory sweeper binds the live completer and the
real embedder and runs every 60 seconds; `enforce_governance` is nowhere on that path.
**Background jobs spend money and nothing stops them.** Every model-calling job carries the
enqueuer's governance context and spends through the same enforcer, with `BudgetExceededError`
as a first-class job outcome.

**RLS fail-closed on an unset scope** — needs a `SECURITY DEFINER` login path and the enumeration
of every unscoped reader. Both sweepers, the LLM-Ops registry warm, `list_recent_audit`, and now
the job claim and the SQL console are load-bearing on the current fail-open predicate.

**Under 20 lines of change removes four of seven ranked limits from the "needs code" column.**

**Named premature, so nobody starts one:** horizontal scale-out, a distributed queue, PgBouncer
(also breaks `LISTEN/NOTIFY`), read replicas, sharding, a log-aggregation stack,
Prometheus/Grafana, exactly-once delivery, per-tenant process isolation, Kubernetes, a real load
harness.

---

## Two tests that make "consistent throughout" real

The user asked for consistency as a property, not an intention. Two tests give it teeth:

1. **The bijection test, extended to the settings catalogue** — a setting cannot exist without a
   UI control appearing.
2. **A route-coverage test** — every non-public endpoint is reachable from some portal. **It
   would fail today on at least eight.**

## Standing principles

**No silent fallbacks** · **measured, never claimed** · **a library's defaults are its author's
trade-offs, not ours** · **real or absent**.

Nearly every real defect found in this project was a violation of the first one: RLS policies
inert because the app ran as a superuser; a budget test green while asserting the reverse of
reality; a console whose every live query returned 400.
