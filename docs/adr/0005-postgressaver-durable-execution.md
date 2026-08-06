# ADR 0005 — PostgresSaver + a durable approvals inbox for production HITL

- **Status:** Accepted
- **Date:** 2026-08-05
- **Deciders:** Team
- **Related:** `docs/ARCHITECTURE_REVIEW.md` §1, `docs/security.md` §4.1
  (statelessness → horizontal scale), `app/agent/graph.py`,
  `app/agent/orchestrator.py`, `app/data/approvals.py`.

## Context

The human-in-the-loop gate is the platform's most enterprise-credible control, but
its first form was entirely in-process: the graph compiled with LangGraph's
`InMemorySaver`, and the `/query` ⇄ `/approval` rendezvous was an in-memory
`asyncio.Future` registry (`app/agent/approvals.py`). A crash, a restart, or a second
Uvicorn worker between the `interrupt` and the decision lost the paused run — directly
contradicting the platform's own "stateless workers → horizontal scale" principle
(`docs/security.md` §4.1). There was no queue an admin could list, no SLA, no
timeout/escalation, and the approver had to answer while the SSE socket stayed open.

We need durable pause/resume **and** an async "approve later from an inbox" surface,
with **no Docker** and no message-broker service (16 GB single box).

## Decision

Adopt **LangGraph `PostgresSaver`** (`langgraph-checkpoint-postgres`) as the durable
checkpoint store, selected by the `AGENT_CHECKPOINTER=postgres` setting; the default
stays `InMemorySaver` so tests and the offline lite demo need no Postgres. A paused
`interrupt` persists keyed by `thread_id == run_id`, so any worker can resume it after
a restart.

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

## Consequences

- **+** A paused run survives a crash/restart and can resume on **any** worker →
  genuine horizontal scale, the stated architecture principle.
- **+** An admin can list, triage, and approve from an **inbox** with an SLA, decoupled
  from the request socket — the "approve tomorrow" enterprise workflow.
- **+** Exactly-once tool execution is enforced in the app layer (the honest answer to
  the "checkpoints ≠ durable execution" caveat), not assumed from the checkpointer.
- **−** `PostgresSaver` adds ~20–50 ms per checkpoint write vs `InMemorySaver`'s ~0 —
  negligible against multi-second LLM calls, and only paid when the durable saver is
  selected.
- **−** The async inbox adds a resumer + a sweeper component (real code), and abstain
  parking adds a terminal state the frontend/eval must handle.
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
