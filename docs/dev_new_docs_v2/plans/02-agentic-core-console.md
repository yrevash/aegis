# Plan 02 — The agentic core and the unified console

> **Scope.** This plan owns: real concurrent multi-agent orchestration, the skills subsystem,
> real MCP (server + client), the unified chat console, the harness, per-tenant LLMOps and
> guardrail surfaces, Tavily-backed research, and budget visibility in the user's own surface.
>
> **Sequencing directive (revised).** The finals are a **checkpoint, not the optimisation
> target**. We build v2 properly, foundations first, in dependency order. A partially-migrated
> system at the finals is acceptable. Every phase below therefore ends with a
> **"Demoable at this boundary"** block so the team can present from whichever checkpoint
> they are standing on.
>
> **Non-negotiables carried from [`docs/hackathon/brief.md`](../../hackathon/brief.md):**
> 16 GB Windows laptop, **no Docker**, no GPU, no local model weights. Postgres native,
> Neo4j Desktop, Memurai for Redis. API-only models. Nothing in this plan violates that.
>
> **Related plan.** Data/governance (Postgres-only, RLS, tenant hierarchy, chat-session
> storage) is planned separately. Every dependency on it is flagged **[DATA]** below.

---

## Part 0 — How to read this

Sections 1–2 are **ground truth and the two decisions everything else hangs off**. Read them
first; the phases are meaningless without them. Section 3 is the phased plan. Sections 4–7 are
dependencies, risks, open decisions, and the "what you missed" list you asked for.

Every claim about current behaviour in Section 1 is grounded in source read for this plan, with
file and line. Where I ran code to verify a design assumption it is called out as **VERIFIED**
and the probe is reproduced in the Appendix.

---

## Part 1 — Ground truth: what Aegis actually does today

### 1.1 There is one graph, one specialist at a time, and no concurrency at all

`aegis/src/aegis/agent/graph.py` compiles a single `StateGraph` with a strictly linear
hot path:

```
guard_input → route → recall_memory → retrieve → ml_predict → plan
                                                               │
                                                   gate ───────┘
                                                  ╱    ╲
                                           approval    act → reflect → generate
                                                                  │        │
                                                                  └─ retry ┘
                                                                           ↓
                                             guard_output → stream → persist_memory
```

- The "multi-agent supervisor" is `route` (graph.py:391) calling
  `aegis/src/aegis/agent/router.py::route_query`. It is a **deterministic keyword classifier**
  that picks **exactly one** role, with a cheap-LLM tiebreak only on a genuine tie
  (router.py:171-211).
- `SPECIALIST_NODES` (graph.py:128) maps exactly **two** roles: `qa → recall_memory` and
  `memory → answer_memory`. The adapter roster (`backend/src/app/adapter/roster.py:78`)
  declares exactly those two specialists.
- **Nothing in the graph runs in parallel.** Every edge is sequential. `AgentState`
  (`aegis/src/aegis/agent/state.py`) has `operator.add` reducers on
  `plan_iterations`/`prompt_tokens`/`completion_tokens`/`cost_usd` and its docstring
  explicitly says they exist so the state "remains correct under a fan-out" — a fan-out that
  does not exist yet. That is good news: the accumulator work is already done.
- The tool loop is one round: `plan` proposes calls, `act` executes them **serially in a for
  loop** (graph.py:890), `reflect` may loop back to `plan` up to
  `config.max_plan_iterations` (default 2).

**So: today a "query" is one LLM plan call, N serial tool calls, one generate call.** There is
one identity, one log, one lane. The console's "Orchestration" panel draws the *graph node*
topology (`aegis/src/aegis/agent/topology.py`), not agents.

### 1.2 The console wire is NOT AG-UI. There are two protocols and only one is live

The brief I was given said the stream is AG-UI CustomEvents mirrored in
`web/src/lib/streamNames.ts`. **That is not what serves the console.** Verified:

| Protocol | Where | Status |
|---|---|---|
| `StreamEvent` Pydantic discriminated union (`backend/src/app/api/schemas.py:448`), mirrored in `web/src/lib/stream.ts:334` | `POST /query` → `EventSourceResponse` (routes.py:894-936) | **This is the live console wire.** |
| `AegisEmitter` / AG-UI `CustomEvent` (`aegis/src/aegis/core/stream.py`), names in `aegis/src/aegis/core/stream_names.py` mirrored in `web/src/lib/streamNames.ts` | `grep` for `AegisEmitter` across the repo returns **tests only**, plus one demo route `GET /stream/guardrail-demo` (routes.py:771) | **Not on the console path.** A parallel, largely unused primitive. |

This matters enormously for the plan: **the multi-agent protocol work goes on the
`StreamEvent` union, not on AG-UI.** Doing it twice would be waste, and doing it on the
inert one would produce a demo that shows nothing.

Nodes emit **plain dicts** via LangGraph's custom stream writer
(`aegis/src/aegis/agent/events.py` builders → `get_stream_writer()`), and
`aegis/src/aegis/agent/orchestrator.py::run_agent` stamps each with `run_id` + a monotonic
`seq` through an **injected `stamp` callable** (orchestrator.py:66-73, 169-173) so the pure
package never imports the host schema. That injected-stamp seam is exactly where agent
identity should be enforced. It is a good design and we keep it.

### 1.3 Three live event types are silently dropped by the console; one console type is never emitted

| Event | Backend emits? | Frontend union has it? | Reducer handles it? |
|---|---|---|---|
| `reflection` | Yes (schemas.py:383, graph.py:963) | **No** | No |
| `routing` | Yes (schemas.py:406, graph.py:425) | **No** | No |
| `memory` | Yes (schemas.py:426, graph.py:469) | **No** | No |
| `abstained` | **No** (not in the Python union) | Yes (stream.ts:279) | Yes (runReducer.ts:302) |

`runReducer.ts:337` ends with `default: return next`, so the three real events land in
`state.events` and are otherwise discarded. The self-repair loop, the supervisor hand-off,
and every memory recall are **already happening and already on the wire, invisible.** This is
free demo value sitting on the floor, and it is also proof that additive protocol changes are
safe: an unknown `type` never breaks the client.

### 1.4 The live console never sends `session_id`, so long-term memory is inert in the real product

`QueryRequest` accepts `session_id` (schemas.py:595) and `run_agent` threads it into the
memory nodes. But `web/src/lib/api/liveTransport.ts:31` posts
`JSON.stringify({ query, persona })` — no `session_id`, ever. `recall_memory` and
`persist_memory` therefore return `{}` on every live run (graph.py:648, 696).

**The whole memory subsystem is dark on the live console today.** Nobody should claim
multi-turn memory on stage until this is fixed. It is a one-line transport change plus the
chat-session work in Phase 2.

### 1.5 There is already a real MCP server — single-tenant, stdio-only, env-pinned persona

`backend/src/app/mcp/server.py` (593 lines) is a genuine `mcp` SDK 2.0 low-level server that
fronts the adapter tool registry. It is honest about what it is: tool list comes from
`tool_definitions_for(persona)` (allowlist-filtered), calls route through `run_tool` which
re-checks the allowlist and writes an audit row, and **HIGH-risk tools are listed but never
auto-executed** — they return "requires human approval, routed to the inbox". That policy is
already right and is a strong jury line.

What it is not:
- **stdio only.** No HTTP transport, so nothing in-product can reach it.
- **Persona pinned by `MCP_PERSONA_ID` env var** (server.py:90) — a process-wide,
  single-tenant assumption. There is no per-caller identity, no tenant scope, no RBAC beyond
  the one baked-in persona.
- No resources, no prompts — tools only.
- No Aegis-side MCP **client**, so the platform cannot consume any external MCP server, and
  the admin cannot query Aegis through MCP.

### 1.6 Skills already exist as a real mechanism — filesystem markdown, adapter-selected

`aegis/src/aegis/memory/recall.py:300-319` lists `*.md` under `spec.SKILLS_DIR`, hands the
names to `spec.select_skills(query, persona, available)`, reads the chosen bodies whole, and
`aegis/src/aegis/memory/working.py` injects them into the working-memory block under
`## Applicable skills` with a token budget share of 0.10 and eviction priority
(`memory/config.py:41`, `working.py:42-45`).

The reference implementation is `backend/src/app/adapter/memory_spec.py:139` — a hardcoded
keyword→skill dict — over two files: `handling_refunds.md` and `de_escalation.md`.

