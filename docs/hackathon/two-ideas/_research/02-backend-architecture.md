# Backend architecture and engineering depth — comparative research

> Lane: backend architecture, data modelling, state, correctness under change.
> Scope rule: clean-room. Judged on the two briefs alone.
> Constraints assumed throughout: **Windows demo box, no Docker, 7 days, synthetic data, live demo to CTOs.**

---

## Executive answer

- **PS-17 is a genuine distributed-systems problem wearing a legal-tech costume. PS-04 is a
  forecasting problem wearing a banking costume.** The backend depth gap is not close: **PS-17
  scores 9/10, PS-04 scores 5.5/10** on this lane.
- PS-17's national-finale inject — *"an amendment changes an SLA threshold after potential
  breaches were flagged; re-evaluate each event using the correct effective version"* — is
  **literally the textbook motivating example for bitemporal modelling** (Fowler's Sally-salary
  case is the same shape: an action was already taken on a belief that later turned out to be
  retroactively wrong). The brief hands you the hardest correct answer for free.
- The winning move on PS-17 is **not** "we used a bitemporal database." It is
  **bitemporal state + how-provenance + targeted incremental re-evaluation**: when a retroactive
  assertion lands, compute the *valid-time delta*, then use per-verdict input lineage to
  re-evaluate **exactly the affected events and nothing else** — and prove it by showing the
  re-evaluation count (3 of 1,140), not the wall-clock time. This is provenance semirings
  (Green/Karvounarakis/Tannen, PODS 2007) and incremental view maintenance (DBSP, VLDB 2023) in
  miniature, and no other team will do it — everyone else will "reprocess everything with the
  latest rules," which is *wrong*, and demonstrably wrong on events that predate the amendment.
- **On bare Windows the durable-execution story is real, not hand-waving.** The Temporal CLI ships
  a **single Windows amd64 binary** that runs a full server + Web UI with SQLite persistence and
  zero runtime dependencies; the Python SDK ships Windows wheels. PostgreSQL has a first-party
  native Windows installer, which unlocks **DBOS Transact** (durable workflows as a *library*, no
  orchestration server). Both are installable in under an hour. Anything Docker-first (XTDB v2,
  Hatchet, Temporal's production self-host guide) is out.
- **PostgreSQL 18 (Sept 2025) added `PRIMARY KEY … WITHOUT OVERLAPS` and `FOREIGN KEY … PERIOD`**
  — SQL:2011 application-time constraints, in a database with a native Windows installer. It does
  **not** give you system-versioning, so you hand-roll transaction time. That split is a *feature*
  for this pitch: the interesting half is the half you own. **MariaDB is the one mainstream,
  natively-Windows-installable engine with SQL:2011 bitemporal tables built in** (system-versioned
  + application-time + explicitly documented "bitemporal tables") — a credible fallback, and a
  strong slide either way.
- PS-04 does have a real hard modelling problem, but it is **point-in-time correctness / temporal
  leakage**, not bitemporality — and it is *invisible*. If you get it wrong your demo looks
  **better**, not worse. That is a terrible property for a 5-minute pitch: the depth is unshowable
  and the failure mode is rewarded.
- PS-04's defensible modelling choice in 7 days is a **discrete-time hazard model** (person-period
  explosion + regularized logistic regression, one model, horizon indicators) — *not* Cox
  proportional hazards (wrong shape for calendar-anchored 30/60/90 with time-varying covariates)
  and *not* DeepHit (needs data volume you will not have, and you cannot calibrate it on synthetic
  labels you generated yourself). Pair with **reliability diagram + Brier score** and **conformal
  intervals**; treat **SHAP as suspect** for correlated financial ratios and say so out loud.
- PS-04's fatal honesty problem for a CTO jury: **your labels come from your own synthetic
  generator.** Any AUC you report measures how well the model recovered your simulator, not
  covenant risk. The mature answer (show calibration, not AUC; show a data-generating-process
  diagram; report base rates) is correct and also *deflating* on stage.
- **Where each gets boring:** PS-17's dull half is document extraction and CRUD-workspace
  plumbing — commodity, and every team will have it. PS-04's dull half is *everything except the
  model*: ratio arithmetic is spreadsheet-grade, there is no concurrency story, no long-running
  state, no partial-failure story, no idempotency requirement, and "orchestration" is a nightly
  scheduled job.
- **The one visual:** PS-17 — a **two-slider bitemporal time cursor** (valid time × knowledge
  time) above the event ledger; drag knowledge-time back before the amendment and the verdicts
  re-colour live. One interaction proves the data model, the provenance fan-out and audit replay
  simultaneously. PS-04 — a hazard term-structure chart with a calibration curve beside it. Both
  are good; only one is a *mechanism* rather than a *chart*, and CTOs have seen the chart.

---

## PS-17: Contract Obligation, SLA & Commercial Leakage Monitor — backend analysis

### 0. Sub-problem decomposition (the narrative spine)

The brief decomposes cleanly into five backend sub-problems. Name them and ship one artefact each:

| # | Sub-problem | Brief clause it answers | Named solution |
| --- | --- | --- | --- |
| SP-1 | Represent late, corrected and conflicting versions without losing earlier evidence | §04 bullet 1 | **The Bitemporal Obligation Ledger** |
| SP-2 | Re-evaluate only what actually changed, correctly, when an amendment lands retroactively | Finale inject; §01 "targeted re-evaluation rather than silently preserving an outdated conclusion" | **Provenance-Directed Re-evaluation** |
| SP-3 | Long-running state, deadlines, no duplicate external actions, safe partial-failure recovery | §04 bullets 3 and 6 | **Durable Case Execution + Effects Ledger** |
| SP-4 | Next-best action from state, deadlines, dependencies, authority, expected value | §04 bullet 2 | **The Action Scorer** (decision-theoretic, not LLM-freestyle) |
| SP-5 | Competing interpretations with supporting/weakening evidence, uncertainty not hidden | §04 bullet 4 | **The Hypothesis Board** (bipolar argumentation) |

Note that SP-1, SP-2, SP-3 are all *stated requirements*, not embellishments. This is unusual:
the problem statement is already asking for the hard engineering. That is the single strongest
argument for PS-17 in this lane.

---

### 1. The domain / data model

#### 1.1 Why bitemporality, precisely

