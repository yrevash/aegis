# Agent

## What it is

The orchestration engine. It takes one question and walks it through a fixed
sequence of steps — input guardrail, intent routing, retrieval, planning, a
risk gate, action, **verification**, reflection, answer generation, output
guardrail — and can fan out into a team of parallel specialist agents when the
question needs it.

The sequence is a **LangGraph**: a way of writing an agent's control flow as
named nodes joined by edges, instead of one long function. Each node can be
logged, tested, and — the part that matters here — paused and resumed.

## Why it exists

An enterprise platform needs two things a single model call cannot give.
First, a run must be able to **stop mid-flight** so a human can approve a
consequential action, and then continue from exactly where it stopped, even
after a server restart. Second, a hard question often needs several
specialists working at once rather than one agent reasoning alone. Both are
control-flow properties, so they live in the graph.

## Diagram

```mermaid
flowchart TD
    GI[guard_input] --> RT[route]
    RT -->|qa| RM[recall_memory]
    RT -->|memory| AM[answer_memory]
    RT -->|team| PT[plan_team]
    RM --> RV[retrieve]
    RV --> PL[plan]
    PL --> GT[gate]
    GT -->|risky tool| AP[approval interrupt]
    GT -->|safe| AC[act]
    AP -->|resumed| AC
    AC --> VF[verify]
    VF --> RF[reflect]
    RF -->|retry| PL
    RF -->|done| GN[generate]
    PT --> RN[run_team]
    RN --> SY[synthesize]
    SY --> GN
    AM --> GO[guard_output]
    GN --> GO
    GO --> ST[stream]
```

## How it works

1. **`guard_input`** screens the question against the guardrail stack.
2. **`route`** classifies intent as `qa`, `memory` or `team`, and resolves
   how wide the run should be. `SPECIALIST_NODES` maps each intent to its
   entry node, so adding a specialist means adding a node *and* a map entry.
3. **Depth** is one line of policy: the user's explicit mode wins unless it
   is `AUTO`, in which case the classifier decides. An unreadable mode falls
   back to `SINGLE`, never to `AUTO`. Whether a team can run at all is read
   from the live roster (`build_team`), not from a feature flag.
4. **Tenant cap.** A request asking for a wider team than the tenant's
   `max_parallel_agents` allows is **clamped, not refused**. The `routing`
   event reports `decided_by: platform_cap` so the console can say "you
   asked for 5, this ran at 4".
5. **`retrieve`** runs agentic retrieval over the tenant's corpus.
6. **`plan`** proposes actions and increments the iteration counter.
7. **`gate`** routes on the proposed tool's declared risk tier. At or above
   `gate_min_risk` it goes to **`approval`**, which calls LangGraph's
   `interrupt()`. The run's state is written to a **checkpointer** — a store
   that saves a snapshot of graph state after each step — and execution
   actually stops.
8. **`act`** executes the approved or safe actions. It no longer reports its
   own success — that judgement belongs to the next node.
9. **`verify`** decides the round against something outside the model. The
   design stance is *no self-critique*: asking a model whether its own work
   was good is the failure mode that does not reliably help. Three tiers run
   cheapest-first and stop at the first that reaches a verdict:

   | Tier | What it costs | What it can decide |
   | --- | --- | --- |
   | deterministic | nothing — reads the rows already in hand | a tool failure, a rail refusal, a read-only round, an oscillation |
   | read-back | one read-only call, below the gate | that the write actually landed |
   | judge | one reasoning call | only what the first two left inconclusive |

   Two of its verdicts are load-bearing. A **read-only** round is progress but
   is not charged to the repair budget, so a successful lookup no longer stops
   the read-then-write pair before the write. And **`OSCILLATING`** fires when
   the same call has now failed *three* identical times — not two, because the
   retry that repairs a transient failure necessarily carries the same
   fingerprint as the attempt it repairs.
10. **`reflect`** reads the verification verdict. It loops back to `plan`
    while the goal is unmet and `max_plan_iterations` (default `4`) still
    allows it; otherwise it goes to `generate`. The counter increments in
    `plan`, so the loop always terminates.
