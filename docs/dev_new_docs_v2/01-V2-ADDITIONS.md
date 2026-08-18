# v2 — additions, and the calibration that governs them

**Written 2026-08-17.** Companion to [`00-MASTER-PLAN.md`](00-MASTER-PLAN.md), which keeps its
phase order. This file records what the user added after phases 1 and 2 landed, and the rule
that decides how to build it.

---

## Status

| Phase | State |
|---|---|
| **1 — Tenant isolation** | **DONE** — pushed `d1822c0`. Retrieval leak closed, RLS 3→13 tables and genuinely enforced under a non-superuser role, live mutation-tested proof, SQLite out of tests |
| **2 — ML out of the graph, fiction deleted** | **DONE** — pushed `7e21890`, `d06e0ce`. `aegis.ml` intact, mock data gone, zero refund references, gold corpus re-derived. Found and fixed the persona bug that made every live console query return 400 |
| **3–9** | Replanned in [`02-ROADMAP.md`](02-ROADMAP.md) as platform spine · ingestion · multi-agent · console · control planes · modularity · scale. Each has its own `phase-NN-*.md` |

**Timing is no longer a constraint.** The user's instruction: *"dont think of timing at all at
this point, you just focus on application sota."* Sequence by dependency, not by calendar.

---

## The calibration — read this before designing anything

The user's exact words, and they cut in two directions at once:

> *"no over engineering does not mean we switch from sota — sota is the goal, but no over
> complex stuff and no conservative also."*

Three rules fall out, and they are equally binding:

**1. SOTA is the target.** Pick the best-evidenced approach available. Do not down-scope to
something merely adequate because it is easier or safer to build.

**2. Not over-complex.** One mechanism used well beats three specialised ones. Every component
must earn its place against *"what breaks without it?"* Adding a technique because it has a
paper is not a reason; adding it because it fixes a measured gap is.

**3. Not conservative either.** Refusing something good because it *sounds* expensive is the
same failure as adding something useless because it sounds impressive. Both were made in this
project already:

- *Over-conservative:* the VLM pipeline was ruled out as "impossible at 16 GB". It uses **less**
  memory than the pipeline we ship. The real objection — 255× throughput — was found only by
  measuring.
- *Over-conservative:* the reranker was locked to API-only "because the deploy target is a
  16 GB, no-GPU machine". An ONNX cross-encoder needs neither — the one now shipped is 33M
  parameters and 134 MB. That premise blocked a **+12.1 pp recall@5** change. Symmetrically,
  measuring it in task 4.9 also caught the *optimistic* half of the same decision: the
  predicted 150–400 ms rerank is 1.44 s in reality. The rule cuts both ways or it is not a
  rule.
- *Over-complex:* a proposed enrichment stack of document expansion, hypothetical questions,
  per-chunk summaries and local enrichment models — when the evidence says the wins come from
  *having three components*, not elaborating around them.

**The test for any decision: is this the best available option, and can I say in one sentence
what breaks without it?** If either answer is no, it is wrong.

---

## What the user added

Everything below is new since the original vision doc and is **not yet planned**.

### A — The platform's own nervous system

| # | Requirement | User's words |
|---|---|---|
| A1 | **Database query page** for Aegis admin | *"a page for aegis admin to check all users and other types of data… view full db not like to go in code or db checking"* |
| A2 | **Pipeline check** — component-level audit | *"full pipeline audits logs and checking if all working with logs of their own component"* |
| A3 | **Request tracking** end to end | *"every query end to end logged and saved"* |
| A4 | **Workers, schedulers, queues, batching, maintainers** | *"need to be thought and worked upon and installed in places wherever needed as we are building enterprise scale — this needs to be added sota approach"* |
| A5 | **Proper pipeline structure per task** + module docs | *"for eg rag or agent — everything should have a proper flow pipeline and all components… clear docs of how each module is implemented"* |
| A6 | **Enterprise scale** | *"scaling system to keep in mind"* |

### B — Modularity

| # | Requirement |
|---|---|
| B1 | **Plug-and-play components** usable by any other application |
| B2 | **API surface + generated docs** — the user places these under plug-and-play |
| B3 | **Multi-user scaling architecture** — specifically how multi-agent scales across users |

### C — Agent control and visibility

| # | Requirement | User's words |
|---|---|---|
| C1 | **Grok-style agent selection** | *"option to select agent and stuff — fast, think and agents, or custom… user specific given tool or automatic"* |
| C2 | **Multi-agent animation** + clear tool options | |
| C3 | **Dashboard ultimate** | *"every user their own pages should give them all control to check and do"* — the ~0-code-change goal |

