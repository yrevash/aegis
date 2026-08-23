# ADR 0005 — PostgresSaver + a durable approvals inbox for production HITL

- **Status:** Accepted — **in force in the deployment since 2026-08-23** (amended below)
- **Date:** 2026-08-05 · **Amended:** 2026-08-23
- **Deciders:** Team
- **Related:** `docs/security/overview.md` §4.1
  (statelessness → horizontal scale), `app/agent/checkpointer.py`, `app/agent/graph.py`,
  `app/agent/orchestrator.py`, `app/data/approvals.py`,
  `app/api/routes_checkpoints.py`.

> **Read the 2026-08-23 amendment before the Decision.** Between 2026-08-05 and
> 2026-08-23 this ADR was Accepted and the deployment did **not** implement it: it ran
> `AGENT_CHECKPOINTER=memory`, there were no checkpoint tables in the database, and the
> `PostgresSaver` the Decision names could not have worked if it had been switched on.
> Both are fixed; the amendment says how, and what the fix cost.

## Context

The human-in-the-loop gate is the platform's most enterprise-credible control, but
its first form was entirely in-process: the graph compiled with LangGraph's
`InMemorySaver`, and the `/query` ⇄ `/approval` rendezvous was an in-memory
`asyncio.Future` registry (`app/agent/approvals.py`). A crash, a restart, or a second
Uvicorn worker between the `interrupt` and the decision lost the paused run — directly
contradicting the platform's own "stateless workers → horizontal scale" principle
(`docs/security/overview.md` §4.1). There was no queue an admin could list, no SLA, no
timeout/escalation, and the approver had to answer while the SSE socket stayed open.

We need durable pause/resume **and** an async "approve later from an inbox" surface,
with **no Docker** and no message-broker service (16 GB single box).

## Decision

Adopt **LangGraph's Postgres checkpoint store** (`langgraph-checkpoint-postgres`) as the
durable checkpoint store, selected by the `AGENT_CHECKPOINTER=postgres` setting; the
default stays `InMemorySaver` so tests and the offline lite demo need no Postgres. A
paused `interrupt` persists keyed by `thread_id == run_id`, so any worker can resume it
after a restart.

> **Amendment 2026-08-23 — which saver, and why not the one this ADR first named.**
> `langgraph-checkpoint-postgres` ships two savers and **neither one serves this
> codebase alone**:
>
> - `PostgresSaver` implements only the **sync** protocol; its `aget_tuple` / `aput` /
>   `alist` are the inherited `BaseCheckpointSaver` stubs, which `raise
>   NotImplementedError`. Every run here is driven with `graph.astream(...)`, and
>   `AsyncPregelLoop.__aenter__` calls `await checkpointer.aget_tuple(...)` as its first
>   act — so selecting `postgres` blew up on the first token of the first run.
> - `AsyncPostgresSaver` implements the async protocol, but its sync entry points
>   deliberately raise `asyncio.InvalidStateError` when called from their own event
>   loop, and `aegis.agent.orchestrator` calls the sync `graph.get_state(config)` from
>   inside `async def` bodies in three places (read the final state; decide whether a
>   parked run is resumable). That saver breaks the resume path instead.
>
> The store is therefore `app.agent.checkpointer.HybridPostgresSaver`: the sync
> `PostgresSaver` with its missing async half implemented by handing the *same* sync
> call to a worker thread (`asyncio.to_thread`). Nothing about how a checkpoint is
> written changes; the blocking psycopg work simply never runs on the event loop, and
> both call styles hit one store. `PostgresSaver` guards every cursor with a single
> `threading.Lock`, so checkpoint operations serialise regardless; the connection is a
> `psycopg_pool.ConnectionPool` (`autocommit`, `prepare_threshold=0`) so a dropped
> connection is re-opened rather than ending the process's ability to checkpoint.
>
> **Schema.** There is no Alembic here — `app.data.session.bootstrap` is the schema
> owner and runs its DDL on the separate owner/admin engine, because the serving role
> owns nothing. The checkpoint tables get the same treatment: LangGraph's own idempotent
> `setup()` runs on `POSTGRES_ADMIN_DSN` at boot, followed by an explicit
> `GRANT SELECT, INSERT, UPDATE, DELETE` to the serving role. Without that grant a fresh
> box gets `permission denied for table checkpoints` mid-run rather than at boot. The
> store is built during the lifespan (not lazily on the first run) so a bad DSN or a
> missing grant is a boot failure with a log line, not a 500 in the middle of somebody's
> question.
>
> **The tables are LangGraph's, and they carry no `tenant_id`.** There is no
> `tenant_isolation` policy on them and none is proposed: the thread key is the
> `run_id`, and every read of them is app-scoped through the `runs` header first (see
> the consequences below).

Because **checkpoints are persistence, not full durable execution** (they snapshot
state but give no exactly-once side-effect guarantee), we pair the saver with a
**durable approvals inbox** — a Postgres `approvals` table we own (`app/data/models.py`
`Approval`) — and make resume + tool execution **idempotent**:

- The interrupt writes a `PENDING` row (persona, action, args, risk, ml_snapshot,
  trace_id, `sla_deadline`, `assignee_tier`) — the source of truth for the paused run.
- The live socket still resolves instantly via the retained `ApprovalRegistry` notify
  cache (the money-shot gate), but the durable row is authoritative; if the socket
  parks (times out), the run is **not lost**.
- An out-of-band `POST /approvals/{id}/decision` flips the row under an **optimistic
  `PENDING → RESUMING/REJECTED`** transition — only the winner (rowcount = 1) resumes,
  so a replayed decision is a no-op. The tool is guarded by the same `approval_id` so
  it runs **exactly once** (proven in `tests/agent/test_durable_approvals.py` and
  `tests/integration/test_durable_approval_roundtrip.py`).