So the *plumbing* is real. What is missing is everything that makes it a subsystem: no
frontmatter, no description tier, no progressive disclosure (bodies are injected whole), no
storage beyond the repo filesystem, no per-tenant/per-user scoping, no authoring UX, no
visibility that a skill fired, and no way for a skill to be scoped to a specific agent.

### 1.7 LLMOps is half-built and has a real multi-tenancy defect

`aegis/src/aegis/ops/` has draft → eval-gate → promote → rollback over a `PromptVersion`
table that **already carries `tenant_id`** (`ops/models.py:82`). The harness reads the active
prompt synchronously off a process-wide cache.

**Defect:** that cache is keyed by `prompt_key` alone —
`_ACTIVE_CACHE: dict[str, tuple[str, dict, int]]` (`ops/registry.py:28`). Two tenants cannot
have different active prompts; whichever was cached last wins for everybody. The table is
multi-tenant, the read path is not. This must be fixed before any per-tenant LLMOps surface
is honest.

### 1.8 Everything else, briefly

- **Budgets:** enforcement is real at the LiteLLM chokepoint and terminates a run with
  `budget_exceeded` (orchestrator.py:328). The read surfaces `/admin/budgets` and
  `/admin/usage` (routes.py:1355, 1454) are **admin-scoped only**. A user cannot see their own
  budget anywhere.
- **Models:** `aegis/src/aegis/gateway/routing.py:34` holds a role→deployment map with
  `MODEL_<ROLE>` env overrides. There is no per-tenant or per-user preference, and no endpoint
  listing the fleet. But a `set_governance_context` **contextvar already exists**
  (routes.py:922) and is bound per request — that is the clean seam for a per-run model
  override with zero signature churn.
- **Harness:** `aegis/src/aegis/agent/harness.py::run_summary` folds the emitted event stream
  into a structured record, and `harness_config()` reflects every `AgentConfig` knob. Both are
  **pure functions over in-memory data**. Nothing is persisted; a run's events die with the
  socket. `web/src/components/harness/HarnessView.tsx` is the single existing surface.
- **Tavily:** not installed (`tavily-python MISSING`), not referenced anywhere.
- **Docling:** not installed. (RAG ingestion is the other plan's domain — flagged only because
  Docling is heavy on a 16 GB box.)
- **Versions (verified in `backend/.venv`):** langgraph 1.2.11, langgraph-checkpoint-postgres
  3.1.2, mcp 2.0.0, fastapi 0.141.1, litellm 1.96.0, sse-starlette 3.4.8, ag-ui-protocol 0.1.19.

---

## Part 2 — The two decisions everything hangs off

### 2.1 Decision A — how sub-agents actually run concurrently

Three candidate mechanisms were considered. I ran a probe against the installed
langgraph 1.2.11 to settle it (Appendix A).