11. **Team path.** `plan_team` decomposes the question, `run_team` runs the
    specialists concurrently over a shared retrieval pool, `synthesize`
    merges their findings.
12. **`generate`** writes the answer, **`guard_output`** screens it, and
    **`stream`** sends it to the caller as server-sent events.

**Two token ceilings bound a trajectory, and both are enforced.** Aegis has no
trajectory compaction, so the ceilings stand in for it:

| Ceiling | Default | What it bounds |
| --- | --- | --- |
| `max_trajectory_tokens` | 36 000 | one lane's whole trajectory, before its next model call |
| `max_tool_result_tokens` | 4 000 | one tool result's contribution to that trajectory |

The second is the one that bites first — the real exposure is a single
unbounded result, not a long conversation. Both are enforced on the main graph
**and** on every sub-agent lane, and both are tenant-tightenable through the
settings catalogue (`agent.max_trajectory_tokens`, `agent.max_tool_result_tokens`,
both `TIGHTEN_ONLY`).

**`SubAgentStatus.CEILING` is a designed terminal state, not an error.** A lane
that reaches its trajectory ceiling has, by construction, already done a lot of
work. It emits `status="ceiling"` on the wire so the console can render it
differently from a clean `done`, it **keeps** its findings, and the synthesis
names it as cut short. Partial findings from a truncated lane are worth
strictly more than silence, and the reader is told about the truncation in
words rather than being told nothing twice over.

**The checkpointer is injected.** `AGENT_CHECKPOINTER=memory` uses LangGraph's
`InMemorySaver`; a parked run dies with the process. `AGENT_CHECKPOINTER=postgres`
uses `HybridPostgresSaver`, which serves both the sync and async call styles
over one Postgres-backed store by handing the blocking work to
`asyncio.to_thread`. Its schema is created by LangGraph's own idempotent
`setup()` on the owner DSN, then granted to the serving role.

**No ML step runs in this graph.** The ML spine is a tenant-facing capability
served by its own endpoints, not a stage of the pipeline.

## What it stores

| Table | Owner | What matters |
| --- | --- | --- |
| `approvals` | this module | `id`, `run_id`, `thread_id`, `tenant_id`, `status`, `action`, `actions` (every call the gate authorises), `args`, `risk`, `requested_by`, `assignee_tier`, `sla_deadline`, `decided_at`, `decided_by` |
| `checkpoints` | LangGraph | one row per super-step snapshot, keyed by `thread_id` (which is the `run_id`) |
| `checkpoint_blobs` | LangGraph | the channel values a checkpoint references |
| `checkpoint_writes` | LangGraph | pending writes recorded against a checkpoint |

`approvals` is the source of truth for a paused run. The checkpoint tables
are LangGraph's own schema and carry **no `tenant_id` column**.

The graph also writes `run_events` rows through `app.agent.run_log`; that
table belongs to the runs module.

## Security and tenant isolation

- `approvals` carries a `tenant_id` and is registered in the RLS registry, so
  Postgres row-level security filters it in addition to the app-level
  predicate.
- The checkpoint tables have no tenant column and therefore no policy.
  `GET /v1/agent/checkpoints/{run_id}` enforces scope in the app layer, on the
  `runs` header, **before** the checkpoint store is touched. An unknown
  `run_id` and another tenant's `run_id` both answer `404`, byte-identical.
- That endpoint returns ids, structure and timing only. `channel_values`,
  interrupt payloads, and task errors and results are dropped, because they
  carry the query, retrieved passages and any PII.
- Approving is admin-only *and* seat-gated: both decision routes check
  `seat.can_approve` after the role check.
- The `load_skill` tool and every other tool read tenant and user identity
  from the server-side request context, never from model-supplied arguments.

## API surface