Two independent time axes ([Snodgrass 1999](https://archive.org/details/developingtimeor0000snod);
[Kulkarni & Michels, *Temporal features in SQL:2011*, ACM SIGMOD Record 41(3)](https://cs.ulb.ac.be/public/_media/teaching/infoh415/tempfeaturessql2011.pdf)):

- **Valid time** (SQL:2011: *application-time period*) — when a fact is true **in the world**.
  The SLA threshold was 4h from 1 Jan, 6h from 1 Mar.
- **Transaction time** (SQL:2011: *system-time period*) — when the database **believed** it.
  We believed "4h from 1 Mar" until 10 Apr; from 10 Apr we believe "6h from 1 Mar."

Martin Fowler's [*Bitemporal History*](https://martinfowler.com/articles/bitemporal-history.html)
gives the decision rule almost verbatim for this brief: bitemporality earns its complexity
**exactly when an action has already been taken on a belief that is later retroactively
corrected**. His words: *"If we can avoid using bitemporal history, then that's usually
preferable as it does complicate a system quite significantly."*

PS-17 does not let you avoid it. The finale inject specifies that breaches were **already
flagged** before the amendment arrives, and §04 requires the system to "represent late, corrected
or conflicting versions **without losing earlier evidence**" while §02 keeps commercial settlement
human-owned — i.e. real-world actions (credit notes, notices) have already left the building.
That is Fowler's trigger condition, stated by the examiner.

**The distinction to make on stage, because it is the one everyone gets wrong:** an *audit log*
is not bitemporality. An audit log tells you the row changed. It does not let you *query* the
world as you understood it on 1 April. Fowler files these as separate patterns
([Temporal Patterns](https://martinfowler.com/eaaDev/timeNarrative.html)) precisely because the
audit log is the cheap option that fails the "reconstruct what the system knew" requirement in
§04 bullet 7.

#### 1.2 Concrete schema

Two tables per temporal entity. An **append-only assertion log** (the truth) and a
**current-belief projection** (the thing you constrain and query fast). Postgres 18 syntax:

```sql
-- (A) Append-only. Never UPDATEd except to close `asserted`. This is the evidence.
CREATE TABLE obligation_assertion (
  assertion_id     uuid PRIMARY KEY,
  obligation_id    uuid        NOT NULL,
  contract_id      uuid        NOT NULL,
  metric           text        NOT NULL,      -- 'P1_RESOLUTION_HOURS'
  comparator       text        NOT NULL,      -- '<='
  threshold        numeric     NOT NULL,      -- 4.0
  credit_pct       numeric     NOT NULL,      -- 5.0 % of monthly charges
  effective        tstzrange   NOT NULL,      -- VALID TIME: when the clause governs
  asserted         tstzrange   NOT NULL,      -- TRANSACTION TIME: when we believed it
                                              --   [t_learned, infinity) while current
  -- provenance: §04 bullet 5 demands explicit separation of fact / inference / input / decision
  fact_class       text        NOT NULL       -- RECORDED_FACT | AI_INFERENCE | USER_INPUT
                     CHECK (fact_class IN ('RECORDED_FACT','AI_INFERENCE','USER_INPUT',
                                           'AUTOMATED_ACTION','HUMAN_DECISION')),
  source_doc_id    uuid,                      -- the PDF
  source_span      int4range,                 -- character offsets -> click-to-highlight
  source_page      int,
  extracted_by     text,                      -- model id + prompt hash, NULL when human
  confidence       numeric,
  superseded_by    uuid REFERENCES obligation_assertion(assertion_id)
);

-- (B) Current belief. Exactly the rows whose `asserted` is still open.
--     PG18 enforces the uni-temporal invariant the domain actually has:
--     one governing threshold per obligation per instant of valid time.
CREATE TABLE obligation_effective (
  obligation_id  uuid      NOT NULL,
  assertion_id   uuid      NOT NULL REFERENCES obligation_assertion(assertion_id),
  threshold      numeric   NOT NULL,
  credit_pct     numeric   NOT NULL,
  effective      tstzrange NOT NULL,
  PRIMARY KEY (obligation_id, effective WITHOUT OVERLAPS)   -- PostgreSQL 18
);
```

`WITHOUT OVERLAPS` on `PRIMARY KEY`/`UNIQUE`, and `FOREIGN KEY … PERIOD`, landed in
**PostgreSQL 18.0 (25 Sept 2025)** — [release notes](https://www.postgresql.org/docs/release/18.0/).
This matters for the pitch: *the database itself* now rejects two governing thresholds at the same
instant. Before PG18 that was a hand-rolled exclusion constraint or, on most teams, nothing at all.
Every service event, credit, notice, invoice line and owner action gets the same
`effective` / `asserted` / `fact_class` / `source_span` treatment.

Evidence rows carry an interval, not an instant, so **Allen's interval algebra**
([Allen, CACM 26(11), 1983](https://dl.acm.org/doi/10.1145/876638.876639) — thirteen exhaustive,
mutually exclusive relations: precedes, meets, overlaps, starts, during, finishes, equals and
converses) is the right vocabulary for "which obligation version governed this incident" when the
incident itself spans a version boundary. That case — an outage that *straddles* the amendment
date — is the edge case worth pre-planting in the synthetic corpus, because it is the one where
"latest version wins" and "version at incident start wins" and "pro-rate across versions" give
three different money answers.

#### 1.3 The amendment inject, handled correctly — step by step

This is the demo. Walk it exactly:

**Given.** Contract C-101, obligation O-7: P1 resolution ≤ **4h**, effective `[2026-01-01, ∞)`,
asserted `[2026-01-05, ∞)`, sourced to page 14 span 2210–2288 of the signed PDF. 1,140 service
events ingested Jan–Apr. Three flagged breaches in March: **E1** (4.5h), **E2** (7.0h), **E3**
(5.2h, occurred **2026-02-20**). A credit note for E1 has **already been issued to the customer**.

**t₃ = 2026-04-10.** Amendment A-2 arrives. Signed 2026-02-15. Effective **2026-03-01**.
Threshold → **6h**.

1. **Ingest as a new assertion, never an update.** Close the old belief in *transaction* time only
   for the affected *valid*-time portion: set `upper(asserted) = t₃` on the 4h row and re-assert
   `4h, effective [2026-01-01, 2026-03-01)` with `asserted [t₃, ∞)`; insert
   `6h, effective [2026-03-01, ∞), asserted [t₃, ∞)`, `fact_class = RECORDED_FACT`,
   `source_doc_id = A-2`. **Nothing is destroyed.** The query
   *"what did we think the SLA was on 2026-03-15, as we understood the world on 2026-04-01?"*
   still returns **4h** — which is the only thing that justifies the credit note you already sent.
   In SQL:2011 vocabulary this is `FOR VALID_TIME AS OF '2026-03-15' FOR SYSTEM_TIME AS OF
   '2026-04-01'`; XTDB exposes exactly that syntax
   ([XTDB bitemporality](https://v1-docs.xtdb.com/concepts/bitemporality/)), MariaDB exposes the
   system half as `FOR SYSTEM_TIME AS OF`
   ([MariaDB system-versioned tables](https://mariadb.com/kb/en/system-versioned-tables/)), and on
   PG18 you write it as a two-predicate `WHERE` over the two ranges.

2. **Compute the retroactive delta.** Diff the obligation timeline before and after the assertion.
   Result: a single valid-time interval `[2026-03-01, ∞)` on obligation O-7. This is a range
   difference, not a scan.

3. **Fan out through provenance, not through a full recompute.** Every breach verdict row carries
   the identifiers of the inputs it was derived from:
   `derived_from = {assertion_id: a1, event_id: e, sla_record_id: s, invoice_id: i}`.
   Select verdicts where `a1` is in the invalidated assertion set **and** the event's own interval
   intersects the delta. That returns **E1 and E2 only — 2 of 1,140.** This is
   *how-provenance*: [Green, Karvounarakis & Tannen, *Provenance Semirings*, PODS
   2007](https://web.cs.ucdavis.edu/~green/papers/pods07.pdf) showed that annotating base tuples
   with semiring elements and propagating them through relational algebra generalises
   why-provenance, bag semantics and probabilistic/incomplete databases as one algorithm — and
   the annotation is exactly the "which facts is this conclusion standing on" structure §04
   bullet 5 and bullet 7 are asking for. You do not need the full semiring; you need the
   *polynomial-of-inputs* idea, which is one JSONB column and one GIN index.
   The complexity claim to make is the incremental-view-maintenance one: **recompute cost
   proportional to the size of the change, not the size of the data**
   ([DBSP, VLDB 2023](https://www.vldb.org/pvldb/vol16/p1601-budiu.pdf); lineage of
   differential dataflow, McSherry et al., CIDR 2013).

4. **Re-evaluate the two, and show the third staying put.** E1 (4.5h, 12 March) flips
   **breach → compliant**. E2 (7.0h, 20 March) stays a breach but its credit recalculates.
   **E3 (5.2h, 20 February) is untouched** — it predates the amendment's effective date, so it
   is still governed by the 4h version, and it is still a breach. **This is the money shot.**
   Every naive implementation ("reload rules, rescore the portfolio") silently clears E3 and is
   *wrong*. Put E3 on screen and let the jury notice. It costs nothing and it is the single most
   convincing 10 seconds available in either problem statement.

5. **Reconcile the action that already left the building.** The E1 credit note was **sent**. You
   cannot un-send it. The correct behaviour is not to rewrite history but to emit a **compensating
   obligation**: a `reversal_required` case, priced, routed to human approval — because §02 puts
   "material commercial settlement decisions" in human hands. This is the saga compensation story
   ([microservices.io: Saga](https://microservices.io/patterns/data/saga.html)) surfacing as a
   *domain* event, which is far more convincing than a slide about sagas.

6. **Everything above is itself asserted**, with `fact_class` and actor. Audit replay is a query
   at a `(valid_time, assertion_time)` pair, not a log-file grep. That is §04 bullet 7 satisfied
   by construction rather than by feature.

#### 1.4 What to actually run on Windows

| Option | Bitemporal support | Native Windows, no Docker? | Verdict for 7 days |
| --- | --- | --- | --- |
| **PostgreSQL 18 + hand-rolled transaction time** | App-time constraints native (`WITHOUT OVERLAPS`, `PERIOD` FK); system time is yours to build | **Yes** — first-party [Windows installers](https://www.postgresql.org/download/windows/) (EDB-packaged) | **Recommended.** You own the interesting half; PG18 enforces the boring half; and PG unlocks DBOS/procrastinate for SP-3. |
| **MariaDB** | **Full SQL:2011**: system-versioned, application-time, and explicitly [bitemporal tables](https://mariadb.com/docs/server/reference/sql-structure/temporal-tables/bitemporal-tables) | **Yes** — native MSI | Strong fallback and a great slide, but you inherit its temporal semantics instead of demonstrating that you understand them. |
| **XTDB v2** | Best-in-class; bitemporal by default, `FOR VALID_TIME` / `FOR SYSTEM_TIME`, Postgres wire protocol | **Docker-first.** [Docs](https://docs.xtdb.com/intro/what-is-xtdb.html) lead with Docker/JVM; release notes reference Docker images. Standalone-uberjar path **not verified** | **Cite it as prior art, do not bet the demo on it.** Naming it (and Datomic) shows you know the reference implementations. |
| **Datomic** | `asOf` / `history` on an accumulate-only log; [`:db/txInstant`](https://docs.datomic.com/transactions/model.html) is transaction time — note it is *uni*-temporal by default, valid time is yours | JVM; licensing/ops overhead | Reference only. Also a useful honesty point: Datomic is often *miscalled* bitemporal. |
| **SQLite** | Nothing native; all hand-rolled | Yes, trivially | Only if you also accept SP-3's constraints below. See §2.4. |

---

### 2. State, orchestration and correctness under partial failure

§04 bullet 3 and bullet 6 are the two hardest lines in either brief: long-running state, deadlines,
cross-system dependencies, **no duplicate external actions**, permission checks, retries, timeouts,
idempotency, and **safe recovery when only part of a workflow succeeds**.

#### 2.1 The five candidate patterns, and what each actually buys you

- **Durable execution / workflow-as-code.** The workflow's *program counter and local variables*
  are the persisted state. Temporal persists an ordered **event history** and reconstructs state by
  **replaying workflow code from the beginning**; hence workflow code must be deterministic — no
  `datetime.now()`, no `random()`, no un-recorded I/O; side effects live in Activities, whose
  results are recorded once and **reused, not recomputed, on replay**
  ([Temporal: Workflows](https://docs.temporal.io/workflows)). This buys you "the process survives
  the crash" *for free*, which is precisely §04 bullet 6.
- **Saga.** Long-running business transaction as a sequence of local transactions each with a
  compensating action ([microservices.io](https://microservices.io/patterns/data/saga.html)).
  Buys you the *semantics* of "only part of the workflow succeeded" — the credit note that cannot
  be un-sent, only offset.
- **Transactional outbox.** Atomically update the DB and record the intent to send, in one local
  transaction; a relay publishes it. Solves the dual-write problem. Note the relay **may publish
  more than once** (crash after publish, before ack), so *outbox implies consumers must be
  idempotent* ([microservices.io](https://microservices.io/patterns/data/transactional-outbox.html)).
- **Idempotency keys.** Client-generated unique value the server uses to recognise retries;
  now an IETF HTTPAPI working-group draft,
  [`draft-ietf-httpapi-idempotency-key-header`](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/)
  ("MUST be unique and MUST NOT be reused with another request with a different request payload").
  This is the *only* mechanism that actually delivers "prevent duplicate requests, duplicate
  transactions or repeated external actions." Durable execution alone gives you at-least-once.
- **Event sourcing + CQRS.** State as an append-only event log with read models projected off it.
  Excellent fit for §04 bullet 5 (versioned state, provenance) — but the real cost is *versioning*:
  events written years ago must be readable by today's code, which is the entire subject of Greg
  Young's [*Versioning in an Event Sourced System*](https://www.infoq.com/news/2017/07/versioning-event-sourcing).
  In 7 days this is a **conceptual** win and a **schedule** risk if applied to the whole system.

#### 2.2 The design that satisfies the brief

Do not pick one. Compose them, and say why:

```
Bitemporal ledger (§1)  →  the FACTS
        │
        ▼
Durable case workflow    →  the PROCESS       (survives crash/restart; deadlines as durable timers)
        │
        ▼
Effects Ledger           →  the EXTERNAL WORLD
   external_effect(
     effect_key   text PRIMARY KEY,   -- idempotency key, DERIVED not random:
                                      --   hash(case_id, step, obligation_assertion_id, payload)
     kind         text,               -- SEND_NOTICE | ISSUE_CREDIT | RAISE_TICKET
     state        text,               -- PENDING | SENT | FAILED | COMPENSATED
     approved_by  text,               -- NULL unless the authority gate passed
     request_hash text,               -- detect same key + different payload -> hard error
     response     jsonb )
```

Three properties worth stating explicitly to a CTO, because they are what separates this from a
demo script:

1. **The idempotency key is derived from the assertion that justified the action**, not randomly
   generated. Consequence: after the amendment, re-evaluating E2 produces a *different* key,
   because the justifying assertion changed — so the system correctly recognises "this is a new,
   different action about the same event" rather than either silently re-sending the old one or
   silently suppressing the new one. This is the join between §1 and §2 and it is the sort of
   detail that wins engineering-depth points.
2. **Same key + different payload is a hard error**, per the IETF draft. Show the error.
3. **Approval is a durable signal, not a callback.** A workflow blocked on human approval is
   *waiting on a durable timer + signal*, so the box can be rebooted mid-demo. **Reboot the demo
   machine on stage.** Nothing else in either problem statement offers a stunt that cheap and that
   convincing. (Rehearse it; budget the cold-start.)

#### 2.3 What actually installs on bare Windows in 7 days

| Tool | Runs on Windows without Docker? | Evidence | Verdict |
| --- | --- | --- | --- |
| **Temporal CLI dev server** | **Yes.** Single binary, `windows_amd64` archives on [GitHub releases](https://github.com/temporalio/cli/releases) and [temporal.download](https://docs.temporal.io/cli/setup-cli); `temporal server start-dev` runs server + Web UI as one process with **zero runtime dependencies**, SQLite persistence (in-memory by default, `--db-filename` to persist) | Docs: [run a dev server](https://docs.temporal.io/develop/run-a-development-server) | **Best-in-class demo asset.** The Temporal Web UI *is* a free "backend depth" visual. Caveat honestly: dev server ≠ the production self-host topology. |
| **Temporal Python SDK** | **Yes** — `pip install temporalio`, PyPI wheels cover major platforms incl. Windows, Python 3.10+ | [sdk-python](https://github.com/temporalio/sdk-python) | Ship it. |
| **DBOS Transact (Python)** | **Yes**, given Postgres. Library, **no separate orchestration server**; workflow state and step history live in your Postgres; interrupted workflows resume from the last completed step | [dbos-transact-py](https://github.com/dbos-inc/dbos-transact-py), [architecture](https://docs.dbos.dev/architecture) | **Strong lightweight alternative.** Lowest ceremony; fewest moving parts; no extra process to babysit on stage. |
| **PostgreSQL 18** | **Yes** — first-party [Windows installers](https://www.postgresql.org/download/windows/) | — | Prerequisite for DBOS/procrastinate. |
| **procrastinate** | **Yes**, given Postgres. Python 3.10+ distributed task queue on **PostgreSQL 13+**; no Redis/RabbitMQ broker; retries, locks, periodic tasks | [PyPI](https://pypi.org/project/procrastinate/) | Good for the *worker* tier if you skip full durable execution. |
| **Prefect 3** | **Yes** — pip-installable, [documented Windows install](https://docs.prefect.io/v3/get-started/install), SQLite backend for local use | — | **Careful:** this is *orchestration*, not durable execution. No deterministic replay. Do not claim otherwise in front of a CTO. |
| **Restate** | **Unverified for Windows.** Single-binary story exists; Windows support not confirmed in what I could open | — | `[UNVERIFIED — no source found]`. Cite conceptually only. |
| **Hatchet** | Postgres-backed but **Docker/Go-engine-first** deployment | [hatchet-v1](https://github.com/hatchet-dev/hatchet-v1) | Out for this box. |
| **XTDB v2 / Kafka / Kubernetes** | Docker-first | — | Out. |

**Recommendation:** **DBOS Transact on PostgreSQL 18** as the default (one database, no extra
server, lowest failure surface on an unfamiliar demo machine), with **Temporal dev server** as the
upgrade if — and only if — you want the Temporal Web UI on the projector as your engineering-depth
visual. Both are honest. Decide by day 2, not day 5.

#### 2.4 The SQLite caveat, stated before the jury states it

If you go SQLite: **SQLite permits one writer at a time, globally across the file, and WAL mode
does not change that** — WAL lets readers and writers proceed concurrently but concurrent *write*
transactions still serialise or return `SQLITE_BUSY`. For a single-node demo with a handful of
workers this is fine and you should say so plainly; for the "how would you scale this" question
the answer is "the write path is a single-node bottleneck by design; production is Postgres, and
the schema is unchanged because the temporal logic is in SQL, not in the engine." That answer
scores. "SQLite scales fine" does not.

---

### 3. The reasoning / decision layer

Two distinct requirements that teams routinely collapse into one LLM prompt. Keep them separate.

#### 3.1 SP-4 — "next-best action … from state, deadlines, dependencies, authority and expected value"

The brief has written a **decision-theoretic** specification, not a prompt. "Expected value" is
the giveaway. The canonical framing is Ronald Howard's
[*Information Value Theory* (IEEE Trans. SSC, 1966)](https://www.semanticscholar.org/paper/Information-Value-Theory-Howard/a7b3c2a88ca459d50010a33db8c2f113f1323e0c):
the value of an information-gathering act is the **difference in expected payoff with and without
it** — you should pay at most that much to resolve the uncertainty. Howard's specific insight, that
you cannot rank information by probability alone without considering consequences, is exactly the
answer to "which evidence should I chase next."

**Build this, not an agent loop.** Every candidate action is a typed record and gets a score:

```
score(a) = [ P(claim succeeds | current evidence) × recoverable_value(a)
             + VOI(a)                                    # expected reduction in decision loss
             - cost(a) ]
           × deadline_urgency(a)                          # notice windows, cure periods
           × authority_gate(a)                            # 0 if actor lacks authority -> not selectable
           × dependency_gate(a)                           # 0 if prerequisites unmet
```

Then the LLM's job is narrowed to what LLMs are actually good at: **proposing candidate actions and
extracting the parameters**, while *selection* is a deterministic, inspectable, replayable
function of typed state. This gives you three things a jury of CTOs will reward:
(a) the same state always yields the same recommendation — **it replays**;
(b) you can show the **runner-up** actions and why they lost, which is the explainability
requirement met by arithmetic rather than by narration;
(c) authority is a hard multiplicative gate, so "the system cannot recommend an action the current
role may not take" is a structural property, not a prompt instruction.
The generic lesson from the verifiable-agent literature applies: raw LLM reasoning as a policy is
expensive, inconsistent and non-verifiable, which is why safety-policy work pushes the decision
into an explicit rule/constraint layer rather than the model. `[UNVERIFIED — I saw this framing
only in search-result summaries of the ShieldAgent line of work and could not open a primary
source; the argument stands on its own and should be made without a citation.]`

Deliberately **not** an MDP/POMDP. Tempting, unbuildable in 7 days, and unshowable.

#### 3.2 SP-5 — "competing interpretations … which evidence supports, weakens or changes each"

The brief has written the definition of a **bipolar argumentation framework**. Dung's
[*On the acceptability of arguments…* (Artificial Intelligence 77(2):321–357, 1995)](https://dl.acm.org/doi/10.1016/0004-3702%2894%2900041-X)
established abstract argumentation with an **attack** relation and admissibility semantics;
[bipolar frameworks (Cayrol & Lagasquie-Schiex, 2005)](https://link.springer.com/chapter/10.1007/11518655_33)
add an independent **support** relation — which is literally "supports, weakens" from §04 bullet 4.

For presentation, **Toulmin's layout** (claim / grounds / warrant / backing / qualifier / rebuttal)
is the better *UI*: it is explicitly the model used in AI-and-law because its structure is
intuitive to non-logicians, and it makes defeasibility visible
([Verheij, *The Toulmin Argument Model in Artificial Intelligence*](https://www.ai.rug.nl/~verheij/publications/pdf/toulmin2009.pdf);
[argumentation-based explainability for legal AI](https://arxiv.org/html/2510.11079)). Since PS-17
keeps *legal interpretation* human-owned, a Toulmin-shaped card — "Claim: breach of §7.2 ·
Grounds: E2, 7.0h · Warrant: obligation assertion a₁ · Qualifier: 0.82 · Rebuttal: force-majeure
notice N-3, unverified" — is the right handoff artefact to a human lawyer.

And the third piece, which almost nobody will name: when the amendment arrives, the system is
performing **belief revision** — incorporating new information that contradicts held beliefs while
retaining as much as possible. That is AGM (Alchourrón, Gärdenfors & Makinson, *On the Logic of
Theory Change*, JSL 1985; see the
[SEP entry](https://plato.stanford.edu/entries/logic-belief-revision/)), and the founding
motivation for that work was literally **change in legal codes**. Being able to say "the amendment
is a revision, not an update; the ledger is our recovery/contraction structure, and prior
conclusions are retracted rather than erased" is the sentence that tells a technical jury you
understand the problem class rather than the ticket.

**Buildability:** implement bipolar argumentation as a small directed graph with support/attack
edges and one fixed semantics (grounded extension) — a few hundred lines, no library. Do **not**
attempt preferred/stable semantics enumeration; it is NP-hard territory and adds nothing on stage.

---

### 4. Where PS-17 gets deep, and where a strong engineer gets bored

**Deep (real, non-obvious engineering):**
- Bitemporal correctness with already-taken external actions (§1.3) — genuinely hard, textbook-hard.
- Provenance-directed selective re-evaluation (SP-2) — the highest-value, lowest-competition idea in either brief.
- Derived idempotency keys crossing the temporal boundary (§2.2) — subtle, correct, demonstrable.
- Straddling intervals + Allen relations for events spanning a version boundary — a real correctness fork with three defensible answers, i.e. exactly the kind of thing a CTO probes.
- Compensation semantics for irreversible commercial acts.

**Boring (commodity; every team will have it; do not over-invest):**
- **PDF → obligations extraction.** LLM + spans. It is table stakes and it is where teams burn day 1 through 4. Timebox it hard; hand-author the synthetic corpus so extraction is a 2-day job, not a 5-day one.
- The CRUD workspace, role menus, mock connectors, notification stubs.
- "We used RAG over the contract." Nobody on a CTO panel will be impressed.
- Any *general* rules engine. The obligations you need are a small typed DSL: metric, comparator, threshold, window, credit formula. Building a general rules engine is a week you do not have and depth the jury cannot see.

**The failure mode to name and avoid:** a beautiful ingestion pipeline and a shallow temporal
model. If the amendment inject is handled by "re-run the rules," you have built what everybody
built and lost the one point the examiner explicitly put on the table.

---

### 5. The "explainable with a visual" test — PS-17

**The one screen: the Bitemporal Time Cursor.**

- **Top:** two horizontal sliders sharing an x-axis of dates — **Valid time** ("as it was true")
  and **Knowledge time** ("as we knew it"). Fowler's two axes, made physical.
- **Middle:** the obligation O-7 timeline, rendered as coloured bands (4h band, 6h band) that
  **change shape as you drag the knowledge-time slider**. Drag to 1 April: one unbroken 4h band.
  Drag to 11 April: the band splits at 1 March.
- **Bottom:** the event ledger, 1,140 rows, verdict-coloured. As the knowledge slider crosses
  10 April, **exactly two rows re-colour** — with a badge reading `re-evaluated: 2 / 1,140` and
  E3 conspicuously *not* changing.
- **Side panel:** click any verdict → its provenance polynomial, i.e. the assertion ids, spans and
  source pages it stands on, with `fact_class` chips (RECORDED_FACT / AI_INFERENCE / HUMAN_DECISION).
- **Toast:** `Effect ce_9f2… already SENT under superseded assertion a1 → compensating case opened,
  awaiting Credit Controller approval.`

One drag proves: the bitemporal model, targeted re-evaluation, provenance, the correctness of *not*
re-evaluating E3, and the compensation path. That is five backend claims in one gesture, with no
narration required.

**Free second visual:** if you run Temporal, its Web UI shows the live workflow event history and
pending timers. Reboot the machine; the workflow resumes. Cost: zero engineering.

---

## PS-04: AI-Powered Dynamic Covenant Monitoring & Early Warning — backend analysis

### 0. Sub-problem decomposition

| # | Sub-problem | Named solution | Backend depth |
| --- | --- | --- | --- |
| SP-1 | Covenant definitions, thresholds, testing frequency, exceptions, waivers | **Covenant Term Sheet** (small bitemporal model) | Moderate |
| SP-2 | Feature construction without leaking the future | **Point-in-Time Feature Store** | **High but invisible** |
| SP-3 | Breach probability at 30/60/90 days | **Discrete-Time Hazard Model** | High (statistical, not systems) |
| SP-4 | Driver attribution | **Contribution panel with correlation caveats** | Moderate, and treacherous |
| SP-5 | Portfolio ranking + intervention + audit trail | **Alert ledger** | Low |

### 1. Does bitemporality matter here?

**Partially — and less than PS-17, in a way worth being precise about.**

It *does* matter in two specific places, and a good team will say so:

- **Financial statements get restated.** Q2 leverage as reported in August ≠ Q2 leverage as
  restated in November. If your model trains on restated figures but scores on
  as-first-reported figures, you have manufactured a skew you will never see in your metrics.
  That is transaction time, exactly.
- **Covenant terms are amended and waived.** A waiver granted in March for the Q1 test is a
  retroactive valid-time fact. So SP-1 wants the same `effective` / `asserted` pair as PS-17's
  obligations — a genuinely reusable idea across the two briefs.

But it matters **less**, for three reasons:
1. The brief never asks for it. There is no "without losing earlier evidence" clause, no
   correction/conflict language, no finale inject. §06 is a clean six-step pipeline.
2. Financial data is *naturally* append-only and periodic. You get bitemporality-shaped behaviour
   from a plain `as_of_date` snapshot table plus an `observed_at` column — a much smaller idea.
3. The *actions* here are advisory ("recommend interventions"). Nothing irreversible has been sent
   to a counterparty, so Fowler's trigger condition — a real action taken on a belief later
   retroactively corrected — is largely absent. That is the analytical heart of the difference.

### 2. The equivalent hard modelling problem: point-in-time correctness

PS-04's real modelling trap is **temporal leakage**, and it has a name and an industry answer.

The mechanism: for each training row `(borrower, as_of_date, label)`, every feature must be the
value that was **knowable at `as_of_date`** — an AS-OF join taking the latest feature value whose
effective timestamp is `≤` the label timestamp. This is precisely what Feast's
`get_historical_features` point-in-time join does, and it exists specifically to stop future
feature values leaking into training and to eliminate training/serving skew
([Feast docs](https://docs.feast.dev/), [quickstart](https://docs.feast.dev/getting-started/quickstart)).
The skew half is the mirror image: divergence between the feature distribution at training and at
inference, caused by separate transform codebases, unit mismatches, null-handling differences,
timezone drift.

Leakage in this domain is **large**. In credit-risk modelling specifically, temporal leakage arises
from including contemporaneous predictors and from splitting data randomly rather than by time —
random *k*-fold CV is prone to it because credit risk evolves with behavioural, policy and
macroeconomic shifts, and the damage is worst exactly when a shock occurs (a reported case: random
forest AUC of **0.692** for the leaked model versus **0.442** for the non-leaked model on the same
task). *Caveat: this figure comes from a search-result summary of
[Risks 14(4):95](https://doi.org/10.3390/risks14040095); the publisher returned HTTP 403 and I
could not open the paper to verify it in context — **treat as `[UNVERIFIED]` and do not put it on
a slide without opening the paper.*** The general result — that ignoring panel structure causes
hard-to-detect leakage and inflated out-of-sample performance — is solidly established in
[Cerqua, Letta & Pinto, *On the (Mis)Use of Machine Learning with Panel Data* (arXiv:2411.09218)](https://arxiv.org/abs/2411.09218),
which I did open: the first systematic assessment of leakage in panel-data ML, across ~500 models.

**Buildability, honestly:** do **not** install Feast. In 7 days a point-in-time join is one
correct SQL statement over a snapshot table with an `observed_at` column, plus a unit test that
proves a future value cannot be selected. That test — a red bar that turns green — is the only
way to *show* this depth, and it is a weak visual.

**And here is the problem.** Getting point-in-time correctness **wrong makes your demo look
better**: the leaked model reports a higher AUC. There is no on-stage symptom. A CTO cannot tell
whether you did it right by watching. You can only *assert* it. Compare with PS-17, where the
equivalent correctness property is visible in a single drag of a slider. **This asymmetry is the
core of the verdict in this lane.**

### 3. State, orchestration and correctness under partial failure

**This is where PS-04 is thin, and it is not close.**

Read §03 and §06 again for what is *absent*: no long-running workflow state, no cross-system
dependencies, no duplicate-action prevention, no partial-failure recovery, no approval gates, no
idempotency requirement, no "act safely on incomplete information." The word "auditable" appears,
but it means *an explanation record*, not *audit replay of a mutating case*.

The honest architecture is: **a scheduled batch job.**

```
nightly:  load snapshots (as_of = D)  →  compute ratios  →  point-in-time feature join
       →  score hazard model          →  rank portfolio  →  write alerts + explanations
```

You can dress this up — durable execution, an outbox for alert delivery, idempotent alert keys so a
re-run does not double-notify the RM — and you *should*, because it is cheap and it answers the
production-readiness question. But you would be **importing** depth, not discovering it. A CTO will
ask "what breaks if this job runs twice?" and the true answer is "nothing much, it is idempotent by
construction because it is a pure function of a snapshot." That is a good property and a boring
story.

The one genuine state problem PS-04 does contain: **alert hysteresis / de-duplication over time.**
If a borrower crosses the alert threshold on Monday, drops below on Tuesday and crosses again on
Wednesday, do you fire three alerts? The brief's core challenge — "distinguish meaningful
deterioration from temporary noise" — is asking for this. The right answer is a small alert state
machine with debounce, escalation and explicit suppression/acknowledgement records. It is real,
and it is one afternoon.

### 4. The reasoning / prediction layer

#### 4.1 What "breach probability at 30/60/90 days" actually is

It is a **discrete-time hazard** problem: `P(first breach in interval k | no breach before k)`,
for k ∈ {30, 60, 90 days}. Three candidates, ranked by 7-day defensibility:

**(a) Discrete-time hazard model — RECOMMENDED.** Explode the panel into a person-period dataset
(one row per borrower per period at risk), embed the baseline hazard as period indicators, fit
*regularized logistic regression*. Botha & Verster's IFRS 9 tutorial
([arXiv:2507.15441](https://arxiv.org/html/2507.15441v1)) is the ideal reference: it argues
explicitly that for credit data collected at discrete points (monthly/quarterly repayment periods)
a discrete-time approach **matches the application problem better than continuous time and is
computationally cheaper for prediction**, and it embeds the baseline hazard through period
indicators *rather than assuming proportional hazards*. Their diagnostics are exactly the two you
want: **time-dependent ROC (tAUC)** for discrimination by horizon and **time-dependent Brier score
(tBS)** for calibration. Their study runs on 90,000 mortgage accounts (2007–2022) with ~37% mean
censoring — useful for calibrating your own synthetic panel's realism.
Why this wins for you: (i) time-varying covariates — utilization, treasury flows, payment delay —
are *native*, they are just columns on the person-period row; (ii) it gives you a **term structure**
(a hazard per horizon) rather than one number, which is literally what §06 step 3 asks for;
(iii) it is `pandas` + `scikit-learn`, so it installs on Windows in one command; (iv) coefficients
are inspectable, which pre-empts the SHAP problem in §4.3.

**(b) Cox proportional hazards — NOT RECOMMENDED here.** Semi-parametric, continuous-time,
proportional-hazards assumption. It gives a relative risk ordering, not a calibrated absolute
probability at a fixed calendar horizon, without extra baseline-hazard machinery; and the PH
assumption is doing real work you cannot check on synthetic data. Available in
[lifelines](https://lifelines.readthedocs.io/) and
[scikit-survival](https://scikit-survival.readthedocs.io/) if you want it as a comparator — and
having a comparator is worth one slide.

**(c) DeepHit — NOT RECOMMENDED, but name it.** Lee, Zame, Yoon & van der Schaar, AAAI 2018
([paper](https://aaai.org/papers/11842-deephit-a-deep-learning-approach-to-survival-analysis-with-competing-risks/),
[code](https://github.com/chl8856/DeepHit)) learns the joint distribution of survival times
directly with no assumption on the underlying stochastic process, and handles **competing risks** —
which does map onto this domain (a borrower can be waived, cured, refinanced or default *instead
of* breaching, and those are competing events). But it needs data volume you will not have, and on
labels you generated yourself its extra capacity mostly buys you the ability to memorise your own
simulator. Mentioning competing risks and then *choosing not to* use a deep model is a maturity
signal; using one is not.

Also worth naming for competing risks with discrete time:
[PyDTS](https://arxiv.org/pdf/2204.05731), a Python package for semi-parametric discrete-time
competing-risks models with LASSO/elastic-net penalisation.

#### 4.2 Calibration and honest uncertainty — this is the differentiator

For a **rare-event, decision-triggering** score, discrimination (AUC) is close to worthless and
**calibration** is everything: if you tell a credit committee "22% chance of breach in 60 days,"
that number must mean something. Two mandatory artefacts:

- **Reliability diagram + Brier score.** Bin predictions, plot observed frequency against predicted
  probability, compare to the diagonal; Brier is the mean squared error between predicted
  probability and outcome ([scikit-learn: probability
  calibration](https://scikit-learn.org/stable/modules/calibration.html)). The classic reference is
  Niculescu-Mizil & Caruana, *Predicting Good Probabilities with Supervised Learning*, ICML 2005
  ([PDF](https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf)) — including the
  specific finding that bagged/forest models are biased away from 0 and 1 because base-model
  variance is one-sided near the extremes. **Post-hoc calibration (Platt/isotonic) is a
  five-minute change with an outsized effect on the plot.**
- **Conformal prediction for the interval.** Distribution-free, finite-sample coverage guarantees;
  [MAPIE](https://github.com/scikit-learn-contrib/MAPIE) is scikit-learn-compatible and pip-installable.
  **State the caveat before the jury does:** vanilla conformal assumes exchangeability, which
  credit panels violate. The literature has the answers —
  [weighted conformal under covariate shift (Tibshirani, Barber, Candès & Ramdas, NeurIPS
  2019)](https://arxiv.org/pdf/1904.06019) and
  [Adaptive Conformal Inference under distribution shift (Gibbs & Candès, NeurIPS 2021)](https://arxiv.org/abs/2106.00170),
  which maintains long-run coverage by online re-estimation of a single shift parameter.
  Implementing ACI is a small online update loop; **saying that sentence out loud is worth more
  than most features in this problem statement.**

#### 4.3 Driver attribution, and the SHAP trap

§03 requires "identify the primary drivers." Everyone will reach for SHAP. **SHAP is unreliable
exactly where this problem lives** — highly correlated features. Financial ratios are
*definitionally* correlated: DSCR, interest cover, leverage and EBITDA margin share numerators and
denominators.

The precise failure ([Chen, Janzing et al.; see also Chen, Covert, Lundberg & Lee, *Algorithms to
estimate Shapley value feature attributions*, arXiv:2207.07605](https://arxiv.org/pdf/2207.07605)):
you must choose between **observational/conditional** and **interventional** expectations —
"true to the data" vs "true to the model." Conditional Shapley gives **non-zero attribution to
features the model does not use at all**, purely because they correlate with used features (their
worked example: a model that never sees BMI still attributes high importance to BMI via arm
circumference and blood pressure). Interventional Shapley avoids that but evaluates the model
**off-manifold**, on feature combinations that never occur in reality.

For a credit committee this is not academic: attributing deterioration to "utilization" when the
model actually keys on "days-payable-outstanding" sends the RM to the wrong conversation.

**What to do in 7 days:** (i) use the **interventional** formulation and say which one you chose
and why; (ii) group correlated ratios into **driver families** (Liquidity / Leverage / Payment
Behaviour / Treasury / Concentration / Industry) and attribute at the family level, which is both
more robust and better UX; (iii) since a discrete-time hazard model is a *logistic regression*,
give the deterministic **coefficient × delta-since-baseline** decomposition as the primary
explanation and use SHAP only as a cross-check. Deterministic attribution that is exactly additive
beats a fashionable attribution that is subtly wrong, and it satisfies §06 step 4 better.

#### 4.4 The elephant: synthetic labels

Every predictive number you report is measured against **breaches your own generator produced**.
If the generator writes "utilization > 85% for 3 weeks → breach in 45 days," the model will find
that rule and you will report a wonderful AUC that means nothing. This is unavoidable given the
brief mandates synthetic portfolios — but it is *fatal if a CTO raises it and you have no answer*.

The pre-emptive answer, which is also good engineering:
1. Put the **data-generating process on a slide** as a causal diagram, explicitly.
2. Report **calibration, not discrimination**, as the headline.
3. Build the generator with **stochastic, latent, non-rule-based deterioration** plus a noise
   channel, so that the "distinguish deterioration from temporary noise" challenge is a real one.
4. Show a **negative control**: a borrower whose signals wobble without deteriorating, which the
   model correctly does *not* escalate. A well-chosen false-positive-avoided is worth more than a
   true positive on stage.
5. Split **by time**, never randomly — and show the split on the slide.

### 5. Where PS-04 gets deep, and where a strong engineer gets bored

**Deep:**
- Point-in-time feature construction (invisible, but real).
- The discrete-time hazard formulation and its term structure.
- Calibration and honest intervals under shift.
- Alert hysteresis / noise-vs-signal state machine.

**Boring — and it is a lot:**
- Ratio computation. DSCR, leverage, current ratio: this is arithmetic. It looks like backend
  volume on a slide and reads as filler to an engineer.
- Covenant "extraction." Compared to PS-17's obligation model, a covenant is a 4-tuple
  (metric, comparator, threshold, test frequency). There is no versioning drama, no conflicting
  evidence, no straddling interval.
- Ranking. It is a sort.
- "Intervention recommendation." Without approval gates, authority checks or irreversible external
  effects, this is a lookup table from risk band to a suggested action string. A strong engineer
  will see straight through it.
- The whole orchestration tier. It is a cron job. Everything interesting about state, concurrency
  and partial failure that PS-17 *demands*, PS-04 merely *permits* — which means you would be
  building it to score points, and a CTO can tell the difference between architecture the problem
  forced on you and architecture you bolted on for the pitch.

### 6. The "explainable with a visual" test — PS-04

**The one screen: the Borrower Risk Term Structure.** A single borrower, three panels:
1. **Hazard term structure** — breach probability at 30 / 60 / 90 days, with conformal intervals
   as error bars, plotted as a curve rather than three numbers so the *shape* of deterioration reads.
2. **Driver waterfall** — family-level contributions from baseline to current, additive and summing
   exactly to the delta.
3. **Calibration curve** — reliability diagram for the whole portfolio, sitting beside it.

Panel 3 is the differentiating one, and it is also the one nobody else will show, because it is the
one that can make you look bad.

**But be honest about what this is: it is a chart.** A CTO panel has seen a thousand risk
dashboards. It communicates *results*, not *mechanism*. There is no equivalent of dragging a
knowledge-time slider and watching the system correctly refuse to change its mind about E3.
The closest PS-04 gets to a mechanism demo is a **leakage A/B**: run the same model with and
without point-in-time correctness and show the leaked one scoring *higher* while failing on the
held-out future period. That is genuinely good and I would build it — but it takes 90 seconds of
explanation before the audience understands why the *worse-looking* number is the right one.
In a 5-minute pitch that is an expensive 90 seconds.

---

## Head-to-head verdict for this lane

| Criterion (backend depth that is *also* demonstrable) | PS-17 | PS-04 |
| --- | --- | --- |
| Hardness of the core data-modelling problem | 9 | 6 |
| Does the brief *require* the hard thing, or merely permit it? | **Requires** (§04 b1, finale inject) | Permits |
| State / concurrency / partial-failure substance | 9 | 3 |
| Correctness properties that are *visible* on stage | 9 | 3 |
| Distance from what every other team will build | 8 | 4 |
| Buildable in 7 days on bare Windows | 7 | 8 |
| Risk that the depth is unfalsifiable / unshowable | Low | **High** |
| **Overall backend depth score** | **9 / 10** | **5.5 / 10** |

**Winner: PS-17, by roughly 3.5 points — a wide margin, not a coin flip.**

The reasoning in one paragraph: PS-17's examiner has written the hardest correct requirement into
the brief (retroactive amendment after actions were taken) and then handed you a live inject that
tests it. That requirement has a rigorous, citable, century-of-database-research answer
(bitemporality + provenance + incremental re-evaluation), a concrete Windows-native
implementation path (PostgreSQL 18 + DBOS or Temporal dev server), *and* — uniquely — a
one-gesture visual that proves it. PS-04's hardest problem is point-in-time correctness, which is
equally real, equally citable, and **invisible**: getting it wrong improves your headline metric
and produces no on-stage symptom. On a metric explicitly defined as *"engineering substance that
must be explainable with a visual,"* an invisible correctness property is worth a fraction of a
visible one.

Secondary reason: PS-04's backend is a batch scoring pipeline. Its genuine depth is *statistical*,
not architectural — and the jury metric here is architectural. PS-17 forces you to build workflow
state, idempotency, approval gates and compensation because the brief will not let you avoid them.
Architecture the problem forced on you always reads as more credible than architecture you added
for the pitch.

### What would change the verdict

- **If the team's centre of gravity is ML rather than systems.** A team that can ship a properly
  calibrated, conformal, competing-risks hazard model with an honest generator will score higher on
  PS-04 than the same team shipping a half-finished bitemporal ledger. Depth you cannot finish is
  zero depth. Be honest about the team on day 0.
- **If PG18 turns out to be unavailable on the demo box** and the temporal constraints have to be
  hand-rolled. This costs perhaps a day, not the verdict — but it narrows the gap. Mitigate by
  installing and smoke-testing PG18 (or MariaDB) on **day 1**, not day 5.
- **If the jury weights "quantified business impact" far above architecture.** PS-04's numbers are
  easier to source and more familiar to a banking audience. PS-17's are also well sourced —
  World Commerce & Contracting's long-running finding that poor contract management costs on the
  order of [~9% of annual revenue](https://www.worldcc.com/resource/Poor-Contract-Management-Continues-To-Costs-Companies-9-Of-Their-Bottom-Line.html)
  (9.2% when first measured; ~15%+ in complex industries) — but PS-04 has the advantage of a
  familiar frame (10–20% of US non-financial firms report a financial covenant violation in a given
  year, 1996–2008 SEC filings, per Nini, Smith & Sufi, *Review of Financial Studies* 2012, as
  summarised [here](https://corpgov.law.harvard.edu/2011/03/11/creditor-control-rights-corporate-governance-and-firm-value/)).
  This is another lane's call; it does not move mine.
- **If the amendment inject is dropped or trivialised at the event.** Unlikely — it is printed in
  the brief — but PS-17's margin depends materially on that inject existing.

---

## Risks and open questions

1. **`WITHOUT OVERLAPS` on a bitemporal table needs care.** The constraint enforces
   non-overlapping valid-time *within one assertion slice*; a full bitemporal table legitimately
   contains overlapping `effective` ranges across different `asserted` ranges. Whether PG18 permits
   a *partial* unique index carrying `WITHOUT OVERLAPS` (`… WHERE upper_inf(asserted)`) is
   **unverified**. The two-table design in §1.2 (append-only log + constrained current-belief
   projection) sidesteps this entirely and is what I would build. **Prototype this on day 1** — it
   is the single highest-risk technical unknown in the PS-17 plan.
2. **Temporal dev server ≠ production Temporal.** The single Windows binary with SQLite is a
   development server. Claiming production self-host on that footing in front of a CTO is a
   credibility loss. The correct line: "dev server for the demo; the production topology is
   server + Postgres/Cassandra + Elasticsearch, and here is the diagram."
3. **The AUC 0.692 vs 0.442 leakage figure is unverified** (MDPI returned 403). Do not put it on a
   slide until the paper is opened. The underlying claim — random CV on panel data inflates
   out-of-sample performance — *is* verified via arXiv:2411.09218.
4. **Restate on Windows is unverified.** Do not name it as a shortlisted option.
5. **XTDB v2 standalone-on-Windows is unverified** (Docker-first docs; no uberjar path confirmed).
   Cite it as prior art; do not plan around it.
6. **The straddling-interval semantic is a real open design question**, not an oversight:
   for an incident spanning an amendment boundary, is the governing version determined by incident
   start, by incident end, or pro-rated? All three are defensible. **Pick one, implement it as a
   configurable policy, and put the other two on the screen as alternatives.** A jury that sees you
   surfaced the ambiguity rather than silently resolving it will score that higher than either
   answer alone.
7. **Extraction is the schedule risk on PS-17, not the temporal engine.** If PDF→obligations is not
   working by end of day 3, hand-author the obligation JSON and keep extraction as a demonstrated
   side path. The temporal ledger is what scores; extraction is what everyone has.
8. **PS-04 alternative worth one hour of thought before committing:** if PS-04 is chosen anyway,
   the way to import PS-17-grade depth is to make the *covenant term sheet* bitemporal (waivers,
   amendments, restatements) and demo a **restatement inject** — "Q2 EBITDA was restated in
   November; which historical covenant tests flip, and which alerts were sent on a belief that is
   now known to be wrong?" That single move imports PS-17's best visual into PS-04 and is the
   highest-leverage change available to that plan.

---

## Sources

Fetched and read in full unless marked otherwise.

**Bitemporal modelling and temporal databases**

1. Martin Fowler, *Bitemporal History* — https://martinfowler.com/articles/bitemporal-history.html **[fetched]**
2. Martin Fowler, *Temporal Patterns* (Audit Log, Effectivity, Temporal Property, Snapshot) — https://martinfowler.com/eaaDev/timeNarrative.html *[search-result summary]*
3. Richard T. Snodgrass, *Developing Time-Oriented Database Applications in SQL*, Morgan Kaufmann, 1999 — https://archive.org/details/developingtimeor0000snod *[search-result summary; book record]*
4. Kulkarni & Michels, *Temporal features in SQL:2011*, ACM SIGMOD Record 41(3), 2012 — https://cs.ulb.ac.be/public/_media/teaching/infoh415/tempfeaturessql2011.pdf **[fetched; PDF partially extractable — attribution confirmed via search, content summary from the fetch]**
5. PostgreSQL 18.0 Release Notes (temporal constraints: `PRIMARY KEY/UNIQUE … WITHOUT OVERLAPS`, `FOREIGN KEY … PERIOD`; released 25 Sept 2025) — https://www.postgresql.org/docs/release/18.0/ *[search-result summary]*
6. PostgreSQL — Windows installers — https://www.postgresql.org/download/windows/ *[search-result summary]*
7. MariaDB — System-Versioned Tables — https://mariadb.com/kb/en/system-versioned-tables/ *[search-result summary]*
8. MariaDB — Bitemporal Tables — https://mariadb.com/docs/server/reference/sql-structure/temporal-tables/bitemporal-tables *[search-result summary]*
9. XTDB — Bitemporality (v1 docs; `FOR VALID_TIME` / `FOR SYSTEM_TIME`) — https://v1-docs.xtdb.com/concepts/bitemporality/ *[search-result summary]*
10. XTDB — What is XTDB? (v2) — https://docs.xtdb.com/intro/what-is-xtdb.html **[fetched]**
11. XTDB v2.2.0-beta1 release notes (Postgres wire compatibility; Docker-first artefacts) — https://github.com/xtdb/xtdb/releases/tag/v2.2.0-beta1 **[fetched]**
12. Datomic — Transaction Model (`:db/txInstant`, accumulate-only, `asOf`) — https://docs.datomic.com/transactions/model.html *[search-result summary]*
13. J. F. Allen, *Maintaining Knowledge about Temporal Intervals*, CACM 26(11):832–843, 1983 — https://dl.acm.org/doi/10.1145/876638.876639 *[search-result summary; ACM DL record for the tractable-subalgebras follow-up, Allen 1983 cited from it]*

**Provenance and incremental re-evaluation**

14. Green, Karvounarakis & Tannen, *Provenance Semirings*, PODS 2007 — https://web.cs.ucdavis.edu/~green/papers/pods07.pdf *[search-result summary]*
15. Budiu et al., *DBSP: Automatic Incremental View Maintenance for Rich Query Languages*, PVLDB 16, 2023 — https://www.vldb.org/pvldb/vol16/p1601-budiu.pdf *[search-result summary]*

**Durable execution, sagas, outbox, idempotency**

16. Temporal — Workflows (event history, deterministic replay, activity result reuse) — https://docs.temporal.io/workflows **[fetched]**
17. Temporal — Install and configure the CLI (Windows amd64 archive; server + Web UI in one binary; SQLite) — https://docs.temporal.io/cli/setup-cli *[search-result summary]*
18. Temporal — Run a development server — https://docs.temporal.io/develop/run-a-development-server *[search-result summary]*
19. Temporal CLI releases (windows_amd64) — https://github.com/temporalio/cli/releases *[search-result summary]*
20. Temporal Python SDK (PyPI wheels, Windows, Python 3.10+) — https://github.com/temporalio/sdk-python *[search-result summary]*
21. DBOS Transact for Python — https://github.com/dbos-inc/dbos-transact-py *[search-result summary]*
22. DBOS Architecture (library, no orchestration server, Postgres-backed) — https://docs.dbos.dev/architecture *[search-result summary]*
23. procrastinate — PostgreSQL-based task queue for Python — https://pypi.org/project/procrastinate/ *[search-result summary]*
24. Prefect — Install (Windows supported; SQLite for local) — https://docs.prefect.io/v3/get-started/install *[search-result summary]*
25. Hatchet v1 (Postgres-backed, Docker-first) — https://github.com/hatchet-dev/hatchet-v1 *[search-result summary]*
26. microservices.io — Saga pattern — https://microservices.io/patterns/data/saga.html *[search-result summary]*
27. microservices.io — Transactional outbox pattern — https://microservices.io/patterns/data/transactional-outbox.html *[search-result summary]*
28. IETF HTTPAPI WG — *The Idempotency-Key HTTP Header Field* (draft-ietf-httpapi-idempotency-key-header) — https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/ *[search-result summary]*
29. InfoQ — *Versioning in an Event Sourced System* (Greg Young) — https://www.infoq.com/news/2017/07/versioning-event-sourcing *[search-result summary]*
30. RFC 6962 — Certificate Transparency (append-only Merkle tree, inclusion/consistency proofs) — https://datatracker.ietf.org/doc/html/rfc6962 *[search-result summary; relevant if the cryptographic-provenance angle is pursued]*
31. OpenTelemetry — GenAI observability / semantic conventions for agent and tool spans — https://opentelemetry.io/blog/2026/genai-observability/ *[search-result summary; agent/tool conventions still provisional]*

**Decision-making and argumentation**

32. R. A. Howard, *Information Value Theory*, IEEE Trans. Systems Science and Cybernetics, 1966 — https://www.semanticscholar.org/paper/Information-Value-Theory-Howard/a7b3c2a88ca459d50010a33db8c2f113f1323e0c *[search-result summary]*
33. P. M. Dung, *On the acceptability of arguments and its fundamental role in nonmonotonic reasoning, logic programming and n-person games*, Artificial Intelligence 77(2):321–357, 1995 — https://dl.acm.org/doi/10.1016/0004-3702%2894%2900041-X *[bibliographic details from search; DL record not opened — `[UNVERIFIED URL]`]*
34. Cayrol & Lagasquie-Schiex, *On the Acceptability of Arguments in Bipolar Argumentation Frameworks*, 2005 — https://link.springer.com/chapter/10.1007/11518655_33 *[search-result summary]*
35. B. Verheij, *The Toulmin Argument Model in Artificial Intelligence* — https://www.ai.rug.nl/~verheij/publications/pdf/toulmin2009.pdf *[search-result summary]*
36. *Argumentation-Based Explainability for Legal AI: Comparative and Regulatory Perspectives* — https://arxiv.org/html/2510.11079 *[search-result summary]*
37. Alchourrón, Gärdenfors & Makinson (1985) AGM belief revision — Stanford Encyclopedia of Philosophy, *Logic of Belief Revision* — https://plato.stanford.edu/entries/logic-belief-revision/ *[search-result summary]*

**Point-in-time correctness, survival analysis, calibration, attribution**

38. Feast — documentation (point-in-time joins, `get_historical_features`, training-serving skew) — https://docs.feast.dev/ *[search-result summary]*
39. Feast — Quickstart — https://docs.feast.dev/getting-started/quickstart *[search-result summary]*
40. Cerqua, Letta & Pinto, *On the (Mis)Use of Machine Learning with Panel Data*, arXiv:2411.09218 — https://arxiv.org/abs/2411.09218 **[fetched]**
41. *Temporal and Cost-Sensitive Evaluation Framework for Credit Risk Modeling Under Distributional Shifts*, Risks 14(4):95 — https://doi.org/10.3390/risks14040095 — **`[UNVERIFIED — publisher returned HTTP 403; the 0.692 vs 0.442 AUC figure comes from a search-result summary and must be verified before use]`**
42. Botha & Verster, *Approaches for modelling the term-structure of default risk under IFRS 9: A tutorial using discrete-time survival analysis*, 2025, arXiv:2507.15441 — https://arxiv.org/html/2507.15441v1 **[fetched]**
43. Lee, Zame, Yoon & van der Schaar, *DeepHit: A Deep Learning Approach to Survival Analysis with Competing Risks*, AAAI 2018 — https://aaai.org/papers/11842-deephit-a-deep-learning-approach-to-survival-analysis-with-competing-risks/ *[search-result summary]*
44. DeepHit reference implementation — https://github.com/chl8856/DeepHit *[search-result summary]*
45. *PyDTS: A Python Package for Discrete-Time Survival Analysis with Competing Risks and Optional Penalization*, arXiv:2204.05731 — https://arxiv.org/pdf/2204.05731 *[search-result summary]*
46. scikit-survival — https://scikit-survival.readthedocs.io/en/stable/user_guide/00-introduction.html *[search-result summary]*
47. scikit-learn — Probability calibration (reliability diagrams, Brier score) — https://scikit-learn.org/stable/modules/calibration.html *[search-result summary]*
48. Niculescu-Mizil & Caruana, *Predicting Good Probabilities with Supervised Learning*, ICML 2005 — https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf *[search-result summary]*
49. MAPIE — conformal prediction for scikit-learn — https://github.com/scikit-learn-contrib/MAPIE *[search-result summary]*
50. Tibshirani, Barber, Candès & Ramdas, *Conformal Prediction Under Covariate Shift*, NeurIPS 2019 — https://arxiv.org/pdf/1904.06019 *[search-result summary]*
51. Gibbs & Candès, *Adaptive Conformal Inference Under Distribution Shift*, NeurIPS 2021 — https://arxiv.org/abs/2106.00170 *[search-result summary]*
52. Chen, Covert, Lundberg & Lee, *Algorithms to estimate Shapley value feature attributions*, arXiv:2207.07605 (interventional vs conditional; "true to the model" vs "true to the data") — https://arxiv.org/pdf/2207.07605 *[search-result summary]*
53. Molnar, *Interpretable Machine Learning* — SHAP chapter — https://christophm.github.io/interpretable-ml-book/shap.html *[search-result summary]*

**Business framing (used only to test the "what would change the verdict" clause)**

54. World Commerce & Contracting — *Poor Contract Management Continues To Cost Companies 9% Of Their Bottom Line* — https://www.worldcc.com/resource/Poor-Contract-Management-Continues-To-Costs-Companies-9-Of-Their-Bottom-Line.html *[search-result summary]*
55. Nini, Smith & Sufi, *Creditor Control Rights, Corporate Governance, and Firm Value*, Review of Financial Studies 25(6), 2012 — 10–20% of US non-financial firms report a financial covenant violation in a given year (SEC filings, 1996–2008) — https://corpgov.law.harvard.edu/2011/03/11/creditor-control-rights-corporate-governance-and-firm-value/ **[fetched — statistic confirmed]**

**Also consulted, informing the SQLite caveat**

56. SQLite single-writer / WAL concurrency — the canonical reference is https://www.sqlite.org/wal.html **(not opened)**. The property was surfaced via search-result summaries of https://turso.tech/blog/beyond-the-single-writer-limitation-with-tursos-concurrent-writes and https://www.bugsink.com/blog/database-transactions/. *The specific throughput figures appearing in those summaries are deliberately **not** used above; only the single-writer property, which is uncontroversial, is relied on.*