| Option | Verdict |
|---|---|
| **LangGraph subgraphs + `astream(subgraphs=True)`** | Works, but changes the yielded tuple from `(mode, chunk)` to `(namespace, mode, chunk)`, which rewrites the orchestrator's hot loop (orchestrator.py:206-223) **including** the `__interrupt__` detection and `graph.get_state(config)` calls that drive the human gate. Agent identity would be a namespace string we then have to parse. High blast radius on the one piece of code that must not break. |
| **`Send` API fan-out to a mapped node** | **VERIFIED working** — each mapped instance emits custom events. But every state key a mapped node writes needs a reducer (state.py's docstring is explicit and correct about this), and the human gate has to sit after the fan-in regardless. It buys nothing over option 3 and costs state-schema churn. |
| **`asyncio.gather` of sub-agent coroutines *inside one node*** | **VERIFIED working, and it is the recommendation.** `get_stream_writer()` propagates into `gather`-spawned tasks through contextvars, so three concurrent workers emitted **live, interleaved** custom events in real time. No LangGraph API change, no tuple-shape change, no state reducer churn, and agent identity is an explicit value we pass rather than a namespace we parse. |

**Recommendation: fan out with `asyncio.gather` inside a dedicated `run_team` node.**

This is not a shortcut — it is genuinely concurrent execution of genuinely independent agents,
each with its own system prompt, its own tool allowlist, its own bounded tool-calling loop, its
own model calls, its own token/cost accounting, and its own failure containment. It is "real"
in every sense the user meant. What it avoids is destabilising the checkpoint/interrupt
machinery that makes the human gate durable — which is a *correctness* argument, not a
convenience one.

**Hard design constraints that fall out of this choice:**

1. **`interrupt()` must never be called from inside a gathered task.** The human gate stays in
   the main graph, after fan-in. Sub-agents are restricted to LOW/MEDIUM-risk tools; anything
   HIGH is *proposed* by a sub-agent and *executed* by the existing `gate → approval → act`
   path in the main graph. This is a security improvement, not a limitation: it means no
   concurrent agent can ever take a consequential action without passing the one gate.
2. **The node returns one summed delta.** Because the gather is inside a node, the node
   returns a single `{prompt_tokens, completion_tokens, cost_usd}` delta — the existing
   `operator.add` reducers keep working untouched.
3. **`asyncio.gather(..., return_exceptions=True)` always.** One agent's failure must never
   cancel its siblings.
4. **`BudgetExceededError` must be re-raised after the gather**, so the orchestrator's existing
   handler (orchestrator.py:328) still terminates the run cleanly as `blocked`.

### 2.2 Decision B — the durable run-event log becomes the substrate

The DeepSeek Harness (open-sourced 2026-08-13) is built on one idea worth stealing outright:
**an append-only session-event log is the single source of truth, and resume, fork, replay,
transcripts, telemetry and the web UI are all projections of it.** ([DeepSeek Harness](https://deepseek.com/harness/en/), [The New Stack](https://thenewstack.io/deepseek-harness-open-source-plugins/))

Aegis already **produces** exactly such a stream. It just throws it away when the socket
closes. `run_summary()` is already a projection over it — it just runs over a list that only
lives in memory.

**Recommendation: persist every stamped event to Postgres at the `emit()` seam, and rebuild
the harness, replay, per-run audit, and per-tenant LLMOps evidence as projections over it.**

```
run_events (
  run_id      uuid,
  seq         int,
  ts          timestamptz,
  tenant_id   int,          -- RLS anchor  [DATA]
  user_id     int,          -- RLS anchor  [DATA]
  session_id  uuid,
  agent_id    text null,    -- NULL = supervisor / graph-level
  type        text,
  payload     jsonb,
  primary key (run_id, seq)
)
```

Why this is the highest-leverage single item in the plan:

- **Harness** becomes a query, not a new subsystem.
- **Replay** becomes free: `GET /harness/runs/{id}/events` re-streams a *real recorded run*
  through the exact same `runReducer`. That is the best possible demo insurance (Section 5,
  R1) and it is not mock data.
- **Per-user scoping** ("not global", as asked) becomes an RLS policy on two columns rather
  than bespoke filtering in every surface.
- **Per-tenant LLMOps evidence** ("show tenants how self-improving prompts help them") becomes
  a join between `run_events` and `prompt_versions` — both already tenant-stamped.
- **Multi-agent inspection** becomes a `WHERE agent_id = …` — the whole per-agent log
  requirement is satisfied by one indexed column.

The write must be **best-effort, batched, and off the hot path**: a bounded `asyncio.Queue`
drained by a background task, injected into `run_agent` as a sixth seam alongside `stamp`,
`enqueue_approval`, `on_terminal`. A failing sink logs and drops; it must never break a
stream. **[DATA]** — the table, RLS policies and retention policy belong to the data plan.

---

## Part 3 — The phases

Effort is in engineer-days assuming one engineer paired with an agentic coding tool.
Ranges are honest, not padded.

---

### Phase 0 — The wire and the substrate

**Goal.** Make the event protocol capable of carrying multiple agents, make runs durable and
inspectable, and make them cancellable — before anything is built on top.

**Nothing after this phase should require another protocol change.**

#### Work items

**P0.1 — Repair the existing protocol drift.** *(0.5 d)*
- Add `Reflection`, `RoutingEvent`, `MemoryEvent` to the TS union
  (`web/src/lib/stream.ts`) and handle them in `runReducer.ts`.
- Delete `Abstained` from the TS union and its reducer branch — the backend never emits it and
  the reducer has a whole `phase: 'abstained'` state that can never be reached
  (runReducer.ts:302, 316).
- Add a parity test in the shape of the existing
  `backend/tests/api/test_stream_name_mirror.py` (which parses the TS file and diffs it against
  the Python set) — but for the **`StreamEvent` union**, not the AG-UI names. The existing test
  guards the wrong protocol.

**P0.2 — Agent identity on every event.** *(1 d)*
- Add one nullable field to `_BaseEvent` (`backend/src/app/api/schemas.py:126`):
  ```python
  agent_id: str | None = Field(default=None, description="Sub-agent that emitted this event; None = supervisor/graph-level.")
  ```
  Every one of the 19 variants inherits it. Mirror on `BaseEvent` in `stream.ts:50`.
  Additive and backwards-compatible — `default: return next` guarantees old clients are safe.
- Add a `scoped_writer(writer, agent_id)` helper in `aegis/src/aegis/agent/events.py` that
  returns a callable merging `{"agent_id": …}` into every payload. Sub-agents get a scoped
  writer; they never remember to stamp anything themselves.
- **New event types** (in `aegis/src/aegis/agent/events.py` + `schemas.py` + `stream.ts`):

  | Type | Payload | Purpose |
  |---|---|---|
  | `agent_plan` | `tasks: [{agent_id, role, label, task, tools[]}]`, `strategy`, `reason` | Emitted by the supervisor **before** any agent starts, so the console can reserve and render all agent cards instantly. This is what makes them "pop up". |
  | `agent_started` | `agent_id, role, label, task, model, tool_allowlist[]` | One agent began. |
  | `agent_status` | `agent_id, phase, detail` | The one-line "what is it doing right now" for the card header (`thinking` / `calling tool` / `reading` / `writing`). |
  | `agent_finished` | `agent_id, status, duration_ms, prompt_tokens, completion_tokens, cost_usd, summary, steps` | Terminal per agent. `status ∈ ok \| failed \| timeout \| cancelled \| skipped`. |
  | `agent_handoff` | `from_agent_id, to_agent_id, reason, payload_summary` | Explicit A2A edge — pairs with the `a2a.*` OTel attributes already stamped at graph.py:411-423. |
  | `synthesis` | `contributing_agents[], omitted_agents[], strategy` | Honest merge record: which agents' work is in the answer and which are missing. |
  | `run_cancelled` | `reason, cancelled_by` | Terminal. |

  Existing `reasoning`, `tool_call`, `tool_result`, `retrieval`, `guardrail`, `node_started`,
  `node_finished` need **no shape change** — they simply carry `agent_id` now, and become
  per-agent for free. That is the whole point of putting identity on the base.

- **`seq` stays globally monotonic.** The orchestrator's single `emit()` closure
  (orchestrator.py:169) is the only stamper, so interleaved agent events still get a strict
  global order. The console sorts by `seq` and groups by `agent_id`. No distributed-ordering
  problem to solve.

**P0.3 — Durable run-event log.** *(1.5 d, + **[DATA]** for the table/RLS)*
- Sixth injected seam on `run_agent`: `event_sink: Callable[[dict], None] | None`. Wire it in
  `routes.py::query`. Implement as a bounded `asyncio.Queue` + a single background drain task
  doing batched `INSERT`s. Drop-oldest on overflow with a counted warning — never block the
  stream.
- `GET /harness/runs` (filterable by tenant/user/session/status/date) and
  `GET /harness/runs/{run_id}/events`.
- Reimplement `harness.run_summary()` as a projection over stored rows (keep the in-memory
  path for tests — it is the same pure function).

**P0.4 — Cancellation.** *(1 d)*
- `POST /runs/{run_id}/cancel`. A process-wide `CancellationRegistry` mirroring the existing
  `ApprovalRegistry`/`ParkedRunRegistry` pattern (`aegis/src/aegis/agent/approvals.py`).
- Cooperative token checked between sub-agent steps and before each tool call; the fan-out
  `finally` cancels pending tasks.
- Terminal `run_cancelled` event, `RunStatus.CANCELLED` added to `aegis/src/aegis/core/types.py`.
- Client abort must already work (SSE generator close → astream cancel), but it currently
  leaves no record; the explicit endpoint gives an auditable "who stopped it".

**P0.5 — The run context carries everything a run needs.** *(1 d)*
- Extend `QueryRequest`: `session_id` (already there, now actually used), `model_overrides:
  dict[str, str] | None` (role → deployment), `depth: 'single' | 'team'`,
  `attachments: [attachment_id]`.
- Carry the model override on the **existing** `set_governance_context` contextvar
  (routes.py:922) so `deps.complete` resolves it without a single signature change through
  the graph. This is the seam that makes per-tenant/per-user model selection cheap.
- Fix `liveTransport.ts:31` to send `session_id` and the new fields. **Until this lands, the
  memory subsystem is dark in the live product (§1.4).**

**P0.6 — Per-agent OTel spans.** *(0.5 d)*
- Each sub-agent opens a `SpanKind.AGENT` span child of `agent.run`, stamped with
  `semconv.A2A_FROM/A2A_TO/A2A_REASON` (the constants already exist and are used by the router).
  Phoenix then shows the same tree the console shows — the strongest available evidence that
  the concurrency is not theatre.

#### Files touched
`aegis/src/aegis/agent/events.py`, `orchestrator.py`, `approvals.py`,
`aegis/src/aegis/core/types.py`, `aegis/src/aegis/agent/harness.py`,
`backend/src/app/api/schemas.py`, `backend/src/app/api/routes.py`,
new `backend/src/app/data/run_events.py`, `web/src/lib/stream.ts`,
`web/src/state/runReducer.ts`, `web/src/lib/api/liveTransport.ts`,
`backend/tests/api/test_stream_*`.

#### Effort
**5–6 days.**

#### Demoable at this boundary
- **Replay.** Run a query, then re-render that exact run from Postgres through the same
  reducer — with real timings. Honest, not mock. This alone is a credible checkpoint demo and
  it is the demo-risk insurance for every later phase.
- Three previously-invisible behaviours become visible in the existing console: the supervisor
  hand-off, the self-repair reflection loop, and memory recall.
- A stop button that actually stops a run.

---

### Phase 1 — Real concurrent sub-agents

**Goal.** Genuinely concurrent, genuinely independent agents with their own prompts, tools,
logs, budgets and failure modes. Strictly real.

#### The architecture

Two new modules in the pure package, plus three graph nodes.

**`aegis/src/aegis/agent/subagent.py` — one agent, one bounded loop.**

```
SubAgentSpec(agent_id, role, label, system_prompt, tool_allowlist,
             model_role, max_steps, timeout_s, skills[])

async def run_subagent(spec, task, *, deps, writer, cancel) -> SubAgentResult
```

A small ReAct-shaped loop, ~200 lines, reusing the injected deps that already exist:
`deps.complete`, `deps.run_tool`, `deps.tool_definitions_for`, `deps.retrieve`. Per step it
emits `agent_status`, then `reasoning`, then `tool_call`/`tool_result` — all through the
scoped writer, so every event is stamped with `agent_id` automatically.

Invariants, all enforced in code not prompt:
- Tools are filtered to the spec's allowlist **intersected with** the persona allowlist
  (`is_allowed`). A sub-agent can never widen its own reach.
- Any tool at or above `gate_min_risk` is **not executable by a sub-agent** — it is returned as
  a *proposal* in `SubAgentResult.proposed_actions` and flows into the main graph's
  `gate → approval → act` path. One gate, always.
- `max_steps` hard cap; `asyncio.wait_for(timeout_s)`.
- Never raises. Every failure becomes `SubAgentResult(status=failed|timeout, error=…)` except
  `BudgetExceededError`, which is captured and re-raised after fan-in.
- Its own usage totals, returned for the node's summed delta.

**`aegis/src/aegis/agent/team.py` — plan, fan out, synthesise.**

- `plan_team(query, roster, deps) -> TeamPlan` — one model call against the sub-agent roster
  producing the task list. Bounded to `config.max_parallel_agents`. Emits `agent_plan`.
  Deterministic fallback (route by keyword to a fixed default team) when the model call fails —
  the team path must never be the reason a run dies.
- `run_team(plan, deps, writer, cancel) -> list[SubAgentResult]` — the `asyncio.gather`
  fan-out, under an `asyncio.Semaphore(config.max_concurrent_agents)`, with launches staggered
  by ~250 ms to avoid a burst against the gateway.
- `synthesize(results, deps) -> str` — one model call merging the agents' findings, plus a
  `synthesis` event naming **which agents contributed and which were omitted**. The synthesiser
  prompt must be told to attribute claims to the agent that produced them.
- **Critic pass** (sequential, after synthesis): a fifth agent reviews the merged draft against
  the sub-agent outputs for unsupported claims. This is what makes "4-5 agents" honest — four
  concurrent + one sequential critic — and it is a real quality control, not padding.

**Graph wiring.** Add a `team` specialist:

```python
SPECIALIST_NODES = {
    "qa":     "recall_memory",   # unchanged, single-pass
    "memory": "answer_memory",   # unchanged
    "team":   "plan_team",       # new
}
```

```
route ─(team)→ recall_memory_t → plan_team → run_team → synthesize → gate → …
                                                          │
                                                          └→ (proposed HIGH-risk actions)
```

`plan_team`/`run_team`/`synthesize` land on the **existing** `gate → approval → act →
reflect → generate → guard_output → stream → persist_memory` tail. The human gate, the output
rail, the answer cache and memory persistence all keep working untouched. The `qa` path is
byte-identical to today, which keeps the golden-trace tests green.

**Sub-agent roster (adapter-owned).** `backend/src/app/adapter/roster.py` grows a second
declaration alongside `AgentRoster`: `SubAgentRoster`, listing the concurrent specialists with
their prompts and tool allowlists. Domain-agnostic mechanism, domain-specific content — the
same seam discipline the file already documents. The reference team:

| Agent | Does | Tools |
|---|---|---|
| **Research** | External evidence | `web_search` (Tavily), `fetch_url` |
| **Knowledge** | Internal corpus + graph | `retrieve` (hybrid + graph) |
| **Data** | Structured records | the adapter's read tools (LOW/MEDIUM) |
| **Policy** | Rules, compliance, guardrail rationale | `retrieve` scoped to the policy corpus, `read_guardrail_policy` |
| **Critic** *(sequential)* | Reviews the merged draft | none |

**P1.x — Tavily as the real search client.** *(1 d)*
- `aegis/src/aegis/retrieval/web.py` wrapping `tavily-python` behind a `WebSearchResult` type,
  added as an **optional extra** in `aegis/pyproject.toml` (a missing key degrades the Research
  agent to internal-only, loudly, never crashes).
- Results cached in **Memurai** keyed by a query hash with a TTL. This is a real use of the
  cache in the pipeline (which the user asked for), and it is also rate-limit and
  demo-day-wifi insurance.
- Needs a Tavily API key — **user dependency**, see §6.

**P1.y — The inter-agent guardrail.** *(1 d)* — **this is new and it is important.**
Today the rails run exactly twice: once on the user's input, once on the final answer. With
sub-agents that (a) pull arbitrary web content and (b) feed each other, the dangerous seam is
now *between* agents. Add a third rail stage, `GuardStage.TOOL_RESULT`, applied to every tool
result before it enters any agent's context — injection screening in particular on Tavily
content. Emits a `guardrail` event stamped with the `agent_id`, so the console shows the rail
firing inside an agent's log. Maps directly to OWASP LLM01 and the Agentic Top 10, and it is
one of the strongest things this plan adds to the security story.

#### Config (new `AgentConfig` knobs — all appear in the harness automatically)
`team_enabled`, `max_parallel_agents` (4), `max_concurrent_agents` (3),
`subagent_max_steps` (4), `subagent_timeout_s` (45), `team_wall_clock_s` (90),
`critic_enabled` (True).

`harness.py::_KNOB_SPECS` has a test asserting every `AgentConfig` field is described —
so each knob needs a `_KnobSpec` entry and gets a UI control for free.

#### Files touched
New `aegis/src/aegis/agent/subagent.py`, `team.py`;
new `aegis/src/aegis/retrieval/web.py`;
`aegis/src/aegis/agent/graph.py`, `deps.py`, `harness.py`, `topology.py`;
`aegis/src/aegis/guardrails/` (new stage);
`backend/src/app/adapter/roster.py`, `tools.py`; `backend/src/app/api/routes.py`.

#### Effort
**7–9 days.**

#### Demoable at this boundary
- Four agents genuinely running at once, streaming interleaved logs and tool calls, visible in
  the **existing** trace panel (which already renders every event) and in Phoenix as a real
  concurrent span tree. Not pretty yet — but provably real, which is the thing that has to be
  true before it is made pretty.
- A HIGH-risk action proposed by a sub-agent still stopping at the one human gate.
- An agent timing out and the answer honestly reporting "synthesised from 3 of 4 agents".

---

### Phase 2 — The unified console

**Goal.** The chat page the user described: clean before a query, alive during it, structured
after it. This is the primary consumer of Phases 0 and 1, and building it validates the
protocol.

#### Component architecture

Everything new lives under `web/src/components/console/`. `MoneyShotConsole.tsx` (233 lines,
a three-column bento of panels) is **not** extended — it is retired in favour of a chat shell
and its panels are re-homed as tab contents. Keeping it would fight the chat model.

```
ChatConsole.tsx              page shell: session rail | thread | composer
├── SessionRail.tsx          chat sessions from Postgres [DATA]; new/rename/delete
├── Mascot.tsx               the animated bot; eyes track the pointer
├── Thread.tsx               ordered turns
│   └── Turn.tsx             one user message + one assistant response
│       ├── AgentSwarm.tsx   ← the money shot: the grid of live agent cards
│       │   └── AgentCard.tsx    role, status pill, current action, tool chips, tokens/cost
│       │       └── AgentLog.tsx virtualised log tail (expanded card only)
│       ├── ActivityRail.tsx  graph/RAG/guardrail activity for supervisor-level events
│       └── ResultTabs.tsx    ← the tabs
└── Composer.tsx             text · image attach · model picker · depth · budget pill · stop
```

**`ResultTabs` — what goes where.** The user was explicit: main tab carries sources and what a
user actually wants; secondary detail gets its own tabs.

| Tab | Contents |
|---|---|
| **Answer** *(default)* | The answer, inline citations, the **sources list** with rerank scores and provenance, and a one-line trust summary (guardrails passed · confidence · cost). |
| **Agents** | The full swarm with every agent's complete log and tool calls. |
| **Retrieval & graph** | `RerankScoreboard`, the knowledge-graph delta, provenance, cache lineage. Re-homed from `MoneyShotConsole`. |
| **Guardrails & policy** | `GuardrailReveal`, the new tool-result rail verdicts, the tenant's own rules and Aegis's read-only defaults (Phase 5). |
| **Trace & cost** | `NodeGantt`, per-agent token/cost breakdown, the trace id, a link to Phoenix, and **Replay this run**. |

#### State model

`runReducer.ts` gains, and nothing else about it changes shape:

```ts
interface AgentRunState {
  agentId: string; role: string; label: string; task: string
  status: 'planned'|'running'|'ok'|'failed'|'timeout'|'cancelled'
  currentAction: string | null
  reasoning: string[]; toolCalls: ToolCall[]; toolResults: ToolResult[]
  guardrails: Guardrail[]
  usage: { prompt_tokens: number; completion_tokens: number; cost_usd: number } | null
  startedAt: number | null; durationMs: number | null
}
interface RunState {
  …existing…
  agents: Record<string, AgentRunState>
  agentOrder: string[]        // fixed at `agent_plan` time — cards never reflow
}
```

The routing rule is one line at the top of the reducer: **if `event.agent_id != null`, route
into `agents[id]` via a `withAgent(state, id, fn)` helper; otherwise fall through to today's
top-level handling, unchanged.** That is the entire structural change, and it is why Phase 0's
"identity on the base event" decision matters so much.

A second reducer, `threadReducer.ts`, holds the multi-turn chat: `turns: Turn[]`, each turn
owning its own `RunState`. Session load/save goes through `/sessions` **[DATA]**.

#### What makes concurrent agents legible — the research

From how Claude Code and Codex actually present parallel work
([Claude Code Agent View](https://code.claude.com/docs/en/agents),
[Agent View guide](https://claudefa.st/blog/guide/agents/agent-view)), plus the DeepSeek
Harness trajectory view, the patterns that carry:

1. **One fixed row/card per agent, reserved up front.** Claude Code's Agent View is literally
   "one table, one row per session". Cards are allocated on `agent_plan` and never reflow —
   reflowing logs are unreadable on a projector.
2. **Status word + one current-action line.** Each row shows status and *current action*, not
   a scrolling wall. `agent_status` exists exactly for this.
3. **Foreground / background.** Exactly one agent expanded and streaming; the rest collapsed
   to two lines. Click (or number key) to promote. This is the single biggest legibility win
   and it is why Agent View exists at all.
4. **Tool calls as discrete chips, not prose.** `🔧 web_search("refund SLA policy") → 5 results · 820 ms`,
   collapsed by default, expandable to full args and result. Prose tool logs are unreadable;
   chips are scannable at three metres.
5. **Real-time, never batched.** The view updates as events land. Our `seq`-ordered SSE gives
   this for free.
6. **Completion is visually final.** Card dims, gains a duration + cost badge. Failure gets a
   *designed* state, not a stuck spinner.
7. **Never fake latency.** If an agent is fast, it finishes fast. Padding for drama is exactly
   the "theatre" the user rejected.

Aesthetic: **light/white theme only** per the user's standing preference. Monospace for logs,
generous whitespace, one accent hue per agent role held consistent across the card, its log
lines, and its span in the trace tab.

#### Other console work items

- **Model selection.** `GET /models` derived from `gateway/routing.py` (role, deployment,
  small/large, unit cost). `model_preferences` table with `platform < tenant < user`
  resolution **[DATA]**. Composer picker shows the *effective* model per role with its source
  ("your default" / "tenant default" / "Aegis default") and a save-as-default control. The
  runtime seam is already in place from P0.5.
- **Image upload.** `POST /attachments` → `attachment_id`; the vision path already exists
  (`aegis/src/aegis/vision/`, `POST /vision/analyse` at routes.py:2787, and the
  `vision_screen` injection rail). Attachments join the run context; the vision screen fires
  as a visible guardrail event before analysis.
- **Budget visibility.** `GET /me/budget` returning the caller's own effective caps, usage and
  remaining. A `BudgetMeter` card on the client dashboard and a compact pill in the composer
  that ticks live off `node_finished`/`agent_finished` cost deltas.
- **Mascot.** Pure inline SVG. `pointermove` on `window`, pupil offset clamped to the iris
  radius, rAF-throttled, listener detached when off-screen, `prefers-reduced-motion`
  respected. Idle blink on a timer. Four expressions driven by `RunState.phase`:
  idle → thinking (pupils dart) → working (ring) → done/blocked.
- **Keyboard demo controls.** `1`–`5` focus an agent, `Esc` collapse, `⌘K` new chat,
  `⌘.` stop. Trivial to build; on a projector it is the difference between fumbling and fluid.
- **Remove the dummy content** the user called out — the console must be honestly empty
  before a query, and the mock/offline path must stay clearly labelled (the existing
  `OfflineBanner` pattern in `ConsoleMount.tsx` is the right precedent; keep it).

#### Files touched
New: `ChatConsole.tsx`, `SessionRail.tsx`, `Thread.tsx`, `Turn.tsx`, `AgentSwarm.tsx`,
`AgentCard.tsx`, `AgentLog.tsx`, `ActivityRail.tsx`, `ResultTabs.tsx`, `Composer.tsx`,
`ModelPicker.tsx`, `Mascot.tsx`, `BudgetPill.tsx`, `web/src/state/threadReducer.ts`.
Modified: `runReducer.ts`, `useRunStream.ts`, `ConsoleMount.tsx`, `liveTransport.ts`,
`web/src/lib/portal.ts`. Retired: `MoneyShotConsole.tsx` (panels re-homed, not deleted
wholesale — `DecisionStrip`, `AnswerPanel`, `RerankScoreboard`, `GuardrailReveal`,
`NodeGantt`, `ConfidenceCard`, `ShapPanel` all survive as tab contents).
Backend: `/models`, `/attachments`, `/me/budget`, `/sessions` in `routes.py` + `schemas.py`.

#### Effort
**10–13 days.** (The swarm UI alone is 4–5; the chat shell + sessions 3–4; model picker,
attachments, budget, mascot, keyboard ~3.)

#### Demoable at this boundary
**This is the money shot.** Type a question into a clean console; four agent cards appear
instantly; each fills with its own reasoning and tool-call chips in real time; a HIGH-risk
proposal stops the whole thing at the human gate; the critic reviews; the answer resolves into
tabs with sources first. Budget ticks. The mascot watches your cursor. Every number is real
and every one is replayable from Postgres.

---

### Phase 3 — Skills

**Goal.** Users add skills that genuinely shape how agents work, with real per-tenant and
per-user autonomy.

#### Adopt the open standard, do not invent one

Anthropic's Agent Skills shipped Oct 2025 and the spec was published as an **open standard at
agentskills.io** in Dec 2025; by March 2026 OpenAI, Microsoft, JetBrains, Cursor, Gemini CLI,
Goose and 25+ other products had shipped compatible implementations
([Firecrawl](https://www.firecrawl.dev/blog/agent-skills),
[Agentman](https://agentman.ai/blog/agent-skills-ecosystem-report-2026),
[SwirlAI](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure)).
A folder, a `SKILL.md` with YAML frontmatter (`name` + `description` required), a Markdown
body, and **three-tier progressive disclosure**.

Adopting it verbatim is worth real points with both juries: it is the current standard, it is
interoperable, and "we implement the open Agent Skills standard, and our skills are portable to
Claude, Cursor and Gemini CLI" is a much better sentence than "we invented a skill format".

#### The tool-vs-skill distinction, stated plainly

A **tool** is a capability the agent does not otherwise have — code we execute, gated by risk
tier and RBAC. A **skill** is *instructions* — how to use the capabilities it already has, in
this tenant's way. Tools extend reach; skills extend judgement. Aegis's risk gate applies to
tools, not skills, precisely because a skill can never do anything a tool would not already
have allowed. **That is the safety argument for letting tenants author skills freely while tool
registration stays a platform action**, and it should be said out loud in the UI.

#### Storage and scoping

```
skills (
  id, tenant_id null, user_id null,     -- NULL/NULL = Aegis platform default
  name, description, body, frontmatter jsonb,
  agent_roles text[],                   -- which sub-agents may load it; empty = all
  status  ('draft'|'active'|'archived'),
  version int, created_by, updated_at,
  unique (coalesce(tenant_id,0), coalesce(user_id,0), name, version)
)
skill_files (skill_id, path, content)   -- tier-3 reference files
```

Resolution: **user overrides tenant overrides platform, by `name`.** Postgres-backed, RLS on
`tenant_id`/`user_id` **[DATA]**. Platform defaults ship in the repo and are seeded on
migration — no runtime filesystem writes (which also keeps Windows deployment clean).

#### Progressive disclosure — and why it is also a demo

Three tiers, exactly as the standard defines them:

1. **Startup / prompt-assembly.** Every in-scope skill's `name` + `description` (~100 tokens
   each) is injected into the sub-agent's system prompt as a menu.
2. **Activation.** The model calls a **`load_skill(name)` tool** (LOW risk) to pull the body
   (target < 5 000 tokens).
3. **References.** `read_skill_file(name, path)` for tier-3 material.

Making activation a *tool call* rather than silent prompt-stuffing is the design the standard
intends, and it has a second payoff here: **skill loading becomes visible in the console as a
tool-call chip inside the agent's log.** The user sees their skill being picked up. That
converts skills from an invisible config into a demonstrable feature.

This replaces the current behaviour, which injects whole skill bodies into working memory
(`memory/recall.py:300-319`, `memory/working.py`). Keep the memory tier for *user-personal*
procedural skills (it is a legitimately different thing — durable personal preference) but
route *agent-shaping* skills through the new subsystem.

#### Runtime reach

- `AgentDeps` gains `load_skill_cards(scope) -> list[SkillCard]` and
  `load_skill_body(name, scope) -> str`, injected host-side like every other dep.
- `SubAgentSpec.skills` filters by `agent_roles`.
- Every activation emits a `tool_call`/`tool_result` pair stamped with the `agent_id`, plus a
  `skill_id` in the result payload so the harness can answer "which skills influenced this run".

#### Authoring UX

A **Skills** section in the portal (`web/src/lib/portal.ts` catalogue + a `SkillsMount`):
- List with scope badges (Aegis / tenant / mine) and an override indicator.
- Markdown editor with **frontmatter validation** and a live token-count meter against the
  5 000-token guidance.
- "Which agents see this" preview, computed from `agent_roles`.
- Enable/disable per skill, and **read-only view of Aegis platform defaults** (the same
  "tenants can read what we keep on" principle the user asked for on guardrails).
- **"Test on a query"** — runs one query with and without the skill and diffs the agent
  behaviour. This is the feature that makes skills feel real rather than declarative.

#### Effort
**6–8 days.** (Store + resolution + runtime 3; authoring UX 2–3; test-on-a-query 1–2.)

#### Demoable at this boundary
Write a skill in the UI ("when a customer is angry, always acknowledge before explaining"),
save it to your tenant, re-ask the same question, and watch the Policy agent call
`load_skill("de_escalation")` as a visible chip and change its behaviour. Then show the
platform default it overrode.

---

### Phase 4 — Real MCP

**Goal.** Aegis is an MCP server other systems can drive under proper RBAC, **and** an MCP
client that can consume any external MCP server — including its own — so the admin can query
the platform.

#### 4a — The Aegis MCP server, done properly

Build on `backend/src/app/mcp/server.py` (§1.5). The facade discipline is already right; what
changes is transport and identity.

- **Transport: Streamable HTTP**, mounted at `/mcp` on the existing FastAPI app. The MCP spec
  (2025-03-26) replaced HTTP+SSE with a single Streamable HTTP endpoint — JSON-RPC over POST,
  optionally upgrading to SSE for streaming ([MCP spec](https://modelcontextprotocol.io/specification/draft/basic/authorization),
  [MCP.Directory](https://mcp.directory/blog/oauth-21-for-remote-mcp-servers-streamable-http-explained-2026)).
  mcp SDK 2.0.0 is installed and supports it. Keep stdio as a second transport for local tools —
  same handlers, different mount.
- **Auth.** The spec's production answer is OAuth 2.1: MCP servers as OAuth **resource
  servers** validating tokens from an external AS, with PKCE, RFC 9728 protected-resource
  metadata, RFC 8414 AS metadata, RFC 8707 resource indicators, mandatory audience validation,
  and **no token pass-through to downstream APIs**
  ([Stack Overflow](https://stackoverflow.blog/2026/01/21/is-that-allowed-authentication-and-authorization-in-model-context-protocol/),
  [Descope](https://www.descope.com/blog/post/mcp-auth-spec)).
  **Recommendation: two steps.** Step 1 — accept the platform's existing bearer JWT via the
  same `require_auth` dependency, and serve RFC 9728 protected-resource metadata at
  `/.well-known/oauth-protected-resource` so discovery is already correct. Step 2 — a real
  OAuth 2.1 AS. Step 1 is honest and shippable; step 2 is a genuine roadmap milestone that
  answers the "production scaling" rubric with a specific, named standard rather than a
  hand-wave. Do **not** claim spec-compliant OAuth until step 2 lands.
- **Tenant scope carried per call, never in env.** Delete the `MCP_PERSONA_ID` global
  (server.py:90). Derive an `AuthContext` from the bearer per request, build a per-call
  `ToolContext(tenant_id, user_id, persona, role)`, and thread it into `run_tool`.
- **RBAC enforcement per caller.** `on_list_tools` returns the intersection of the persona
  allowlist and the caller's role→tool matrix — **a tool the caller may not use is never
  listed**, which is both a security property and an information-leak control. `on_call_tool`
  re-checks before any side effect (as it already does). Keep the HIGH-risk policy exactly as
  it is: listed, never auto-executed, routed to the approvals inbox. It is one of the best
  lines in the whole system.
- **Add MCP resources**, which are the natural fit for "let the admin ask Aegis about itself":
  `aegis://tenants/{id}/budget`, `aegis://tenants/{id}/audit`, `aegis://runs/{id}/trace`,
  `aegis://runs/{id}/events`, `aegis://skills`, `aegis://prompts/{key}/versions`.
  Every read RLS-scoped to the caller.
- **Audit every MCP call** with `via="mcp"` so the audit page can filter by channel.

#### 4b — The Aegis MCP client

Two consumers, one implementation.

- `aegis/src/aegis/mcp/client.py` — a general MCP client that connects to configured servers,
  lists their tools, and **adapts them into the platform's tool registry** with a risk tier
  assigned by policy (default: unknown external tool = HIGH, therefore gated — consistent with
  the existing "unrecognised tool counts as HIGH" rule at graph.py's gate). This is the bigger
  prize: any external MCP server becomes agent capability under the same gate, the same audit,
  the same allowlist.
- An **admin MCP console** section: connected servers, their advertised tools and resources,
  connection health, and a call form. Plus the loop the user asked for — the Aegis admin
  pointing the client at Aegis's own `/mcp` to ask questions of the platform in natural
  language, through the same agent, with the same RBAC.

**Server registry** in Postgres (`mcp_servers{id, tenant_id, name, transport, url, auth_ref,
enabled, tool_risk_default}`) so servers are added from the dashboard — which serves the
"almost zero code change, everything from the dashboard" goal directly. **[DATA]**

#### Effort
**7–9 days.** (HTTP transport + per-caller RBAC 3; resources 1; client 2; admin console 2.)

#### Demoable at this boundary
Connect Claude Desktop (or any MCP host) to Aegis over HTTP with a tenant-admin token; it sees
exactly that tenant's tools, calls a LOW-risk one and gets real data, tries a HIGH-risk one and
is told it went to the approval inbox — then the inbox shows it. Then flip it around: the Aegis
admin asks "which tenant burned the most budget this week?" in the console and watches an
agent answer it through the MCP client.

---

### Phase 5 — Harness, per-tenant LLMOps, tenant guardrails

**Goal.** The inspection and control surfaces. Almost all of this is *projection over Phase 0's
event log* plus fixing one real defect — so it is much cheaper than it looks.

#### 5a — The harness

Shaped after the DeepSeek Harness idea already adopted in §2.2: everything is a view over the
append-only log.

- **Run list** — filter by tenant / user / session / status / date / agent / cost.
  Scoping is enforced by RLS, not by UI: a tenant user sees their own; a tenant admin sees
  their tenant's; `ai_team`/`admin` may select any tenant or user. This is exactly the "for
  the AI team, select per user or per tenant; tenants can do the same for themselves"
  requirement, and it falls out of two columns.
- **Session inspector** — full transcript with every agent's log, tool calls and results,
  guardrail verdicts, model calls and costs, rendered per-agent.
- **Replay** (from P0.3) and **fork** — re-run a stored run's query with a changed config,
  model, or skill set, and diff the two runs side by side. Fork is where the harness stops
  being a log viewer and becomes an experimentation tool. It is also directly what the user
  meant by "see how self-improving prompts help them".
- **Config panel** — `harness_config()` already reflects every `AgentConfig` knob with type,
  bounds and docs (`agent/harness.py`), so this is a rendered form, not new modelling. Add
  per-tenant persistence of the knob values **[DATA]**.

#### 5b — Per-tenant LLMOps

- **Fix `_ACTIVE_CACHE` first** (§1.7): key it by `(tenant_id, prompt_key)` with a NULL-tenant
  platform fallback, and make `promote`/`rollback`/`refresh_cache` tenant-aware. Until this is
  fixed, *no* per-tenant prompt surface is truthful. Half a day, and it is a genuine bug.
- **Tenant prompt-version surface**: version list, diff between versions, active marker, the
  eval delta that gated each promotion (`ops/gate.py` already computes it), and rollback.
  Read-only for tenant users; promote/rollback for tenant admins under the existing approval
  gate.
- **The improvement story**, which is the thing the user actually wants tenants to see: for
  each promotion, the eval score before and after, on that tenant's own runs, joined from
  `eval_results` (already tenant-stamped, `ops/models.py:52`) and `run_events`.

#### 5c — Tenant guardrails

- `tenant_guardrails{tenant_id, kind, config jsonb, action, enabled, created_by}` **[DATA]**,
  evaluated **in addition to** the platform rails, never instead of them. Composition order:
  platform rails first (a tenant can never disable an Aegis rail), tenant rails second.
- **A read-only catalogue of Aegis platform defaults** — the user asked for this explicitly.
  Derive it from `aegis/src/aegis/guardrails/` config so it can never drift from what actually
  runs, and render each rail's name, stage, what it detects, and its default action. This is
  also a strong trust artefact for a jury.
- Surface both in the console's **Guardrails & policy** tab and as a portal section.

#### Effort
**6–8 days.**

#### Demoable at this boundary
A tenant admin opens the harness, filters to one of their users, opens a session, sees every
agent's tool calls, forks the run with a different system-prompt version, and watches the
eval score move. Then opens Guardrails and reads exactly which rails Aegis keeps on for them,
and adds one of their own.

---

### Phase 6 — Measurement and hardening

**Goal.** Turn "we have multi-agent" from a claim into a measured one, and make the system
survive a bad network and a busy gateway.

- **Trajectory evals for the team.** `aegis/src/aegis/ops/trace_eval.py` already runs as the
  `on_terminal` post-run hook (orchestrator.py:319). Extend it to grade the *plan*: were the
  right agents launched for this query? did the synthesis attribute claims correctly? did the
  critic catch anything? This is what converts multi-agent from a demo into a Business-Impact
  number, which is precisely what the 15% rubric line asks for.
- **Load and rate-limit hardening.** Adaptive concurrency: on a 429 from the gateway, drop the
  semaphore and back off, and surface a `degraded` badge rather than failing. Per-agent and
  per-run wall-clock ceilings enforced.
- **Cost attribution report.** Cost per agent, per role, per tenant, per skill — falls out of
  `agent_finished` rows in the event log.
- **A2A agent cards.** Publish a card per sub-agent (capabilities, tools, cost profile). The
  `a2a.*` OTel attributes are already stamped; the card is a small, concrete roadmap milestone
  toward interop.
- **Deterministic demo mode.** `?replay=<run_id>` re-streams a stored **real** run at real
  timings through the production reducer, clearly labelled as a replay. See §5 R1.

#### Effort
**4–6 days.**

---

### Phase 7 — Explicitly deferred (the roadmap slide)

Not because they are unimportant, but because they are not on the critical path and each is
a real project:

- OAuth 2.1 authorization server for MCP (step 2 of §4a).
- Sub-agent **spawning** sub-agents (recursive teams). The current design is deliberately one
  level deep; depth is where budgets and legibility both die.
- Distributing sub-agents across processes/workers.
- Skill marketplace / cross-tenant skill sharing with review.
- Streaming token-level output from the model (currently deliberately chunked *after* the
  output rail — `graph.py::stream_answer` documents exactly why, and it is the right call;
  changing it needs a streaming-aware output rail, not just a streaming gateway call).
- Resolving the two-protocol situation (AG-UI `AegisEmitter` vs `StreamEvent`) — see §6 D6.
- Voice input in the console (`aegis/src/aegis/voice/` exists and works).

---

## Part 4 — Dependencies on the data/governance plan

Flagged **[DATA]** above; collected here so nothing is assumed.

| Needed by | What is needed |
|---|---|
| P0.3 | `run_events` table, indexes on `(tenant_id, user_id, session_id, ts)`, RLS policies, retention/partitioning policy. Volume estimate: a team run emits roughly 150–400 events. |
| P0.5, P2 | `chat_sessions` + `chat_messages` in Postgres, tenant/user scoped. The console's session rail is blocked on this. |
| P2 | `model_preferences{scope, scope_id, role, deployment_id}` with platform < tenant < user resolution. |
| P2 | `attachments` storage (bytes on disk or bytea, metadata in Postgres) — no object store on a no-Docker box. |
| P3 | `skills` + `skill_files` tables with the composite uniqueness and RLS above. |
| P4 | `mcp_servers` registry table. |
| P5 | `tenant_guardrails`, per-tenant harness config persistence, and the tenant-scoped read path for `prompt_versions`. |
| All | The **tenant hierarchy** (Aegis admin → tenant → tenant admin → sub-roles) must be settled before RBAC is wired into the MCP server (§4a) and the harness scoping (§5a). This is the one dependency that can genuinely block Phase 4. |
| All | The user's "no SQLite fallbacks" directive means the `STORES=off` / lite mode that several modules degrade into needs an explicit decision from the data plan: keep it for tests only, or remove it. My default: **keep it for the pure package's offline tests, remove it as a runtime mode.** |

Reverse dependency worth stating: **the data plan should not design `run_events` without
reading §2.2** — the schema above is what every surface in this plan projects over.

---

## Part 5 — Risk register

Ordered by expected damage.

**R1 — Concurrent live model calls fail on stage (rate limit, latency, conference wifi).**
Four agents × a multi-step tool loop is 10–20 gateway calls in ~20 s, against a shared
hackathon gateway, plus Tavily.
*Mitigations:* semaphore default 3 with staggered launches; the existing `_MODEL_RETRY`
transient-retry policy (graph.py:1114) extended to sub-agents; Memurai caching on Tavily and
on sub-agent results; adaptive back-off with a visible `degraded` badge; and above all
**replay mode** (P0.3 / P6) — a stored real run re-streamed at real timings, honestly
labelled. Replay is the single highest-value insurance in this plan and it costs almost
nothing once the event log exists.

**R2 — Partial failure reads as a bug rather than as resilience.**
One agent times out and its card sits spinning; the audience concludes it is broken.
*Mitigations:* hard per-agent timeout with a **designed** terminal state ("Policy agent timed
out at 45 s"); the `synthesis` event names contributing **and omitted** agents; the answer
itself says "synthesised from 3 of 4 agents". Graceful, visible degradation is a scoring
positive under both Working Prototype and Business Impact — but only if it is designed, not
discovered live.

**R3 — Cost and token burn.**
Team mode is roughly 4–6× a single-pass run. Tokens are visible to the jury
(brief.md §2), so this cuts both ways: it can read as waste.
*Mitigations:* cheap-model routing for the Research and Data agents (`ModelRole.CHEAP`), the
answer cache and the Tavily cache doing real work, per-agent cost shown live so the *choice*
is visible, and the depth toggle so single-pass is one click away. Frame it honestly: parallel
agents buy latency and coverage, and the console shows exactly what they cost.

**R4 — Scope. This plan is roughly 45–60 engineer-days.**
That is the honest number for "properly". It is sequenced so that every phase boundary is
demoable, which is the mitigation: you can stop anywhere and still have a coherent system.

**R5 — The multi-agent design proves harder than the probe suggests.**
The `gather`-in-a-node approach is verified for streaming, but the interaction with
`interrupt()`/checkpointing under a mid-fan-out gate is the sharp edge.
*Mitigation:* the design constraint in §2.1 (no `interrupt()` inside a gathered task; HIGH-risk
work is *proposed*, never executed, by sub-agents) removes the interaction entirely. Prove it
with a test on day one of Phase 1: a sub-agent proposes a HIGH-risk action, the run gates,
parks, and resumes correctly.

**R6 — Prompt injection through the Research agent.**
Tavily pulls arbitrary web content directly into an agent's context, which then feeds the
synthesiser. This is a genuine new attack surface that the current two-rail design does not
cover.
*Mitigation:* the `GuardStage.TOOL_RESULT` rail (P1.y). Treat this as required, not optional.

**R7 — The two-protocol confusion is scored against us.**
The brief says an AI reader parses the repo. Two streaming primitives where only one is live
reads as incoherence.
*Mitigation:* document the split explicitly, or delete `AegisEmitter`. See §6 D6.

**R8 — Windows / 16 GB.**
Nothing in this plan needs Docker or a GPU. The two watch items are Memurai memory pressure
under heavy caching (cap the Tavily cache) and, outside this plan's scope, Docling's footprint
for RAG ingestion — flag it to the data/RAG plan.

---

## Part 6 — Decisions I cannot make alone (with my defaults)

**D1 — Is team mode the default, or opt-in?**
*Default:* an explicit **Depth** control in the composer (`Single` / `Team`), defaulting to
Team, with the router able to escalate a `single` request to `team` when the query is plainly
multi-part. An explicit control means a live demo can never be sabotaged by an unlucky routing
decision — which matters more than elegance.

**D2 — How many agents?**
*Default:* 4 concurrent (Research, Knowledge, Data, Policy) + 1 sequential Critic, with
`max_concurrent_agents = 3` so the gateway sees at most three in flight.

**D3 — MCP auth for the first release.**
*Default:* platform bearer JWT over Streamable HTTP, with RFC 9728 protected-resource metadata
served so discovery is already spec-shaped. Full OAuth 2.1 AS in Phase 7. Do not claim
spec-compliance before it lands.

**D4 — Skill storage.**
*Default:* Postgres only, platform defaults seeded from the repo at migration. No runtime
filesystem writes.

**D5 — Does the `memory` skills tier survive?**
*Default:* yes, but for a different purpose — personal procedural preferences stay in memory;
agent-shaping skills move to the new subsystem. If you would rather have exactly one skill
concept, say so and I will fold memory skills into the new store.

**D6 — What happens to `AegisEmitter` / AG-UI?**
*Default:* document it as the module-level emitter used by `aegis.*` packages and their tests,
and `StreamEvent` as the console wire; keep the mirror test but point a *second* test at the
`StreamEvent` union. **My actual recommendation is to delete `AegisEmitter` and
`stream_names.py`** — 22 names and a whole encoder maintained for tests and one demo route is
dead weight in a repo an AI reader scores. That is your call, not mine.

**D7 — Tavily API key.** Needed before the Research agent is real. Free tier is fine for
development. **User dependency — nothing else in Phase 1 blocks on it, but the Research agent
is a stub until it arrives.**

**D8 — Does `MoneyShotConsole` survive anywhere?**
*Default:* retired as a page, its panels re-homed as tabs. If you want to keep a
projector/"Present" mode as a separate route, say so — it is a different layout over the same
`RunState` and would cost ~2 days.

---

## Part 7 — What you missed (you asked for suggestions)

Ordered by value.

1. **The durable run-event log.** You asked for a harness; the enabling primitive is a
   persisted, append-only event stream. Without it, the harness, replay, per-agent inspection,
   audit depth and the tenant LLMOps evidence are five separate features. With it they are one
   table and five queries. This is the biggest idea in the plan and it is stolen wholesale from
   DeepSeek Harness, which you asked me to research. (§2.2)

2. **An inter-agent guardrail on tool results.** Your rails run on the user's input and the
   final answer. The moment agents pull web content and feed each other, the dangerous seam is
   *between* agents. This is the largest security hole the multi-agent work opens, and closing
   it is also one of the best sentences you will have for a jury: *"we guard the agent-to-agent
   seam, not just the user seam."* (§P1.y)

3. **A stop button, and per-agent budgets.** Four agents burning tokens with no way to stop
   them is both a live-demo hazard and something no enterprise buyer would accept. (§P0.4)

4. **Replay as demo insurance.** A stored *real* run, re-streamed at real timings through the
   production reducer, honestly labelled. This is not mock data and it is not cheating — it is
   the thing that means a dead network cannot cost you the Working Prototype score. (§P0.3)

5. **Sub-agents may propose HIGH-risk actions but never execute them.** You did not ask for
   this; it makes the multi-agent story *safer* than the single-agent one, because now there is
   provably exactly one gate no matter how many agents ran. Say it out loud on stage.

6. **Eval the team, not just the answer.** `ops/trace_eval.py` already runs post-run. Grading
   whether the *right* agents were launched turns "we have multi-agent" into a measured claim,
   which is exactly what Business Impact (15%) rewards and what most teams cannot produce.

7. **Three live events are invisible and one dead event is wired.** Your console already
   receives `reflection`, `routing` and `memory` and silently discards all three, and it has a
   whole unreachable `abstained` state. The self-repair loop in particular is a great thing to
   show and it is already running. (§1.3)

8. **Your live console never sends `session_id`, so long-term memory is inert today.** One
   line in `liveTransport.ts`. Worth knowing before anyone claims multi-turn memory. (§1.4)

9. **A real multi-tenancy defect in LLMOps.** `_ACTIVE_CACHE` is keyed by `prompt_key` alone,
   so two tenants cannot have different active prompts — last write wins for everybody. Fix
   this before building any per-tenant prompt surface. (§1.7)

10. **The MCP client matters more than the MCP server.** You framed MCP as exposing Aegis. The
    bigger win is Aegis *consuming* any external MCP server, with unknown external tools
    defaulting to HIGH risk and therefore landing at the human gate. That is "connect agents to
    do the work with proper role-based access" in the direction that actually grows
    capability — and it is a genuinely strong architecture claim. (§4b)

11. **Skills should be the open standard, not a bespoke format.** agentskills.io is a published
    open standard with 25+ compatible implementations. "Our skills are portable to Claude,
    Cursor and Gemini CLI" beats "we invented a skill format" with both the human jury and the
    AI reader — and it is less work.

12. **Make skill activation a visible tool call.** Progressive disclosure is the standard's own
    design, and here it has a second payoff: the user *sees* their skill being loaded in the
    agent's log. Silent prompt-stuffing would work and would be invisible.

13. **Keyboard-driven demo controls.** `1`–`5` to focus an agent, `Esc` to collapse, `⌘.` to
    stop. Sounds trivial. On a projector it is the difference between fumbling and fluid, and
    Articulation is 15%.

14. **Per-agent OTel spans so Phoenix mirrors the console.** The `a2a.*` semconv constants are
    already there and already used by the router. Making the trace tree match the console is
    the cheapest possible proof that the concurrency is not theatre — and a jury that asks
    "is this real?" gets an answer from a third-party tool rather than from your own UI.

15. **Decide the fate of `AegisEmitter`.** Two streaming protocols where one is dead is the
    kind of thing an AI reader scoring your repo will notice. Either document the split
    deliberately or delete it. (§6 D6)

---

## Appendix A — The concurrency probe (VERIFIED)

Run against `backend/.venv`, langgraph 1.2.11, on 2026-08-16. This settles Decision A (§2.1).

```python
async def worker(name, delay):
    w = get_stream_writer()          # inside an asyncio task spawned by gather
    for i in range(3):
        await asyncio.sleep(delay)
        w({"type": "log", "agent": name, "i": i})
    return f"{name}-done"

async def fanout(state):
    res = await asyncio.gather(*[worker(n, d) for n, d in
                                 (("research", 0.01), ("policy", 0.015), ("data", 0.005))])
    return {"out": res}
```

Observed output — genuinely interleaved, in real time, from three concurrent tasks inside a
single LangGraph node:

```
custom {'type': 'log', 'agent': 'data',     'i': 0}
custom {'type': 'log', 'agent': 'research', 'i': 0}
custom {'type': 'log', 'agent': 'data',     'i': 1}
custom {'type': 'log', 'agent': 'policy',   'i': 0}
custom {'type': 'log', 'agent': 'data',     'i': 2}
custom {'type': 'log', 'agent': 'research', 'i': 1}
custom {'type': 'log', 'agent': 'policy',   'i': 1}
custom {'type': 'log', 'agent': 'research', 'i': 2}
custom {'type': 'log', 'agent': 'policy',   'i': 2}
updates {'fanout': {'out': ['research-done', 'policy-done', 'data-done']}}
```

The same probe also confirmed `Send`-based fan-out streams correctly
(`{'agent': 'a', 'via': 'Send'}`, `{'agent': 'b', 'via': 'Send'}`), and that
`CompiledStateGraph.astream` accepts a `subgraphs` parameter — so all three options work.
The recommendation rests on blast radius, not capability.

**Conclusion:** `get_stream_writer()` propagates through contextvars into `gather`-spawned
tasks. Concurrent sub-agents can each stream their own live event log with no LangGraph API
change, no `astream` tuple-shape change, and no state-reducer churn.

---

## Appendix B — Sources

- [DeepSeek Harness — developer preview](https://deepseek.com/harness/en/) · [The New Stack coverage](https://thenewstack.io/deepseek-harness-open-source-plugins/) — append-only SessionEvent log as the substrate; resume/fork/replay/transcripts/telemetry/UI as projections.
- [Agent Skills explained (Firecrawl)](https://www.firecrawl.dev/blog/agent-skills) · [Agent Skills ecosystem 2026 (Agentman)](https://agentman.ai/blog/agent-skills-ecosystem-report-2026) · [Progressive disclosure as a system design pattern (SwirlAI)](https://www.newsletter.swirlai.com/p/agent-skills-progressive-disclosure) — SKILL.md, three-tier disclosure, open standard status.
- [MCP authorization specification](https://modelcontextprotocol.io/specification/draft/basic/authorization) · [Is that allowed? Auth in MCP (Stack Overflow)](https://stackoverflow.blog/2026/01/21/is-that-allowed-authentication-and-authorization-in-model-context-protocol/) · [Diving into the MCP authorization spec (Descope)](https://www.descope.com/blog/post/mcp-auth-spec) · [OAuth 2.1 for remote MCP servers (MCP.Directory)](https://mcp.directory/blog/oauth-21-for-remote-mcp-servers-streamable-http-explained-2026) — Streamable HTTP, OAuth 2.1 resource-server model, RFC 9728/8414/8707.
- [Run agents in parallel — Claude Code docs](https://code.claude.com/docs/en/agents) · [Claude Code Agent View](https://claudefa.st/blog/guide/agents/agent-view) · [Managing multiple agents (MindStudio)](https://www.mindstudio.ai/blog/claude-code-agent-view-manage-multiple-agents) — one row per agent, status + current action, foreground/background, real-time updates.