| Method | Path | Who may call | Returns |
| --- | --- | --- | --- |
| POST | `/v1/query` | any authenticated caller | the SSE run stream |
| GET | `/v1/approvals` | any authenticated caller | the approvals inbox, scoped to the caller |
| POST | `/v1/approval` | admin, with `seat.can_approve` | the decision result, resuming the run |
| POST | `/v1/approvals/{approval_id}/decision` | admin, with `seat.can_approve` | the durable-row decision result |
| GET | `/v1/agent/checkpoints/{run_id}` | any authenticated caller | the checkpoint chain, ids and timing only |
| GET | `/v1/agent/topology` | any authenticated caller | the real node/edge shape, read off the compiled graph |
| GET | `/v1/harness/config` | admin or ai_team | the tweakable-knob record and effective values |

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `AGENT_CHECKPOINTER` | `memory` | `memory` or `postgres`; decides whether a parked run survives a restart |
| `POSTGRES_DSN` | `postgresql://postgres:postgres@localhost:5432/taif` | where the Postgres saver writes |
| `POSTGRES_ADMIN_DSN` | `""` | owner DSN that creates the checkpoint schema and grants it |
| `APPROVAL_SLA_SECONDS` | `3600` | deadline stamped on a new approval row |
| `APPROVAL_DEFAULT_TIER` | `tier-1` | default assignee tier |
| `APPROVAL_SWEEPER_INTERVAL_SECONDS` | `30.0` | how often the SLA sweeper runs |
| `MODEL_<ROLE>` | per-role fleet default | overrides which deployment a role resolves to |

## Where it lives

| Path | What it does |
| --- | --- |
| `aegis/src/aegis/agent/graph.py` | the LangGraph itself, every node body (including `verify` and its three tiers), `NODE_LABELS`, `SPECIALIST_NODES` |
| `aegis/src/aegis/agent/router.py` | intent classification and the depth policy |
| `aegis/src/aegis/agent/state.py` | `AgentState`, the typed graph state |
| `aegis/src/aegis/agent/deps.py` | the dependency-injection contract and risk ordering |
| `aegis/src/aegis/agent/team.py` | team planning, concurrent execution, synthesis |
| `aegis/src/aegis/agent/subagent.py` | one specialist worker with its own working memory; `SubAgentStatus` and the ceiling terminal state |
| `aegis/src/aegis/agent/topology.py` | `graph_topology()`, read off the compiled graph |
| `aegis/src/aegis/agent/approvals.py` | the approval registries and parked-run handles |
| `aegis/src/aegis/agent/orchestrator.py` | the host-agnostic run loop and gate rendezvous |
| `aegis/src/aegis/agent/rails.py` | tool-result screening |
| `aegis/src/aegis/agent/retry.py` | transient-only retry around node calls |
| `aegis/src/aegis/agent/harness.py` | the tweakable-knob record and run summary |
| `backend/src/app/agent/checkpointer.py` | `HybridPostgresSaver`, schema setup and role grant |
| `backend/src/app/agent/deps.py` | the composition root that binds real modules to the contract |
| `backend/src/app/agent/orchestrator.py` | durable decision glue around the pure run loop |
| `backend/src/app/agent/run_log.py` | writes the run's events into `run_events` |
| `backend/src/app/agent/skills_tool.py` | the `load_skill` tool definition and dispatch |
| `backend/src/app/api/routes_checkpoints.py` | the checkpoint-history endpoint |

## What it does not do

- It does not prune checkpoint history. Storage grows with every run.
- It does not expose the state a checkpoint captured; the projection is
  structural only.
- It does not refuse an over-wide team request; it clamps and reports.
- **It does not compact a trajectory.** There is no summarise-and-continue
  step; the two token ceilings are what stand in for one, and a lane that
  reaches `max_trajectory_tokens` stops rather than shrinking its history.
- **`verify` never asks the model to grade itself.** The judge tier grades what
  a tool returned, and only where the deterministic and read-back tiers were
  inconclusive.
- `team` is not a roster role a domain adapter can declare. The router writes
  it, so it cannot be configured away.
- It does not run a machine-learning step. Predictions are a separate
  capability with its own endpoints.
