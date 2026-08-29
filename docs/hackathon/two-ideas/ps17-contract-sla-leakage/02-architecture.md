# PS-17 — architecture

## The stack, verified against Windows/no-Docker

Every component below was checked for a native Windows install path. Docker-first options
(XTDB v2, Hatchet, Temporal's production self-host guide, Restate — no Windows binary at all) are
excluded.

| Layer | Choice | Why it survives the constraint |
| --- | --- | --- |
| Store | **PostgreSQL 18** | First-party native Windows installer. Sept 2025 added `PRIMARY KEY … WITHOUT OVERLAPS` and `FOREIGN KEY … PERIOD` — SQL:2011 application-time constraints |
| Queue | **pgmq** | SQL-only install, no C/Rust compile step |
| Durable execution | **DBOS Transact** | Durable workflows as a *library*. No orchestration server, one process |
| Alternative | **Temporal CLI** | Single Windows amd64 binary: server + Web UI + SQLite, zero runtime deps. Take it if you want the Web UI as a free depth visual |
| Fallback store | **MariaDB** | The one mainstream natively-Windows-installable engine with SQL:2011 bitemporal tables built in (system-versioned + application-time) |
| Tracing | **Jaeger** (`windows-amd64.zip`) or **Phoenix** (pure-Python wheel) | Langfuse is out — six services |

**Traps, named so nobody rediscovers them:** Celery unsupported on Windows since 4.x; RQ calls
`os.fork()`; Huey's own docs rule out multiprocess on Windows; APScheduler 4 says "do NOT use in
production"; Redis has no official Windows build (MS port archived at 3.0.504).

**PostgreSQL 18 does not give you system-versioning** — you hand-roll transaction time. Treat that
as a feature for the pitch: the interesting half is the half you own.

## The data model

Two tables per temporal entity: an **append-only assertion log** (the truth) and a
**constrained current-belief projection** (the fast path). Nothing is ever updated in place.

```
(valid_from, valid_to)      -- when the fact is true in the world
(recorded_at, superseded_at) -- when we believed it
```

- **Valid time** — the SLA threshold was 4h from 1 Jan, 6h from 1 Mar.
- **Transaction time** — we believed "4h from 1 Mar" until 10 Apr; from 10 Apr we believe
  "6h from 1 Mar".

Fowler's decision rule, which is the slide: bitemporality earns its complexity **exactly when an
action has already been taken on a belief that is later retroactively corrected**. His own words:
*"If we can avoid using bitemporal history, then that's usually preferable as it does complicate a
system quite significantly."* PS-17 does not let you avoid it — the inject specifies breaches were
already flagged, and the brief keeps settlement human-owned, so credit notes have already left the
building. That is Fowler's trigger condition, stated by the examiner.

The two-table design also sidesteps **OR-3** (unknown whether PG18 allows a partial unique index
carrying `WITHOUT OVERLAPS`). Spike it on day 1 anyway.

## The five backend sub-problems

| # | Sub-problem | Named solution |
| --- | --- | --- |
| SP-1 | Late, corrected, conflicting versions without losing earlier evidence | **The Bitemporal Obligation Ledger** |
| SP-2 | Re-evaluate only what changed, correctly, when an amendment lands retroactively | **Provenance-Directed Re-evaluation** |
| SP-3 | Long-running state, no duplicate external actions, safe partial-failure recovery | **Durable Case Execution + Effects Ledger** |
| SP-4 | Next-best action from state, deadlines, dependencies, authority, expected value | **The Action Scorer** (decision-theoretic, not LLM-freestyle) |
| SP-5 | Competing interpretations with supporting/weakening evidence | **The Hypothesis Board** (bipolar argumentation) |

SP-1, SP-2 and SP-3 are *stated requirements*, not embellishments. The problem statement is
already asking for the hard engineering — the single strongest argument for PS-17.

## Provenance-directed re-evaluation

When a retroactive assertion lands: compute the **valid-time delta**, then use per-verdict input
lineage to re-evaluate **exactly the affected events and nothing else**.

**Prove it by showing the count, not the wall-clock time.** In the worked example: **2 of 1,140
events re-evaluated** — and a third flagged breach that *predates* the amendment's effective date
correctly **does not change**. Every team that does "reload rules, rescore the portfolio" silently
clears that third event and is demonstrably wrong on stage.

This is provenance semirings (Green/Karvounarakis/Tannen, PODS 2007) and incremental view
maintenance (DBSP, VLDB 2023) in miniature.

**Important scoping note:** selective re-evaluation is **not** claimable as novelty — see
`04-differentiation.md`. Forty years of publications teach it (de Kleer's ATMS 1986, Doyle 1979,
Gupta et al. 1993, Green et al. 2007, differential dataflow 2013). It is a *narrowing limitation*
inside a larger claim, and it supplies the bounded-recomputation hook that gives the §101 Prong Two
and EPO technical-character arguments something to stand on.

## Scaling — the honest numbers

**Steady state is deliberately unimpressive.** 2,000 contracts × 40 obligations × 250 events/day
= 1.5M evaluations/day ≈ **17/sec**. Say this out loud; it buys credibility for the next number.

**The retroactive fan-out is the real problem, and it spans four orders of magnitude:**

| Amendment scope | Events reopened |
| --- | ---: |
| Single obligation | ~4,400 |
| Master term inherited by 200 SOWs | **18,250,000** |

Bounding it needs real technique:

1. **Bitemporal range joins** to find the affected valid-time window.
2. **Scope-predicate cardinality computed *before* work starts** — you know the blast radius
   before you pay for it.
3. **Verdict memoisation by rule-bytecode hash** — ~93% hit rate, because amendments touch 1–3
   clauses of 40.
4. **A declared-cardinality gate** that refuses to auto-run above a threshold and escalates to a
   human instead.

Even the pathological case becomes a **~1 hour chunked, resumable backfill**. That is the
production-readiness answer, and it is specific to this problem rather than generic cloud
boilerplate.

## Failure recovery

Partial-failure taxonomy → compensating actions → exactly-once **effects** (not exactly-once
delivery) → transactional outbox. The compensation catalogue from SP-6 is the interlock: an
irreversible action turns re-derivation from a computation into a human question.

**Demo it.** Kill a worker mid-action on stage, restart, show it does not double-send. This is the
single cheapest way to win backend-depth points, because idempotency is invisible and therefore
nobody else builds it.

## Compliance surface nobody spots

SLA credits are **ASC 606 variable consideration** → a revenue input → in scope for ITGC / AS 2201.
That is what makes the hash-chained decision ledger an actual **control** rather than a gimmick.
Worth one sentence in the deck; it reframes the crypto feature from novelty to necessity.