### D — Still open from the original vision

Carried forward, not yet built: per-tenant memory upload / view / delete plus showing which
memories were used at retrieval · per-tenant guardrails and reading the platform defaults ·
per-tenant LLMOps prompt versions · a genuinely-used cache on Memurai, shown working · real MCP
with RBAC plus an admin MCP client · **skills** · real red-teaming for the infra profile ·
tenant sub-roles · downloadable forecast/audit/tenant/budget reports · knowledge-graph
construction visible during ingest.

---

## The architectural call I would make first

**One job substrate on Postgres — not a broker.**

`aegis/src/aegis/memory/consolidate.py` already implements a guarded claim
(`SET status='running' WHERE id=:id AND status='pending'`, `rowcount==0` means another worker
won). Generalise that into a single `jobs` table using `SELECT … FOR UPDATE SKIP LOCKED`.

**Why this and not Celery / RQ / arq:**

- No new infrastructure on a Docker-less Windows box. Each alternative adds a broker, a worker
  supervisor and a second failure domain.
- Jobs are transactional with the data they touch — a job and its result commit together.
- **One queue means one place to observe.** A2 (pipeline check), A3 (request tracking) and A4
  (batching) become views over the same table rather than three separate systems. That is the
  difference between one mechanism used well and three specialised ones.

**Where it must not be conservative:** the substrate needs real retry/backoff, a dead-letter
path, idempotency keys, a visibility timeout and cancellation. A job queue without those is a
toy, and building the toy first is how you end up with two.

**What still needs deciding:** the scheduler (APScheduler vs Postgres-backed vs `pg_cron`),
and whether workers run in-process or as a separate process on Windows without a supervisor.

**Every new table needs a tenant story and an RLS policy** — Phase 1 registered 13 tenant-scoped
tables in `aegis/src/aegis/governance/rls.py`, and the boot-time catalog read-back reports any
that are missing.

---

## The highest-risk item: A1, the database query page

A SQL console inside a web application is a serious attack surface, and it deserves a
deliberate design rather than a text box wired to `execute()`.

Questions the plan must answer: read-only enforcement (a separate Postgres role, not a regex
on the string) · statement timeouts · row caps · which role executes, and how RLS interacts
with an admin who is *meant* to see across tenants · an audit row for every query run · and
whether it should be free-form SQL at all, versus a curated schema browser plus saved queries.

Stated plainly because the user asked for reasons, not conclusions: free-form SQL is more
powerful and is what they asked for; a schema browser is safer and covers most of the real need
("check all users and other types of data"). The recommendation goes in the plan.

---

## Research plan — three agents, then new phase files

**New phase files are written only after this research lands.** The order:

| Agent | Scope | Output |
|---|---|---|
| **1 — Enterprise substrate** | Jobs, queues, workers, scheduler, batching (A4) · end-to-end request tracking (A3) · pipeline-health page (A2) · admin DB query page (A1) · what "enterprise scale" honestly means here (A6) | `plans/04-enterprise-substrate.md` |
| **2 — Modularity and scale** | Plug-and-play module contract (B1) · public API surface and generated docs (B2) · multi-user and multi-agent scaling architecture (B3) | `plans/05-modularity-scale.md` |
| **3 — Dashboards and control** | The per-profile control goal (C3) · Grok-style agent selection (C1) · memory, guardrails, LLMOps, sub-roles, reports, red-teaming (D) | `plans/06-dashboards-control.md` |

**Not re-researched:** skills and MCP are already covered in
[`plans/02-agentic-core-console.md`](plans/02-agentic-core-console.md); they get folded into the
phase files rather than studied again.

**Then:** the additions are integrated into phases 3–6 rather than becoming separate phases,
because they are mostly *properties* of those phases — a pipeline needs a queue, a console
needs agent selection, an admin surface needs the DB page — not work that stands alone.

---

## Standing principles these inherit

From [`00-MASTER-PLAN.md`](00-MASTER-PLAN.md), unchanged and still binding:

**No silent fallbacks.** A control that cannot run fails closed and says so. Nearly every real
defect found in this project was a violation of this — RLS policies inert because the app ran
as a superuser; a budget test green while asserting the reverse of reality; a console whose
every live query returned 400.

**Measured, never claimed.** If a number is on screen, something computed it.

**A library's defaults are its author's trade-offs, not ours.** Every deviation gets written
down with its reason.

**Real or absent.** No gimmicks. If the cache is not really caching, do not draw a cache.
