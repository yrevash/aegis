# Production readiness, deployment, scaling and security — comparative research

> Lane: "How would you deploy this in an enterprise, and what happens at scale?"
> Constraints assumed throughout: **one Windows demo machine, no Docker, no container runtime, 7-day build (29 Aug → 4 Sep 2026), synthetic data, live demo judged by CTOs.**

---

## Executive answer

- **PS-17 wins this lane: 8.5/10 vs PS-04 6.5/10.** PS-17 has the genuinely hard production problem (bounding retroactive re-evaluation fan-out over bitemporal state, with exactly-once *effects*), and — decisively — it is the only one of the two where you can **kill a worker on stage and let the jury watch the system heal**. That is a falsifiable claim a CTO can verify with their own eyes in 90 seconds.
- **PS-04's compute is embarrassingly easy and you should say so.** For a 5,000-borrower / 9,000-facility book, the entire nightly model inference is **under one second of CPU**; the whole nightly run fits in ~20 minutes on one laptop. Any team that pitches "scale" for PS-04 in terms of GPUs or clusters has misread the problem.
- **PS-04's real scaling limit is human, not machine.** The RBI's own Early Warning Signals list runs to ~42 indicators (Master Directions on Fraud Risk Management, 15 July 2024). Naively evaluating 42 indicators × 5,000 borrowers daily at even a 0.5% fire rate yields **~1,050 alerts/day → ~168 alerts per relationship manager per month → 3–4 hours of every RM's day** at 20–30 min of documented triage each. The best-sourced analogue: BPI's *Getting to Effectiveness* found surveyed US institutions reviewed **~16 million alerts in 2017 to file ~640,000 SARs**, of which a **median 4%** drew law-enforcement follow-up — an end-to-end yield near **0.16%**, sustained by **>14,000 staff and ~$2.4bn**. Design consequence: **alert volume must be a controlled variable (a capacity-constrained knapsack), not a model output.**
- **PS-17's retroactive fan-out is the interesting scaling question and it has a real answer.** A retroactive amendment to a *single* obligation re-opens ~4,400 events; a retroactive amendment to a *master* term inherited by 200 SOWs re-opens **~18.2 million**. The bound is: bitemporal range-join + scope-predicate index + verdict memoisation by rule-bytecode hash + a **declared-cardinality gate** that refuses to auto-run a backfill above a threshold and instead opens a human "Retroactive Impact Review". Even the pathological case lands at **~1 hour of chunked, resumable backfill** at a priority class that cannot starve live traffic.
- **The Windows-without-Docker worker landscape is mostly traps, and there is exactly one clean answer.** Celery is officially unsupported on Windows since 4.x. RQ calls `os.fork()`. arq is Redis-only and **Redis has no official Windows build** (Microsoft's port is archived at 3.0.504). Huey's docs state multiprocess support is unavailable on Windows. APScheduler 4 ships with an explicit "do NOT use this release in production" warning. **Restate publishes no Windows binary at all.** The survivors: **Postgres `FOR UPDATE SKIP LOCKED` / pgmq (SQL-only install path, no C/Rust compile) + DBOS Transact (pip library, Postgres-checkpointed durable execution, documented Windows commands, no separate orchestrator).**
- **Recommended stack, both problems: Postgres 16 (native Windows installer) as the *only* server process + DBOS Transact for durable execution + pgmq for priority-class queues.** No Redis, no RabbitMQ, no Erlang, no Kafka, no Debezium. One stateful component on the deployment slide is the punchline.
- **Every other team will cite SR 11-7 for PS-04. SR 11-7 no longer exists.** It was superseded on **17 April 2026** by **SR 26-2 / OCC Bulletin 2026-13**, which also supersedes SR 21-8. Its footnote 3 states that generative and agentic AI models are **not within scope**, while the principles **do** apply to non-generative, non-agentic AI models — and its definition of "model" **excludes deterministic rule-based processes**. This dictates the architecture: put the *prediction* in a governable statistical model, use the LLM only for narrative assembly over already-computed numbers.
- **PS-17 has a SOX exposure almost nobody will spot.** SLA credits and penalties are **variable consideration under ASC 606**. A system that decides whether a credit is owed is producing an input to the transaction price — i.e. to recognised revenue — putting it in ICFR/ITGC scope (PCAOB AS 2201 requires auditors to assess the extent of IT involvement in period-end financial reporting). This is precisely where a **tamper-evident hash-chained audit log stops being a gimmick and becomes the control**.
- **PS-04 has a symmetrical, two-edged IFRS 9 finding.** IFRS 9 says an entity **cannot rely solely on past-due information** where more forward-looking information is available without undue cost or effort. Building a 30/60/90-day breach forecast therefore arguably *creates* an accounting obligation: once the bank has it, it is information it must consider for SICR staging, pulling model output into ECL provisioning and external audit scope. Mitigation to state explicitly: keep the EWS **decision-support only**, formally outside the SICR pipeline, with a documented boundary.
- **What would flip the verdict:** PS-17 is higher ceiling, **lower floor**. Get bitemporal modelling wrong and the stage demo double-issues credits in front of the jury. PS-04's failure mode is merely boring. A team of <3, or one shaky on transactional reasoning, should take PS-04.

---

## PS-17: Contract Obligation, SLA & Commercial Leakage Monitor

### 1. The scaling maths, specific to the workload

**Portfolio parameters** (stated assumptions for a mid-size managed-services / telco / IT-outsourcing provider; the arithmetic is parametric so the jury can re-run it with their own numbers):

| Symbol | Meaning | Value |
| --- | --- | --- |
| N | Active customer contracts | 2,000 |
| — | Amendments per contract per year | 1.4 → **2,800 amendments/yr** |
| M | Tracked obligations per contract | 40 (≈12 SLA-measurable; rest are notice / reporting / renewal / pricing) |
| — | Total tracked obligations | **80,000** |
| K | Service events per contract per day (tickets, incidents, uptime samples, change requests, batch outcomes) | 250 |

**Steady-state evaluation rate.**

```
Daily events        = 2,000 × 250            = 500,000 events/day
                                              ≈ 5.8 events/sec average
                                              ≈ 30/sec peak (5× diurnal)
Matching obligations per event (scope predicate: service line × region × severity × product)
                                              ≈ 3 of the 12 SLA obligations
Obligation-event evaluations = 500,000 × 3   = 1.5M/day ≈ 17/sec average
```

**17 evaluations/second.** That is nothing. A single Postgres and a handful of worker processes on a commodity Windows box do this without noticing. **This is the finding to lead with**, because it reframes the whole conversation: PS-17 is not compute-bound in steady state, so any team pitching horizontal scale for the *happy path* is pitching the wrong thing.

**What actually costs money: LLM placement.** If you LLM-evaluate every event, that is **1.5M LLM calls/day ≈ 3 billion tokens/day**. Absurd, and the reason most agentic contract demos cannot be productionised. The architecture must be:

- **LLM at extraction time only.** 2,000 contracts × ~30 pages × ~800 tokens ≈ **48M input tokens, one-time** — on the order of **$150** at ~$3/M input. Amendments add 2,800/yr × ~3 pages: negligible.
- **Deterministic rule evaluation at event time.** Each obligation-version compiles once into an executable predicate (threshold, window, measurement basis, exclusions/service-credit caps). Events are evaluated against compiled bytecode, not prose.
- **LLM only at ambiguity adjudication** — contradictory evidence, unparseable clause, novel exclusion — target **<1% of events ≈ 15,000/day**, and gate that with a budget.

This split *is* the production-readiness answer, and it is visually explainable: one diagram, two paths, one with a dollar sign on it.

**The retroactive amendment fan-out — the genuinely interesting problem.**

The finale inject is exactly this: an amendment changes an SLA threshold *after* breaches were flagged; every affected event must be re-evaluated under the correct effective version.

Blast radius arithmetic. An amendment arrives on day *D* with `effective_from = D − 365`:

```
Single-obligation amendment (e.g. P1 resolution target 4h → 2h on one contract):
   events matching that obligation's scope ≈ 12/day (P1 incidents on that contract)
   × 365 days                              = ~4,400 re-evaluations        → milliseconds

Master-agreement amendment inherited by 200 subsidiary SOWs:
   200 contracts × 250 events/day × 365    = 18,250,000 re-evaluations    → the real problem
```

The naive implementation is O(all events × all obligations) and will hang on stage. The bounded implementation has five parts:

1. **Bitemporal keying.** Every event carries `(occurred_at, recorded_at)`; every obligation-version carries `(valid_from, valid_to, asserted_from, asserted_to)`. This is the SQL:2011 application-time + system-versioned period model. Without both axes you cannot answer the two distinct questions the brief demands: *"what was true at time T?"* versus *"what did the system believe at time T?"* Re-evaluation becomes a **range join**, not a scan.
2. **Scope-predicate index.** Materialise each obligation-version's scope as a normalised selector tuple `(service_line, region, severity, product, entity)`; index events on the same tuple. "Which events does this amendment touch?" resolves as a bitmap index intersection in milliseconds — and, crucially, its **cardinality is computable before any work starts**.
3. **Delta-only recomputation.** The verdict view is `V = f(events ⋈ obligation_versions)`. An amendment is a *delta on the obligation_versions relation*. DBSP (VLDB 2023 best paper) proves that for any such query the incremental version is mechanically derivable and its cost is proportional to the delta and its join image, not to the base relations. You do not need to install Feldera; you need the **discipline**: never recompute the view, always compute Δ.
4. **Verdict memoisation by content hash.** Key each verdict on `hash(obligation_version_id, event_id, rule_bytecode_hash, input_facts_hash)`. Amendments typically touch 1–3 clauses of 40, so **~93% of memo lookups hit** and do zero work. The re-evaluation "fan-out" is therefore usually 1/13th of its nominal size.
5. **The declared-cardinality gate — the part that makes it a product.** Compute the re-evaluation cardinality *before* enqueuing. Above a threshold (say 100k), the amendment does **not** auto-run. It opens a **Retroactive Impact Review**: a human sees *"this amendment re-opens 18.2M adjudications and may reverse ₹X of already-issued credits"* and approves the run, which then executes chunked, checkpointed and resumable at a priority class below live traffic. This satisfies the brief's "material commercial settlement decisions remain human-owned" *and* its "safe recovery when only part of a workflow succeeds", and it is the correct product behaviour: no CFO wants a system silently reversing a year of credits at 03:00.

**Backfill throughput.** A re-evaluation is deterministic bytecode against pre-materialised facts; the bound is the Postgres write of new verdict versions — roughly **5,000 verdict rows/sec** with `COPY`-based batching on one commodity Windows box. So:

```
18,250,000 re-evaluations ÷ 5,000/sec ≈ 61 minutes
```

**The pathological case is a one-hour bounded backfill, not an unbounded one.** That is a number you can put on a slide. *(Estimate from batch-insert rates, not a measured benchmark — measure it on the actual demo laptop on day 2 and replace it with the real figure.)*

**Storage and retention.** 500k events/day × 3 verdicts × ~300 B ≈ **450 MB/day → 164 GB/yr**. Real, and worth a bullet. Mitigation: persist full per-event verdicts hot for 90 days; beyond that keep only **state transitions** (compliant→breach, breach→cured) plus daily rollups, using Postgres declarative partitioning by month on `occurred_at` and partition detach to compressed archive.

**Idempotency across re-evaluation.** Every external effect (issue credit note, send notice) is keyed by `hash(obligation_version, breach_window, action_type)`. A re-evaluation that reaches the *same* conclusion must not re-issue. This is the brief's "preventing duplicate requests, duplicate transactions or repeated external actions", and it is the invariant you will assert live during the chaos moment.

### 2. Workers and schedulers

See the shared Windows-viability table in the [Cross-cutting](#cross-cutting-the-windows-no-docker-worker-landscape) section below.

**Recommended stack for PS-17:**

```
Postgres 16 (native Windows installer)   ← the ONLY server process
  ├─ bitemporal domain tables (partitioned monthly on occurred_at)
  ├─ pgmq queues (SQL-only install — no C/Rust compile)
  ├─ transactional outbox
  ├─ DBOS workflow/step checkpoints
  ├─ effects table (idempotency, UNIQUE effect_key)
  └─ hash-chained audit log

DBOS Transact (pip)  ← durable execution; each adjudication and each
                       external action is a checkpointed step
Worker pools (Windows services via NSSM), three priority classes:
  q_live     vt=30s   — new events            SLO p99 < 2s
  q_amend    vt=300s  — targeted re-eval      SLO p99 < 60s (≤10k events)
  q_backfill vt=900s  — bulk retroactive      ≥5,000 re-evals/sec, preemptible
```

- **Queue**: pgmq gives SQS-parity semantics — visibility timeout, archive tables for replayability, FIFO with message-group keys — with **no new daemon**, because its SQL-only install path avoids compiling an extension (the thing that is genuinely painful on Windows without MSVC + Postgres source).
- **Worker pool**: fixed process count *per priority class*, each competing via `SELECT … FOR UPDATE SKIP LOCKED` (in Postgres since 9.5, January 2016). Weighted so BACKFILL structurally cannot starve LIVE — separate pools, not separate priorities in one pool.
- **Scheduler**: `@DBOS.scheduled` cron workflows for renewal/notice deadline sweeps. **Windows Task Scheduler is used only as the outermost watchdog** that starts the service at boot and restarts it if it dies — it has no retries-with-backoff, no DLQ, no idempotency and only minute granularity, so it must never be the queue.
- **Retry/backoff**: exponential with full jitter, max 6 attempts. Then `pgmq.archive` into a DLQ table carrying the failing step name, the input hash, and the OpenTelemetry trace id — so a DLQ row is one click from the trace that produced it.
- **Idempotency**: `INSERT INTO effects (effect_key, …) ON CONFLICT DO NOTHING RETURNING id`. No row returned ⇒ the effect already happened ⇒ skip. One line, and it is the whole exactly-once-effects story.
- **Backpressure**: producers poll `pgmq.metrics(queue)`; above a high-water mark the ingest endpoint returns 429 and the backfill pauses. Simple, visible, **demoable** — put the queue depth on the UI.

### 3. Failure recovery

The brief explicitly demands "safe recovery when only part of a workflow succeeds". Taxonomy and response:

| # | Partial failure | Recovery |
| --- | --- | --- |
| 1 | Worker crash between steps | Durable step checkpoint (DBOS); resume from last completed step |
| 2 | **Step succeeded, checkpoint didn't** — the classic | Exactly-once *effects*: the unique effect key makes the replayed step a no-op |
| 3 | External call ambiguous (timeout, no response) | Query-then-act on the idempotency key; pass the key downstream so the callee dedupes |
| 4 | Multi-effect workflow half-done (notice sent, credit note not created) | **Compensating action** (saga) — issue a retraction, and record the compensation as a first-class event in the same audit stream so replay shows sent→compensated, never a silent deletion |
| 5 | Poison message crashing every worker | Attempt counter → DLQ after N; DLQ row is replayable from the UI after a fix |
| 6 | **Retroactive invalidation of an already-executed effect** (the amendment says the credit you issued wasn't owed) | Not a bug — a business event. Never mutate the prior verdict; append `superseded_by` and open a human-gated clawback/settlement task |
| 7 | Duplicate / late-arriving corrected evidence | Dedupe by natural key + content hash; bitemporal `recorded_at` preserves the earlier belief rather than overwriting it |

**Exactly-once delivery is impossible; exactly-once *effects* are achievable.** Say this sentence to the jury. What you build is: at-least-once delivery + idempotent effects + a **transactional outbox** so that "we decided X" (DB write) and "tell the world X" (message) commit atomically. The pattern's own documented caveat is that the relay may publish duplicates if it crashes after publishing but before marking sent — which is *precisely why* consumers must be idempotent, and quoting that caveat back at the jury demonstrates you read the pattern rather than the buzzword.

Outbox on Postgres, Windows-friendly: `outbox(id, aggregate_id, payload, created_at, published_at)`; the relay is a DBOS scheduled workflow doing `SELECT … FOR UPDATE SKIP LOCKED WHERE published_at IS NULL`. **Polling publisher, not transaction-log tailing** — deliberately, because CDC (Debezium/Kafka) does not install cleanly on bare Windows and would be a demo-time trap.

**The stage demo — PS-17 wins this outright.** Frame it as a chaos experiment with a declared steady-state hypothesis (principlesofchaos.org: steady-state hypothesis, vary real-world events, minimise blast radius) rather than "watch me kill a process":

> **Hypothesis:** verdict count per event stays at exactly 1; the `effects` table gains zero duplicate rows; the backfill resumes from its checkpoint.

1. Start a retroactive amendment backfill over 4,400 already-adjudicated events. Progress bar reaches 38%.
2. **Kill the worker process in Task Manager, visibly.**
3. UI shows the lease expiring; the queue's visibility timeout returns in-flight messages; a second worker claims them.
4. Backfill resumes **at 38%, not at 0** — and the verdict count does not double.
5. Show the `effects` table: **0 new rows** for already-applied verdicts.
6. Show one mid-flight event: exactly one verdict, not two.
7. Replay the audit log: reconstruct "what the system knew at 14:32" versus "what it knows now".

Ninety seconds, fully falsifiable, and it maps word-for-word onto the brief's own phrasing.

### 4. Security, tenancy and compliance

**Multi-tenancy — and why PS-17's is *harder* than PS-04's.** Postgres RLS is the right primitive: `ENABLE ROW LEVEL SECURITY` plus **`FORCE ROW LEVEL SECURITY`** so even the table owner is subject to policy; the app connects as a non-superuser role without `BYPASSRLS` and sets `SET LOCAL app.tenant_id` per transaction. Name the two documented caveats out loud — this is what separates a real answer from a slide:

- **Referential-integrity checks always bypass RLS**, so unique-constraint violations are a covert channel that can leak the existence of another tenant's row. Schema design must avoid cross-tenant unique constraints.
- **Policy subqueries have a documented READ COMMITTED race** unless you use `FOR SHARE`; a concurrent transaction can see data filtered under a stale privilege snapshot.

But row-level isolation is **not sufficient** for PS-17. An obligation, an amendment and a service event may belong to *different counterparties with mutually confidential terms*. A cross-contract benchmarking view must not leak Customer A's pricing to Customer B's account team. So PS-17 needs **clause-level redaction** on top of row-level policy — a harder, more demonstrable, and more differentiating control than anything PS-04 requires.

**Secrets on bare Windows.** There is no Vault daemon. Use DPAPI-backed Windows Credential Manager (via `keyring`), scoped to a dedicated **low-privilege service account** — and be honest that on a demo laptop this is theatre unless the service actually runs under that account, so make it do so. Never a `.env` in the repo.

**PII and confidentiality.** PS-17's PII surface is modest (signatories, owner contacts) but its **confidentiality** surface is the whole product: the contracts *are* the confidential asset, governed by NDA scope rather than privacy statute. GDPR Art. 32 / DPDP security-safeguard duties apply to the personal data that is there. The operative control is **field-level redaction before any LLM egress**, plus a local-model path for the sensitive route.

**SOX — the finding almost no team will have.** SLA credits and penalties are **variable consideration under ASC 606** (step 3 of the five-step model), constrained to the amount for which a significant revenue reversal is not probable. A system that determines whether a service credit is owed is producing an input to the transaction price — i.e. to recognised revenue. That makes it a system relevant to financial reporting, in ICFR/ITGC scope; **PCAOB AS 2201** requires the auditor to evaluate the extent of IT involvement in the period-end financial reporting process. Build these in from day one:

- **Change management over rule versions** — who changed the SLA rule, approved by whom, when, with the prior version retained.
- **Completeness-and-accuracy evidence** for system-generated reports.
- **Segregation of duties** — whoever edits an obligation rule cannot approve the resulting credit.
- **Immutable retention** — and this is exactly where a **Merkle / hash-chained audit log** stops being an "out-of-the-box factor" gimmick and becomes the actual ICFR control. That is the strongest justification either problem statement offers for the cryptographic-provenance angle.

**Autonomy model (the rubric asks for this explicitly).** L4 (*act, then notify*) for evidence gathering, reconciliation and draft preparation. **L2 (*recommend, human decides*) hard-wired** for legal interpretation, contractual notice and material commercial settlement — mandated by the brief. Implement the gate as a **DB-enforced state transition requiring a signed approver row**, not a UI checkbox; a checkbox is not a control and a CTO will ask.

**Data residency — with a nice recursion.** The applicable rule is whatever the customer contracts' data-location clauses say — *which the system itself extracts*. "The system knows its own residency obligations because it read them out of the contracts" is a slide worth having.

### 5. The deployment slide

```
┌─ Edge ───────────────────────────────────────────────────────────┐
│ Static SPA + FastAPI/uvicorn behind IIS+ARR (or Caddy) on Windows │
│ STATELESS · scales horizontally · no session affinity             │
└───────────────────────────────────────────────────────────────────┘
                              │
┌─ Durable execution tier ─────────────────────────────────────────┐
│ DBOS worker pools, each a Windows service under NSSM              │
│   pool LIVE (n=4)  ·  pool AMEND (n=2)  ·  pool BACKFILL (n=2)    │
│ STATELESS · scale by adding processes; they compete via SKIP LOCKED│
└───────────────────────────────────────────────────────────────────┘
                              │
┌─ STATE (the only stateful component) ────────────────────────────┐
│ Postgres 16, native Windows installer                             │
│   bitemporal domain · pgmq queues · outbox · DBOS checkpoints      │
│   effects/idempotency table · hash-chained audit log              │
│ + streaming replica (read scale + failover)                       │
└───────────────────────────────────────────────────────────────────┘
                              │
┌─ Ancillary ──────────────────────────────────────────────────────┐
│ Contract PDFs: filesystem share, content-addressed by sha256      │
│   (DB stores hashes, never blobs)                                 │
│ LLM gateway: redaction + prompt-hash cache + rate limit;           │
│   swappable for a local model                                     │
│ Telemetry: OTel SDK → OTLP; GenAI semantic conventions            │
│   (invoke_agent parent span, execute_tool children,               │
│    gen_ai.usage.input_tokens / output_tokens)                     │
└───────────────────────────────────────────────────────────────────┘
```

**SLOs.**

| SLO | Target |
| --- | --- |
| Live event → verdict | p99 < 2s |
| Amendment → targeted re-evaluation (≤10k events) | p99 < 60s |
| Bulk retroactive backfill throughput | ≥ 5,000 re-evaluations/sec |
| Duplicate external effects | **0 — hard invariant, alerted on** |
| Audit-chain verification | hourly job, must pass |
| RPO / RTO | 5 min (WAL archiving) / 15 min |

---

## PS-04: Dynamic Covenant Monitoring & Early Warning

### 1. The scaling maths, specific to the workload

**Portfolio parameters** (mid-size commercial bank C&I book):

| Symbol | Meaning | Value |
| --- | --- | --- |
| P | Commercial borrowers | 5,000 |
| F | Facilities per borrower | 1.8 → **9,000 facilities** |
| C | Covenants per facility | 3.5 → **31,500 covenants** |
| H | Horizons | 3 (30 / 60 / 90 days) |

**Nightly compute.**

```
Predictions per night  = 9,000 facilities × 3 horizons   = 27,000
   (at covenant granularity: 31,500 × 3               = 94,500)

Feature vector         ≈ 250 features per borrower-day
Raw scan if naive      = 5,000 borrowers × 730 days      = 3.65M borrower-days
                       @ 20 txns/borrower/day            ≈ 73M transactions over 2 yrs
                       → 2–5 min with partitioning; SECONDS with pre-aggregated
                         borrower-day rollups (window over 730 pre-agg rows, not 14,600 txns)

GBM inference (500 trees) ≈ 100,000 rows/sec/core
   94,500 rows                                            ≈ < 1 SECOND
```

**The entire nightly model inference is under one second of CPU.** This is the honest headline and it should be said out loud, because it kills the generic "we'd scale this on Kubernetes" answer stone dead and replaces it with the real constraint.

**What actually dominates the nightly window:**

- **Explanation, not prediction.** TreeSHAP is O(T·L·D²) per row — for 500 trees at depth 6 (~64 leaves), roughly **1–5 ms/row** → 94,500 rows ≈ **5 minutes single-core, ~40 seconds on 8 cores**. Fine. **KernelSHAP would be 100–1000× worse and is a genuine demo-time trap** — name it and avoid it.
- **LLM narrative generation is the budget killer if placed wrongly.** One narrative per scored facility = **94,500 LLM calls/night ≈ 180M tokens/night**. The design answer: generate narratives **only for alerts that survive triage** (~100–300/night) and template the rest. 300 × 2k tokens ≈ 600k tokens/night — trivial.
- **Net: the nightly window is comfortably under 20 minutes on one Windows box.** SLO: *portfolio scored, explained, triaged and ranked by 06:00 local*.

**Intraday signal triggers.** 5,000 borrowers × 20 txns/day = 100k transactions/day; trigger rules fire on ~2% = **~2,000 intraday re-scores/day ≈ 0.02/sec**, peaking maybe 200/min. Trivial. Do not over-engineer this.

**Feature recomputation when a restated financial statement arrives.** This is PS-04's structural analogue of PS-17's retroactive amendment — and it is **materially smaller**, which is exactly why PS-04 scores lower in this lane:

```
Restatement, one borrower, 8 restated quarters:
   1 borrower × 730 days × 3 horizons = 2,190 re-scores  → ~22 ms
50 borrowers restating in one quarter:
   110,000 re-scores                                     → ~1 second
```

**But there is a nastier one that is easy to miss.** If the restatement triggers **retraining or recalibration**, *every* borrower's score changes — a full-portfolio rescore. Computationally that is still ~1 second. Operationally it is a **model version change**, which under the model-risk regime requires validation, versioning, and the ability to reproduce any historical alert under the model version in force at the time. So PS-04's hard production requirement is not compute; it is **model-version-pinned reproducibility**: every stored alert must carry `(model_version, feature_snapshot_hash, threshold_config_version)`.

This also forces the single design decision that prevents the most common silent bug in EWS builds: a **point-in-time-correct feature store**. One append-only row per borrower per day, with `computed_at` and `source_snapshot_hash`; restatements **append a new snapshot, never update in place**. Without this you get look-ahead leakage into backtests and the model's reported performance is fiction.

### The alert volume and false-positive burden — the real operational limit

This is where PS-04's genuine production difficulty lives, and it is a *human* constraint.

**The naive arithmetic, straight from the regulator's own indicator list.** The RBI Master Directions on Fraud Risk Management (15 July 2024) enumerate roughly **42 Early Warning Signals** — bouncing of high-value cheques, non-routing of sales proceeds through the consortium, heavy cash withdrawal in loan accounts, significant reduction in promoter/director stake, resignation of key personnel, disproportionate movements in receivables versus turnover, and so on. Evaluate them naively:

```
5,000 borrowers × 42 indicators              = 210,000 indicator evaluations/day
@ 0.5% fire rate                             = ~1,050 alerts/day
                                             = ~21,000 alerts/month
125 RMs (at 40 borrowers each)               = ~168 alerts / RM / month
                                             = ~8 per working day per RM
@ 20–30 min per documented, defensible triage
                                             = 3–4 HOURS of every RM's day
```

**That arithmetic is what kills deployed EWS**, and it comes from the regulator's own list, not from a vendor's marketing.

**The published evidence for the yield.** No peer-reviewed false-positive rate specific to *commercial-credit* EWS was found in this research; AML transaction monitoring is the right proxy and should be presented explicitly *as* a proxy. The best-sourced figure is the Bank Policy Institute's *Getting to Effectiveness* survey of US financial institutions:

- **~16 million alerts reviewed in 2017** → **>640,000 SARs filed** (≈4% alert-to-SAR conversion)
- Of those SARs, a **median 4%** drew a law-enforcement follow-up inquiry
- End-to-end yield ≈ **0.16%**
- Sustained by **>14,000 BSA/AML staff and ~$2.4bn of spend**, across **as many as 20+ IT systems per institution**
- Of ~2.36M customers designated high-risk, a median ~6% were SAR-subjects, of which 0.3% drew follow-up

Industry commentary places AML false-positive rates at **85–95%**; a widely-repeated (weaker-sourced) survey figure for credit EWS specifically is that roughly **eight in ten early warning signals prove to be false alarms**. *(Treat the 8-in-10 as indicative only — it appears in practitioner commentary, not in a peer-reviewed or regulator source.)*

**The design consequence — this is the answer that wins the lane for PS-04 if you take it:**

1. **Alert budget / capacity-constrained thresholding.** Do **not** threshold on probability. Threshold on *rank subject to a capacity constraint*: if the bank has 125 RMs at 8 investigable alerts/month each, the system emits **exactly the top 1,000 alerts/month by expected-loss-weighted risk** and nothing else. This converts "how many false positives?" into "given a fixed review budget, what is the maximum expected loss avoided?" — a **knapsack, not a threshold**. It is also the only formulation a CRO will actually buy.
2. **Precision@budget, not AUC.** Every other team will show an ROC curve. Show `precision@k` and `expected-loss-captured@k` where k is the review capacity. AUC is indifferent to where your budget lands on the curve; a CRO is not.
3. **Alert deduplication and case-merging.** One deteriorating borrower with 3 facilities × 3 covenants × 3 horizons emits **27 alerts for one story**. Collapse to a single borrower-level case with a driver tree. This alone is typically a **10–20× volume reduction** and is the highest-ROI engineering decision in the whole system.
4. **Suppression with memory.** Once an RM dispositions *"known — seasonal working-capital swing, valid to 31 Mar"*, the same driver signature must not re-alert. Persist the disposition keyed by `(borrower, driver_signature_hash, valid_until)`. This is the feedback loop every deployed EWS eventually grows and that almost no hackathon demo shows.
5. **Tiered autonomy.** L4 auto-triage (system dispositions and closes low-value alerts *with a recorded rationale*), L2 RM review, L3 credit committee. Only L2/L3 consume scarce human time.

Make **alert count a service objective**, not an output. That single slide is the most sophisticated production-readiness claim available in PS-04.

### 2. Workers and schedulers

Same substrate as PS-17, different shape:

```
Postgres 16 (native Windows installer)
  ├─ borrower_day_features (append-only, point-in-time correct)
  ├─ model_versions registry (hash, training snapshot id, validation report, approver)
  ├─ alerts + dispositions (suppression memory)
  ├─ pgmq: q_signal (vt=60s) for intraday triggers
  └─ DBOS checkpoints

DBOS Transact:
  @DBOS.scheduled("0 2 * * *")  nightly_run  ← ONE durable workflow
      step: extract → step: feature-build → step: score
          → step: explain → step: triage → step: rank → step: publish
      fan-out inside steps via a DBOS queue, concurrency = cores
Scoring workers: plain Python processes holding the pinned model artifact in memory
```

**The argument for durable execution here in one sentence:** if the nightly batch dies at 03:12 during explanation, it resumes **at explanation**, not at extraction. That is a 15-minute save every time and it is the whole reason not to write a cron script.

No Redis. No RabbitMQ. No Erlang. One server process.

### 3. Failure recovery

PS-04's partial-failure surface is real but narrower than PS-17's:

- **Nightly batch dies mid-run** → resume from last completed step (DBOS). Well-handled, but a batch job that restarts does not surprise a CTO.
- **Partially-published alert set** → publish is a single transactional step gated on the whole run completing; never publish a half-scored portfolio, because a *missing* alert is invisible and therefore worse than a late one.
- **Duplicate alerts on retry** → idempotency key `hash(borrower, driver_signature, run_date, model_version)`.
- **Restated statement arriving mid-run** → snapshot isolation on `source_snapshot_hash`; the run completes on the snapshot it started with, then a targeted re-score is enqueued. Never mix snapshots inside one run.
- **Model artifact missing or corrupt** → hash-verify on load; refuse to run rather than score with the wrong model. Failing closed is the correct behaviour for a regulated model and worth saying.

**PS-04's best live moment is reproducibility, not recovery.** *"Here is an alert from 14 days ago; re-derive it under the model version and feature snapshot in force that day, and get bit-identical output."* That is genuinely impressive — but it reads as **determinism**, and determinism is an explainability demo wearing a production-readiness costume. It does not produce the visceral "the system healed itself" moment a CTO panel remembers.

### 4. Security, tenancy and compliance

**This is PS-04's strongest section — the regulatory story is richer and more citable than PS-17's.**

**Model risk — and the trap every other team will fall into.** SR 11-7 is the reflexive citation. **SR 11-7 no longer exists.** On **17 April 2026** the Federal Reserve, OCC and FDIC issued **SR 26-2 / OCC Bulletin 2026-13, "Revised Guidance on Model Risk Management"**, which *supersedes and replaces* both SR 11-7 (April 2011) and SR 21-8 (the 2021 interagency statement on MRM for BSA/AML systems). Three provisions change the PS-04 architecture:

1. **Applicability** is "expected to be most relevant to banking organizations with over $30 billion in total assets", with a tailored, risk-based approach below that.
2. **The definition of "model" excludes deterministic rule-based processes** and simple arithmetic. So PS-04's covenant calculator is *out* of model scope; the 30/60/90-day breach predictor is squarely *in*.
3. **Footnote 3, verbatim:** *"Generative AI and agentic AI models are novel and rapidly evolving. As such, they are not within the scope of this guidance. Nonetheless, a banking organization's risk management and governance practices should guide the determination of appropriate governance and controls for any tools, processes, or systems not covered in this document. However, the principles described in this guidance apply to traditional statistical and quantitative models and non-generative, non-agentic AI models."* The agencies have signalled a separate RFI on AI/ML.

**The architectural consequence is not stylistic.** Put the **prediction** in a governable, versioned, validatable statistical model (GBM with monotonic constraints on the credit-sensible features), and use the LLM **only for narrative assembly and evidence retrieval over already-computed numbers — never as the estimator.** That is the difference between an artefact a bank's MRM function can validate *today* and one sitting in a regulatory gap the agencies have explicitly declined to cover. Saying this to a CTO jury, with the April 2026 date and the footnote, is the single sharpest compliance move available in either problem statement.

**IFRS 9 — the two-edged finding.** IFRS 9's 30-days-past-due SICR trigger is a **rebuttable presumption and a backstop, not a primary staging mechanism**; where reasonable and supportable forward-looking information is available without undue cost or effort, an entity **cannot rely solely on past-due information**. A 30/60/90-day breach forecast *is* such information. So building this EWS arguably **creates an accounting obligation**: once the bank has it, it becomes information it must consider for SICR staging, pulling model output into ECL provisioning and into external audit scope. State the mitigation explicitly: keep the EWS **decision-support only**, formally outside the SICR staging pipeline, with a documented boundary and a governance decision on record — unless and until it is validated as an ECL input. Very few teams will have thought about the downside of their own system being *too* good.

**RBI Master Directions on Fraud Risk Management (15 July 2024).** These superseded and consolidated 36 prior circulars. Directly relevant obligations:

- **EWS framework integrated with Core Banking Solution** for real-time monitoring (Clause 8.3) — an architectural requirement, not just policy.
- **Red Flagged Account (RFA)**: an account with one or more EWS indicators requires deeper investigation and preventive measures (Clause 8.3.1); accounts above the CRILC threshold identified as RFA must be **reported to RBI within seven days** (Clause 8.3.3). That is a hard **SLA on your pipeline**, not a nice-to-have.
- **Mandatory Data Analytics and Market Intelligence Units** (Clause 8.3.5).
- **Natural justice before adverse classification** — the Directions now expressly require it, following the Supreme Court in *State Bank of India & Ors. v. Rajesh Agarwal & Ors.* (Civil Appeal No. 7300 of 2022, 27 March 2023), which held that borrowers must be given notice and a hearing before their accounts are classified as fraudulent.

**The autonomy consequence is legally forced, not chosen.** An AI system that auto-escalates a borrower onto a watchlist feeding RFA must carry an **auditable pre-decisional record and a right-to-respond step**. L4 (act-then-notify) is acceptable for triage and case-merging; **anything that reaches the borrower is L2 (recommend, human decides)**. Almost no team will have this on a slide, and it is a direct answer to "how would this survive contact with a real bank?"

**DPDP Act 2023 + DPDP Rules 2025.** The Rules were notified **13 November 2025** and gazetted **14 November 2025**. PS-04 unavoidably handles personal data — the RBI EWS list *itself* references promoter/director stake changes and key-personnel resignations. Obligations: itemised notice, purpose-limited retention, reasonable security safeguards, **72-hour breach notification**. And the provision that lands squarely on a credit-scoring model: **Significant Data Fiduciaries owe annual DPIAs, audits and algorithmic-fairness assessments**, plus a DPO. Penalties up to **₹250 crore**.

**BCBS 239.** 14 principles (11 for banks, 3 for supervisors), applying to G-SIBs from 1 January 2016. The brief's "auditable warning trail showing data, trends, calculations and reasoning" *is* BCBS 239 Principle 3 (accuracy and integrity) and Principle 7 (accuracy of risk reports). Frame the lineage graph as a BCBS 239 artefact, not as a nice UI.

**EBA GL 2020/06 §§269–277** already mandates the use of early-warning indicators and watchlists in credit monitoring, with a defined follow-up-and-escalation process on triggered EWIs. This validates the problem — and simultaneously warns that the regulator has *already specified the workflow*, which is a uniqueness risk for the other lanes to weigh.

**Tenancy and residency.** PS-04's tenancy is simpler than PS-17's: one bank, one legal entity per instance, RLS by business unit / branch / RM book with `FORCE ROW LEVEL SECURITY`. Residency is straightforward — run everything on-prem, so the **only** residency decision is LLM egress, which the redaction gateway and the local-model path resolve.

### 5. The deployment slide

```
┌─ Edge ───────────────────────────────────────────────────────────┐
│ SPA + FastAPI/uvicorn behind IIS+ARR on Windows · STATELESS       │
└───────────────────────────────────────────────────────────────────┘
┌─ Batch tier ─────────────────────────────────────────────────────┐
│ ONE DBOS scheduled workflow (nightly_run), stepwise & resumable    │
│ fan-out via DBOS queue, concurrency = cores                       │
│ Scoring workers: pinned model artifact held in memory             │
│ Scales horizontally by adding worker processes                    │
└───────────────────────────────────────────────────────────────────┘
┌─ Intraday tier ──────────────────────────────────────────────────┐
│ pgmq q_signal (vt=60s) · 2 workers · SLO p99 < 30s                │
└───────────────────────────────────────────────────────────────────┘
┌─ STATE (the only stateful component) ────────────────────────────┐
│ Postgres 16 (native Windows installer)                            │
│   borrower_day_features (append-only, point-in-time correct)      │
│   model_versions registry · alerts · dispositions/suppression     │
│   pgmq · DBOS checkpoints · audit trail                           │
│ + streaming replica                                               │
│ Model artifacts: content-addressed on disk, hash-verified on load │
└───────────────────────────────────────────────────────────────────┘
┌─ Ancillary ──────────────────────────────────────────────────────┐
│ LLM gateway: redaction + prompt-hash cache; narratives ONLY for   │
│   alerts surviving triage (~300/night, not 94,500)                │
│ OTel → OTLP with GenAI semantic conventions                       │
└───────────────────────────────────────────────────────────────────┘
```

**SLOs.**

| SLO | Target |
| --- | --- |
| Nightly portfolio scored, explained, triaged, ranked | by **06:00 local**; < 20 min wall clock for 9,000 facilities |
| Intraday signal → updated score | p99 < 30s |
| **Alert volume within configured monthly budget** | **the unusual and correct SLO — alert count is a service objective** |
| RFA-threshold case → reportable packet | < 7 days (regulatory hard deadline, RBI Clause 8.3.3) |
| Reproducibility | any alert bit-identical from `(model_version, feature_snapshot_hash, config_version)` |
| RPO / RTO | 5 min / **30 min** — a missed night is survivable; note the asymmetry vs PS-17, where a missed *contractual notice deadline* is not |

---

## Cross-cutting: the Windows, no-Docker worker landscape

Researched honestly. **Most of the obvious choices are demo-time traps.**

| Option | Installs & runs on bare Windows in 7 days? | Verdict |
| --- | --- | --- |
| **Celery** | Official FAQ: *"Since Celery 4.x, Windows is no longer supported due to lack of resources. But it may still work and we are happy to accept patches."* Windows lacks `fork`, so the prefork pool is gone; `--pool=solo` / `--pool=gevent` are the workarounds. Also needs a broker. | **Trap.** Two unsupported things stacked. Do not. |
| **RQ** | Calls `os.fork()`; Windows raises `AttributeError: module 'os' has no attribute 'fork'`. `SimpleWorker` / `SpawnWorker` exist as workarounds; job timeouts rely on UNIX signals. Needs Redis. | **Trap on Windows.** |
| **Dramatiq** | pip-installable, Python ≥3.10; the `watch` extra is UNIX-only, core is fine. Requires RabbitMQ (recommended) or Redis. | Workable **if** you accept RabbitMQ-on-Windows (Erlang runtime). Middle option. |
| **arq** | asyncio, **Redis-only**. | Blocked by the Redis-on-Windows problem below. |
| **Huey** | pip-installable; SQLite / Postgres / Redis storage. But the docs state: *"Multiprocess support is not available for Windows. The only process start method available on Windows is 'spawn,' which has the downside of requiring the Huey state to be pickled."* Thread and greenlet workers are fine. | Fine for a **single-node thread-pool** demo. Genuinely installs. |
| **APScheduler 3.x** | Pure Python, cross-platform, SQLAlchemy jobstore — but *"job stores must never be shared between schedulers."* It is a **scheduler, not a distributed queue**. v4 is a pre-release carrying an explicit *"do NOT use this release in production!"* warning. | Use 3.x as a **timer only**, in front of a real queue. Never as the queue. |
| **Windows Task Scheduler** | Native, zero install, survives reboot, runs under a service account. No retry-with-backoff, no DLQ, no idempotency, minute granularity. | **Outermost watchdog only** — starts and restarts the durable engine. |
| **Postgres `FOR UPDATE SKIP LOCKED`** | In every Postgres since **9.5 (Jan 2016)**; Postgres has a first-class native Windows installer. ~150 lines of SQL gives claim / lease / retry / DLQ. Caveat: SKIP LOCKED alone does **not** enforce global constraints like "never more than N concurrent" — that needs an advisory lock. | **The honest default and the escape hatch.** Zero new infrastructure. |
| **pgmq** | Supports Postgres 14–18. Critically, it has a **SQL-only install path** — *"use psql to install PGMQ's objects directly into the pgmq schema"* — avoiding a C/Rust extension build, which is exactly the painful part on Windows (PGXS/MSVC). Gives visibility timeouts, archive tables (replayability), FIFO with message groups, SQS/RSMQ API parity. `pg_partman ≤ 4.7.0` only if you want partitioned queues. | **Strong.** SQS semantics with no new process. |
| **SQLite-backed queue** | Works; single file. But WAL plus multi-process writers and Windows file-locking semantics get fragile past one writer. | Fine for a single-process demo; not an answer to "how does this scale". |
| **Temporal** | The CLI dev server is a **single binary published for Windows amd64 and arm64**, runs as one process with zero runtime dependencies, SQLite persistence (in-memory by default), Web UI on :8233. But the docs point production at the self-hosted cluster or Cloud. | Great *demo* durability. **Be honest** that production is a cluster — a CTO will ask, and pretending otherwise loses more than it gains. |
| **Restate** | Single binary — but published release assets cover **macOS (aarch64/x86_64-darwin) and Linux MUSL only. No Windows build.** | **Out.** |
| **DBOS Transact (Python)** | pip-installable **library**, no separate orchestrator; checkpoints workflow and step state into Postgres; queues with per-queue and per-process concurrency limits plus rate limits; cron-scheduled workflows; exactly-once event processing; docs give explicit Windows PowerShell/cmd commands. Recovery = resume from last completed step. | **Best fit.** Durable execution with zero extra daemons on the one OS you are stuck with. |
| **Prefect 3** | Has an **official Windows self-hosting guide**; SQLite default at `%USERPROFILE%\.prefect\prefect.db`; process work pools; NSSM recommended for running as a service. But **multi-worker mode requires PostgreSQL (SQLite unsupported due to locking) *and* Redis** for messaging and concurrency leases. | Viable single-worker; multi-worker reintroduces the Redis problem. |

**The Redis problem, stated plainly:** there is **no official Windows build of Redis**. Microsoft's port was retired and **archived at 3.0.504**; that repository itself points users to Memurai, a commercial Windows-native Redis-compatible datastore. Any stack whose broker is Redis (arq, RQ, Huey-redis, Prefect multi-worker, Celery-redis) inherits a commercial dependency or an eight-year-old archived binary. **Choose a stack that needs no broker at all** — which is exactly what Postgres-as-queue gives you.

**Observability, both problems.** OpenTelemetry SDK → OTLP, using the **GenAI semantic conventions** (CNCF-backed): an `invoke_agent` parent span with `chat` children per LLM call and `execute_tool` children per tool invocation, carrying `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`. This is precisely the "per-loop observability over agent/reasoning loops" the rubric asks for, and using the *standard* rather than a bespoke log format is itself the production-readiness signal. Known limitation to state honestly: the conventions cover telemetry, **not output evaluation or safety scoring** — that layer is yours to build.

---

## Head-to-head verdict for this lane

### Scores

| Criterion | PS-17 | PS-04 |
| --- | --- | --- |
| Genuine scaling difficulty (is there a real problem?) | **9** — retroactive fan-out from 4.4k to 18.2M, needs real technique to bound | 4 — nightly inference is <1s of CPU; nothing is hard |
| Depth of the workers/queue/scheduler answer | **8** — priority classes, cardinality gate, backpressure all have concrete demoable form | 7 — same substrate, simpler shape |
| Failure recovery (and provability) | **10** — the brief *demands* partial-failure recovery; the invariant is falsifiable on stage | 6 — real, but "batch resumes" does not surprise a CTO |
| Security / tenancy difficulty | **8** — clause-level redaction across mutually confidential counterparties | 6 — single-tenant bank, RLS by book |
| Compliance richness & citability | 7 — ASC 606 / SOX / AS 2201 is sharp but singular | **9** — SR 26-2, IFRS 9 SICR, BCBS 239, RBI MD + *Rajesh Agarwal*, DPDP SDF duties |
| Quality of the live production-readiness demo | **10** — kill a worker at 38%, resume, zero duplicate effects | 6 — bit-identical replay is determinism, not resilience |
| **Overall — "how would you productionise this?"** | **8.5** | **6.5** |

### Winner: PS-17, by 2 points

**Why.** Production readiness as this jury scores it is not "did you list AWS services" — it is *is there a hard correctness-under-change problem, did you solve it, and can you prove it live?* PS-17 answers yes three times.

- Its scaling question is **real and non-obvious**: the honest steady-state number is trivial (17 evals/sec), and the interesting number is the retroactive fan-out, which spans four orders of magnitude (4.4k → 18.2M) depending on contract structure. Bounding it requires actual technique — bitemporal range joins, scope-predicate cardinality computed *before* work starts, verdict memoisation by rule-bytecode hash, and a human gate above a declared threshold. A CTO will recognise that as engineering.
- Its failure-recovery requirement is **written into the brief** ("safe recovery when only part of a workflow succeeds", "preventing duplicate requests, duplicate transactions or repeated external actions"). You are not inventing a production story; you are answering the one you were asked.
- Its demo is **falsifiable in 90 seconds** by a hostile observer: kill the worker, watch the backfill resume at 38%, show zero new rows in the `effects` table.

**Where PS-04 genuinely wins**, and it should be conceded openly: the **regulatory story is better** (SR 26-2 superseding SR 11-7 with the agentic-AI carve-out; IFRS 9 SICR creating an obligation from your own capability; RBI's 7-day RFA reporting deadline as a hard pipeline SLA; the *Rajesh Agarwal* natural-justice ruling forcing a specific autonomy level), and the **alert-budget-as-a-knapsack framing** is the most sophisticated single idea in either problem statement. But regulation is not production readiness, and a CTO panel feels the difference between "we understood the rules" and "we made it not break".

### What would change the verdict

- **PS-17 is higher ceiling, lower floor.** Its production story rests entirely on getting bitemporal modelling and effect idempotency right. Get them wrong and the stage demo double-issues credits *in front of the jury* — a public, humiliating failure. PS-04's worst case is a batch job that ran.
- **Team size and shape.** Fewer than 3 engineers, or a team shaky on transactional reasoning and Postgres isolation semantics: **take PS-04.** Its floor is much higher.
- **If PS-04 fully commits to the alert-budget framing** — capacity-constrained ranking, case-merging with the 10–20× collapse, suppression-with-memory, precision@budget as the headline metric — its score rises to ~8, and the gap narrows to half a point. That framing is the one thing that makes PS-04's production story *interesting* rather than merely correct.
- **If PS-17's team cannot demonstrate the chaos moment reliably**, its advantage evaporates entirely, because the demo *is* the argument. Rehearse the kill-and-resume at least ten times before the pitch, from a cold laptop.

---

## Risks and open questions

1. **DBOS is young.** Version-pin it, and **write the pgmq + `SKIP LOCKED` fallback first** as the escape hatch (~150 lines). Do not discover on day 6 that the durable-execution library has a Windows edge case.
2. **Postgres on Windows needs a rehearsal, not an assumption.** Verify `max_connections` under the multi-pool design, confirm the service account can write the WAL archive directory, and **rehearse a full restore** at least once. RPO/RTO claims you have not tested are the easiest thing for a CTO to puncture.
3. **The 5,000 re-evaluations/sec backfill figure is an engineering estimate from Postgres batch-insert rates, not a measured benchmark.** `[UNVERIFIED — measure on the actual demo machine on day 2 and put the measured number on the slide.]` The same caveat applies to the TreeSHAP 1–5 ms/row figure and the 100k rows/sec GBM inference figure — both are standard order-of-magnitude expectations, not citations.
4. **All portfolio parameters (N, M, K, P, F, C) are stated assumptions**, not sourced industry medians. Present them as parameters with the arithmetic visible so the jury can substitute their own — that is more credible than a fake-precise sourced number, and it invites the CTO to do the sum themselves.
5. **No published, peer-reviewed false-positive rate specific to commercial-credit EWS was found.** AML transaction monitoring is used as a proxy throughout and **must be presented as a proxy**. The "eight out of ten early warning signals are false alarms" figure appears only in practitioner commentary and should be labelled as such if used at all.
6. **The IFRS 9 "your EWS creates an accounting obligation" argument is an inference** from the standard's prohibition on relying solely on past-due information. It is a defensible reading and a compelling one, but it is an argument, not a quoted rule — present it as *"an auditor could reasonably take the view that…"*, not as settled fact.
7. **SR 26-2 is four months old** as of the build window (17 April 2026). Confirm no further interagency AI/ML RFI or supplementary guidance has landed before the pitch; the agencies explicitly signalled one was coming.
8. **Whether the demo laptop has cores to spare** for parallel SHAP alongside an LLM gateway and Postgres is unmeasured. Profile on day 2.
9. **Temporal's dev server on stage is a judgement call.** It demos beautifully and is a genuine single Windows binary, but a sharp CTO will ask what production looks like. If you use it, own the answer: *"dev server for the demo, self-hosted cluster in production, and here is the migration path"* — do not let them find it.
10. **pgmq's SQL-only install path was confirmed from its documentation but not executed on Windows in this research.** `[Verify by actually running it on the demo machine on day 1 — it is the load-bearing assumption of the recommended stack.]`

---

## Sources

**Opened and read directly:**

1. Celery FAQ — Windows support. https://docs.celeryq.dev/en/stable/faq.html *(verbatim: "Since Celery 4.x, Windows is no longer supported due to lack of resources.")*
2. Bank Policy Institute — *Getting to Effectiveness: Report on U.S. Financial Institution Resources Devoted to BSA/AML & Sanctions Compliance* (announcement page with figures). https://bpi.com/new-report-demonstrates-aml-regime-not-producing-significant-results/
3. BPI — report landing page. https://bpi.com/getting-to-effectiveness-report-on-u-s-financial-institution-resources-devoted-to-bsa-aml-sanctions-compliance/
4. pgmq — repository. https://github.com/pgmq/pgmq
5. pgmq — documentation (SQL-only install, visibility timeout, archive, Postgres 14–18). https://pgmq.github.io/pgmq/latest/
6. DBOS Transact (Python) — README. https://github.com/dbos-inc/dbos-transact-py/blob/main/README.md
7. DBOS — Quickstart (explicit Windows PowerShell/cmd instructions; Postgres requirement). https://docs.dbos.dev/quickstart
8. Huey — consumer docs (*"Multiprocess support is not available for Windows"*). https://huey.readthedocs.io/en/latest/consumer.html
9. Chris Richardson / microservices.io — Transactional Outbox pattern, incl. duplicate-publication caveat and polling-publisher vs log-tailing. https://microservices.io/patterns/data/transactional-outbox.html
10. Restate — GitHub releases (macOS + Linux MUSL assets only; **no Windows build**). https://github.com/restatedev/restate/releases
11. Prefect 3 — official Windows self-hosting guide (SQLite default, NSSM, multi-worker requires Postgres + Redis). https://github.com/PrefectHQ/prefect/blob/main/docs/v3/how-to-guides/self-hosted/server-windows.mdx
12. PostgreSQL — Row Security Policies (FORCE RLS, BYPASSRLS, referential-integrity covert channel, READ COMMITTED policy-subquery race). https://www.postgresql.org/docs/current/ddl-rowsecurity.html
13. Principles of Chaos Engineering (steady-state hypothesis, vary real-world events, minimise blast radius). https://principlesofchaos.org/
14. **Federal Reserve SR 26-2, "Revised Guidance on Model Risk Management", 17 April 2026** — read in full; supersedes SR 11-7 and SR 21-8; $30bn applicability; "model" excludes deterministic rule-based processes; **footnote 3 excludes generative and agentic AI from scope**. https://www.federalreserve.gov/supervisionreg/srletters/SR2602.pdf
15. OCC Bulletin 2026-13 — Model Risk Management: Revised Guidance (companion to SR 26-2). https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html
16. Economic Laws Practice — *RBI Revised Master Directions on Fraud Risk Management: July 2024* — read in full; EWS/CBS integration (Cl. 8.3), RFA (Cl. 8.3.1), CRILC 7-day reporting (Cl. 8.3.3), Data Analytics Units (Cl. 8.3.5), natural-justice requirement, and the ~42-item EWS list. https://elplaw.in/wp-content/uploads/2024/07/RBI-Revised-Master-Directions-on-Fraud-Risk-Management-July-2024.pdf
17. Dramatiq — installation (Python ≥3.10; RabbitMQ recommended, Redis supported; `watch` extra UNIX-only). https://dramatiq.io/installation.html
18. Temporal — Run a development server (Windows binary, single process, SQLite/in-memory persistence, production points elsewhere). https://docs.temporal.io/develop/run-a-development-server

**Found via search, not opened directly — verify before quoting on stage:**

19. RQ — `os.fork()` on Windows issues. https://github.com/rq/rq/issues/226 · https://github.com/rq/rq/issues/1232 · https://github.com/rq/rq/issues/859
20. RQ — Workers docs (SimpleWorker / SpawnWorker). https://python-rq.org/docs/workers/
21. MicrosoftArchive/redis — archived Windows port (3.0.504), points to Memurai. https://github.com/MicrosoftArchive/redis
22. Memurai — Redis-compatible Windows datastore. https://www.memurai.com/redis-windows
23. APScheduler — user guide, master branch (data stores; v4 *"do NOT use this release in production!"*). https://apscheduler.readthedocs.io/en/master/userguide.html
24. arq — documentation (asyncio + Redis). https://arq-docs.helpmanual.io/
25. Budiu et al., *DBSP: Automatic Incremental View Maintenance for Rich Query Languages*, PVLDB 16(7):1601–1614, 2023 (VLDB 2023 Best Paper). https://www.vldb.org/pvldb/vol16/p1601-budiu.pdf · https://dl.acm.org/doi/10.14778/3587136.3587137 · preprint https://arxiv.org/pdf/2203.16684
26. Postgres-as-a-queue with `FOR UPDATE SKIP LOCKED` (available since 9.5, Jan 2016; advisory-lock caveat for global constraints). https://www.prisma.io/blog/you-dont-need-a-job-queue-postgres-already-has-skip-locked
27. Kulkarni & Michels, *Temporal features in SQL:2011* (SIGMOD Record). https://www.researchgate.net/publication/261845780_Temporal_features_in_SQL2011
28. Bitemporal retroactive-transaction prior art (USPTO). https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11935046 · .../11915236 · .../8812512
29. EBA — *Guidelines on loan origination and monitoring* (EBA/GL/2020/06), §§269–277 early-warning indicators / watch lists and escalation. https://www.eba.europa.eu/sites/default/files/document_library/Publications/Guidelines/2020/Guidelines%20on%20loan%20origination%20and%20monitoring/884283/EBA%20GL%202020%2006%20Final%20Report%20on%20GL%20on%20loan%20origination%20and%20monitoring.pdf
30. BCBS 239 — *Principles for effective risk data aggregation and risk reporting* (Jan 2013; 14 principles; G-SIB application from 1 Jan 2016); BIS implementation newsletter. https://www.bis.org/publ/bcbs_nl36.htm *(canonical standard at https://www.bis.org/publ/bcbs239.pdf — **not opened**)*
31. IFRS 9 impairment — SICR and the 30-days-past-due rebuttable presumption as a backstop; cannot rely solely on past-due information. https://ifrscommunity.com/knowledge-base/ifrs-9-impairment/ · IASB PIR request for information https://www.ifrs.org/content/dam/ifrs/project/pir-9-impairment/rfi-iasb-2023-1-ifrs9-impairment.pdf
32. PCAOB AS 2201 — *An Audit of Internal Control Over Financial Reporting That Is Integrated with An Audit of Financial Statements* (extent of IT involvement in the period-end financial reporting process). https://pcaobus.org/oversight/standards/auditing-standards/details/AS2201
33. ASC 606 variable consideration and the constraint (SLA credits, penalties, liquidated damages as variable consideration). https://www.revenuehub.org/article/variable-consideration-constraint
34. DPDP Rules 2025 — notified 13 Nov 2025, gazetted 14 Nov 2025 (PIB press release). https://www.pib.gov.in/PressReleasePage.aspx?PRID=2190014 · summary PDF https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf
35. DPDP Act 2023 + Rules 2025 — Significant Data Fiduciary duties (annual DPIA, audit, algorithmic fairness assessment, DPO); penalties to ₹250 crore. https://www.ey.com/en_in/insights/cybersecurity/decoding-the-digital-personal-data-protection-act-2023
36. OpenTelemetry — GenAI observability and semantic conventions (`invoke_agent`, `execute_tool`, `gen_ai.usage.*`). https://opentelemetry.io/blog/2026/genai-observability/
37. World Commerce & Contracting — contract value erosion (9.2% of annual revenue, later ~8.6%; best performers ~3%, worst >20%). https://www.worldcc.com/resource/Stopping-the-Leak-The-value-of-contracts.html · https://www.legaldive.com/news/contract-value-erosion-CLM-software-contracts-contracting/688790/
38. RBI Master Directions on Fraud Risk Management, July 2024 — secondary summaries corroborating scope and the rescission of 36 circulars. https://www.scconline.com/blog/post/2024/07/17/rbi-revises-master-directions-on-fraud-risk-management-in-regulated-entities-legal-news/ · https://vinodkothari.com/wp-content/uploads/2024/07/FRM.pdf

**Weakly sourced — label explicitly if used:**

39. AML false-positive rates of 85–95% (industry/vendor commentary, not peer-reviewed). https://www.flagright.com/post/understanding-false-positives-in-transaction-monitoring · https://finance.yahoo.com/news/hidden-cost-aml-95-false-134601048.html
40. *"Roughly eight out of ten early warning signals turned out to be false alarms"* for credit EWS — practitioner commentary only. `[UNVERIFIED — no peer-reviewed or regulator source found for a commercial-credit-specific EWS false-positive rate.]`