- An `asyncio` SLA sweeper (no cron, no Docker) marks past-deadline rows and applies
  the D5 default (auto-reject HIGH-risk).

Both approval models — the dramatic in-run gate and the async inbox — converge on one
`decide_approval` path.

## What is actually running (amended 2026-08-23)

`AGENT_CHECKPOINTER=postgres` is set in the deployment's environment and the boot log
says which store it built:

```
INFO:app.agent.checkpointer:Durable agent checkpointer ready (Postgres, checkpoint tables ensured)
INFO:app.main:Agent checkpointer: postgres (HybridPostgresSaver)
```

**Demonstrated, not asserted.** A run was parked on the human gate, the backend process
was **killed**, a fresh process was started, and the gate was then approved out of band
through `POST /approvals/{id}/decision`. Run `af7c7dd2eb67430fb814ec3eb013272e`:

| | |
|---|---|
| parked at | checkpoint `1f19eba4-8030-…`, step 6, `next = ("approval",)`, interrupt attached |
| process | killed, port closed, new PID started — the parked graph was in nobody's RAM |
| after restart | the same approval row still `pending`; the same 8 checkpoints still readable |
| resumed | step 7's **parent is the interrupted checkpoint**; steps 7 → 17 followed |
| entries | **1** — one `source: "input"` checkpoint, so the graph was entered once and never re-run |
| final | `runs.status = COMPLETED` |

## Consequences

- **+** A paused run survives a crash/restart and can resume on **any** worker →
  genuine horizontal scale, the stated architecture principle. Proven end to end above.
- **+** An admin can list, triage, and approve from an **inbox** with an SLA, decoupled
  from the request socket — the "approve tomorrow" enterprise workflow.
- **+** Exactly-once tool execution is enforced in the app layer (the honest answer to
  the "checkpoints ≠ durable execution" caveat), not assumed from the checkpointer.
- **+** The checkpoint chain is **readable**, via `GET /agent/checkpoints/{run_id}`
  (`app/api/routes_checkpoints.py`) over `graph.get_state_history(config)`, and drawn on
  the console's Trace tab. It returns ids, structure and timing **only** — never
  `checkpoint.channel_values`, never `Interrupt.value` — because a checkpoint holds the
  query, the retrieved passages and the tool arguments. The checkpoint tables carry no
  `tenant_id` and therefore no RLS policy, and this deployment's posture is fail-**open**
  for an unbound scope, so the endpoint's tenant filter on the `runs` header is the whole
  of the isolation: another tenant's `run_id` answers **404**, byte-identical to an id
  that does not exist (`tests/api/test_checkpoint_history.py`).
- **−** Durable checkpointing costs **~1.2 ms per checkpoint** more than `InMemorySaver`
  on this box (measured: 1.38 ms vs 0.23 ms per checkpoint over a 10-checkpoint graph),
  against multi-second LLM calls — and only paid when the durable saver is selected. The
  earlier "~20–50 ms" figure in this ADR was an estimate and was an order of magnitude
  too pessimistic.
- **−** Checkpoints are **not free storage**: one real run's chain is ~90 kB across
  `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` (39 runs ≈ 3.4 MB). Nothing
  prunes them yet. LangGraph exposes `prune`/`delete_thread`, and a retention sweep is
  the obvious next step; until it exists, this table grows with traffic.
- **−** The async inbox adds a resumer + a sweeper component (real code), and abstain
  parking adds a terminal state the frontend/eval must handle.
- **−** `langgraph-checkpoint-postgres` moves fast: `HybridPostgresSaver` overrides nine
  async methods by name, and a saver method added upstream would fall through to the
  raising base stub. `tests/agent/test_hybrid_checkpointer.py` pins the current set and
  asserts the upstream gap, so the day `PostgresSaver` grows a real async protocol the
  test fails and the wrapper can be deleted.
- **Note:** we deliberately hand-roll a thin `SELECT … FOR UPDATE SKIP LOCKED`-style
  table rather than adopt the PGMQ extension — a Postgres *extension* may not install
  cleanly on a no-Docker Windows box, and we want full control of the escalation policy
  (Open Decision D1).

## Alternatives considered

- **Keep `InMemorySaver` + the in-process future registry.** Simplest and lowest
  latency, but non-durable and single-worker — it fails the scalability and
  "procurement-ready" thesis the moment there is a restart or a second worker.
- **An external broker (Redis/RabbitMQ/SQS) for the approvals queue.** Battle-tested
  re-delivery semantics, but every option is either another server process (breaks the
  single-box, no-Docker constraint) or a managed cloud service (unavailable on the
  day). Postgres is already required, so a table we own is the portable choice.
- **PGMQ (SQS-like queues inside Postgres).** Gives visibility-timeout re-delivery for
  free, but it is a Postgres extension with an install-risk on a no-Docker Windows box;
  our own table + `asyncio` sweeper is fully portable and policy-controllable.
- **(2026-08-23) `AsyncPostgresSaver`, and rewriting the orchestrator's sync
  `get_state` calls to `await aget_state`.** The alternative to the hybrid wrapper, and
  the tidier one on paper: no wrapper class, and the saver's own async implementation
  rather than a thread pool. Rejected for now because `graph.get_state(config)` is
  called from three places in `aegis.agent.orchestrator` — the standalone package, which
  hosts other than this backend compile — and one missed call site is not a type error
  but an `asyncio.InvalidStateError` at the moment a human approves a parked run. The
  wrapper makes both call styles correct without touching the resume path; converting
  the package to the async state API is a separate, testable change.
