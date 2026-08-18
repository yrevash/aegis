# Phase 5 — Adaptive multi-agent

> ## Amendments of 2026-08-19 — four rulings, read before the body
>
> These are decisions from the user and they override the body where they disagree.
>
> ### A. The user's width is the user's decision
>
> The classifier decides **only in Auto**. In an explicit mode the user's number wins, and
> the platform does not second-guess it. **We optimise tokens as a platform rather than by
> restricting the person paying for them.**
>
> The optimisations are therefore all supply-side and none of them are refusals: the answer
> cache, `ModelRole.CHEAP` for the agents that do not reason, the Tavily cache, **one shared
> retrieval pool per run** (fanning out four agents must not retrieve the same chunks four
> times), and de-duplicating overlapping sub-tasks before dispatch rather than after.
>
> The one gate that remains is the **tenant's own budget**, and that is not us overriding the
> user — it is the tenant admin's cap, enforced where every other spend is enforced.
> `BudgetExceededError` inside a gathered task still terminates the run as `blocked`.
>
> ### B. Fallback is a first-class requirement, and it must respect the tier
>
> `aegis/src/aegis/gateway/llm.py` already carries per-role fallback chains
> (`_DEFAULT_ROLE_FALLBACKS`, overridable via `configure(fallbacks=...)`), routed through
> LiteLLM. **This phase does not build a second fallback mechanism.** It adds the three
> things multi-agent needs and single-agent did not:
>
> 1. **A circuit breaker.** A deployment that fails repeatedly is marked degraded and skipped
>    for a cooldown, instead of every one of N concurrent agents independently discovering it
>    is down and each paying the timeout. With a fan-out, a dead provider costs N times what
>    it used to.
> 2. **Fallback must be visible.** It emits an event and logs at ERROR. A silent downgrade is
>    the same defect class as the budget hook that skipped the database when no context was
>    bound, and as a reranker that quietly stops reranking.
> 3. **Fallback must not escape the tenant's model allowlist.** A tenant entitled to CHEAP
>    must not be silently upgraded to GENERATION because the cheap deployment was down — that
>    is a spend decision made on the tenant's behalf. If every model in tier is unavailable,
>    the run fails loudly.
>
> ### C. A real harness for every agent, and the LLM-Ops loop reaches sub-agents
>
> Both surfaces already exist and neither is to be rebuilt:
>
> - `aegis/src/aegis/agent/harness.py` — `harness_config()` (every `AgentConfig` knob as a
>   typed, bounded descriptor) and `run_summary()` (the ordered event stream folded into one
>   record). Because `run_summary` consumes the emitted events verbatim, the "how it worked"
>   record cannot diverge from what streamed.
> - `aegis/src/aegis/ops/models.py` — `PromptVersion` / `PromptStatus`
>   (`draft → staged → active → archived`), with the registry cache and the eval gate. This
>   **is** the LLM-Ops loop; it exists and is unused by sub-agents.
>
> What this phase adds:
>
> - **Per-agent trace.** `run_summary` gains an agent dimension, so the harness renders one
>   record per sub-agent rather than one blurred record for the run. This falls out of §5.4's
>   `agent_id` almost free — the events are already stamped.
> - **Every sub-agent prompt is a `prompt_key` in the registry**, so a system prompt is
>   improved by promoting a version through the existing gate, not by editing a string in a
>   file. The adapter prompt stays the floor when no active version exists.
> - **Every new knob gets a `_KnobSpec`** (already in the Definition of done) — a knob with no
>   spec is a control the harness cannot show and nobody can tune.
> - **User memory and skills reach sub-agent context.** The main graph already resolves
>   `memory_subject`, renders the profile and selects skills; a sub-agent that cannot see the
>   user's durable facts is a worse agent than the single one it replaced. The selection is
>   the adapter's (`memory_spec.render_profile` / `select_skills`), not a second copy.
>
> ### D. `agent_id` is confirmed as §5.4 and is load-bearing for all of the above
>
> It is simultaneously the wire field, the `run_events` column Phase 3 already created, and
> the key the per-agent harness record groups on. Phase 3 built the table; this phase fills
> the column and must not build a parallel log.

**3 days. The money shot, done honestly.**

**Depends on Phase 3 and Phase 4.** Per-agent logs are `run_events` rows
([`phase-03-platform-spine.md`](phase-03-platform-spine.md) §3.6) — this phase adds `agent_id`
to the event schema and does **not** invent a storage channel for agent output. Phase 4 gives
the Knowledge agent a corpus worth retrieving from.

**Phase 6 owns the manual override; this phase owns the field it writes to.** The composer's
mode control (`phase-06-console.md` §6.9) sets `effective_depth`; the classifier below is what
happens when it says `AUTO`. Build the classifier to be overridden — see task 5.1.

Read the constraint before the design, because the constraint *is* the design:

> "based on the query it should automatically think if agents should be launched — if simple
> answer then simple answer, no extra stuff... we have some headroom but not extreme
> headroom... $100 worth of credits... let's be mature and have the right balance."

So this phase **leads with a classifier that decides whether to fan out at all**, and only
then builds the fan-out. Five agents on "what is my budget" is not impressive. It is
wasteful, it is slow, and at $100 of gateway credit it is also expensive.

The thing to demo is not "look, four agents". It is: *ask a simple question, get a simple
answer in one pass; ask a hard one, watch four agents go.* The contrast is the demo.

---

## What is actually wrong

### 1. Nothing in the graph runs in parallel

`aegis/src/aegis/agent/graph.py` compiles one `StateGraph` with a strictly linear hot path.
Every edge is sequential. `act` executes its tool calls in a plain `for` loop
(`graph.py:891`).

The "supervisor" is the `route` node calling `aegis/src/aegis/agent/router.py::route_query`.
It is a **deterministic keyword classifier that picks exactly one role**, with a cheap-LLM
tiebreak only on a genuine tie. `SPECIALIST_NODES` (`graph.py:126-129`) maps exactly two:

```python
SPECIALIST_NODES: dict[str, str] = {
    "qa": "recall_memory",
    "memory": "answer_memory",
}
```

Today a query is: one plan call, N serial tool calls, one generate call. One identity, one
log, one lane.

### 2. The accumulator work is already done, which is the good news

`AgentState` (`aegis/src/aegis/agent/state.py:123,138-140`) already carries `operator.add`
reducers on `plan_iterations`, `prompt_tokens`, `completion_tokens`, `cost_usd`, and its
docstring at `state.py:11` says outright that they exist so state remains correct when nodes
each contribute a share — a fan-out that does not exist yet. Fan-out costs no state-schema
churn.

### 3. The concurrency question is settled — do not reopen it

Plan 02 ran a probe against the installed **langgraph 1.2.11** on 2026-08-16 (its Appendix
A). `get_stream_writer()` **propagates through contextvars into `asyncio.gather`-spawned
tasks**. Three concurrent workers inside a single node emitted live, interleaved custom
events in real time.

**Decision: fan out with `asyncio.gather` inside one node.** Not because it is easier —
because subgraphs would change `astream`'s yielded tuple from `(mode, chunk)` to
`(namespace, mode, chunk)`, rewriting the orchestrator hot loop *including* the
`__interrupt__` detection and `graph.get_state(config)` calls that make the human gate
durable. That is high blast radius on the one piece of code that must not break.

**The constraint that falls out, and it is not negotiable:**

> **No `interrupt()` inside a gathered task.** Sub-agents *propose* HIGH-risk actions. The
> main graph's single `gate → approval → act` path executes them.

This is a security improvement, not a limitation. No concurrent agent can ever take a
consequential action without passing the one gate.

### 4. Three real events are already on the wire and invisible

`routing`, `reflection` and `memory` are emitted by the backend
(`backend/src/app/api/schemas.py:324,347,367`, all three in the `StreamEvent` union at
`schemas.py:389`) and are **not in `web/src/lib/stream.ts`'s union at all** — that union
(`stream.ts:268`) carries fifteen variants and none of the three. `runReducer.ts` ends with
`default: return next`, so they land in `state.events` and are dropped.

Two consequences. First, additive protocol changes are provably safe: an unknown `type`
never breaks the client, so backend can land before frontend. Second, the `routing` event
(`aegis/src/aegis/agent/events.py:217-230`) is exactly where the depth decision belongs, and
rendering it is free demo value already sitting on the floor. **Phase 6 task 6.1 does the
rendering; this phase puts the fields on the event.**

### 5. The rails cover two seams, and we are about to open a third

`GuardStage` is a two-value enum:

```python
# aegis/src/aegis/core/types.py:52
class GuardStage(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
```

Mirrored at `web/src/lib/stream.ts:25`. The rails run exactly twice: once on the user's
input, once on the final answer. Tavily pulls **arbitrary web content** straight into an
agent's context, which then feeds the synthesiser. Nothing screens it.

Plan 02 calls the third stage **required, not optional** (its R6). It is carried here as a
task, not a nice-to-have.

### 6. The Tavily key is in `backend/.env` — spelled wrong

```
TRAVILY_API_KEY=...
```

"Travily". Not a blocker, but find it now rather than at 2am. There is no `tavily` package in
either `pyproject.toml` and no `tavily` reference anywhere in `aegis/src`, `backend/src` or
`web/src`. This is greenfield too.

---

## What we are fixing now, and what waits

| | |
|---|---|
| **Now** | A cheap classifier that decides fan-out width — including width zero, and **overridable by Phase 6**. |
| **Now** | Genuinely concurrent sub-agents with their own prompts, tool allowlists, budgets and failure containment. |
| **Now** | `agent_id` on the event base — which is simultaneously the wire field and the `run_events` column. |
| **Now** | A synthesis event naming contributing **and omitted** agents. |
| **Now** | Per-agent timeout as a *designed* terminal state. |
| **Now** | Tavily behind a seam, cached in Memurai. |
| **Now** | `GuardStage.TOOL_RESULT`. |
| **Now** | A circuit breaker over the existing gateway fallback, **visible** and **bounded by the tenant's tier** (§5.8). |
| **Now** | A per-agent harness record, sub-agent prompts in the LLM-Ops registry, and the user's memory in sub-agent context (§5.9). |
| **Now** | The user's explicit width is honoured; the classifier decides **only in Auto** (Amendment A). |
| **Already done, in Phase 3** | The durable `run_events` log itself — table, partitioning, RLS, the `runs` header, and the sink at the `emit()` seam (§3.6). This phase **fills its `agent_id` column**; it does not build it, and it must not build a parallel one. |
| **Waits** | Replay mode. The events are durable from Phase 3, so replay is a Phase 6 read surface (`GET /runs/{id}/events` re-streamed through the same reducer), not new machinery. It is the best demo insurance in the plan. |
| **Waits** | The console rendering of any of this. Phase 6. Agree the event shape with it **before** starting 5.4. |
| **Waits** | Skills attached to sub-agents. The mechanism already exists (filesystem markdown, adapter-selected); wiring it per-agent is not a 3-day item. |
| **Waits** | MCP. Excellent, not required to win a blind problem. |

### The cut, decided now

Per the master plan: **if we slip, the team drops from 4 agents to 2 — research +
synthesise.** Still genuinely concurrent (research runs while knowledge retrieval runs),
still genuinely visible, half the work. Tasks 5.1–5.5 and 5.7 are load-bearing at any width.
Task 5.6's roster entries are what shrink.

---

## Tasks

| # | Task | Days | Note |
|---|---|---|---|
| 5.1 | Depth classifier **+ the override seam** | 0.5 | Do this first. Defaults SINGLE on every failure path; the seam is what Phase 6's mode control writes to |
| 5.2 | One sub-agent, one bounded loop | 0.75 | Allowlist ∩ persona through the *same* `is_allowed`. Never raises. Write the gate test on day one |
| 5.3 | The fan-out node | 0.5 | `return_exceptions=True` always; one summed delta; the `qa` path stays byte-identical |
| 5.4 | `agent_id` on every event | 0.25 | Fills the `run_events` column Phase 3 built. No parallel log |
| 5.5 | Synthesis + timeout as a designed state | 0.25 | Names contributing **and omitted** agents |
| 5.6 | Tavily behind a seam, cached | 0.5 | The key in `backend/.env` is spelled wrong — that is why search has never worked |
| 5.7 | `GuardStage.TOOL_RESULT` | 0.25 | Tool output passes a rail **before** it reaches any agent's context |
| **5.8** | **Fallback that survives a fan-out** | 0.4 | Circuit breaker + visible + tier-bounded. Extends the gateway's existing chains; builds no second mechanism |
| **5.9** | **A real harness for every agent** | 0.5 | Per-agent trace, sub-agent `prompt_key`s, user memory in context. Extends `harness.py` and `ops/`; rebuilds neither |

**Total: ~3.9 days.**


### 5.1 — The depth classifier (0.5d) — **do this first**

This is the task the user actually asked for, and building it first stops the fan-out from
becoming the default by accident.

Extend `RouterDecision` (`aegis/src/aegis/agent/router.py:42-54`, currently three fields:
`role`, `reason`, `used_llm`) with the width:

```python
@dataclass(frozen=True)
class RouterDecision:
    role: str
    reason: str
    used_llm: bool = False
    depth: Depth = Depth.SINGLE   # SINGLE | TEAM
    fanout: int = 0               # 0 for SINGLE; 2..max_parallel_agents for TEAM
```

Keep the module's existing discipline: **deterministic first, model only on ambiguity.**
That is already how `route_query` works and it is the right shape here too.

- Deterministic SINGLE: short queries, self-referential ones, anything the keyword pass
  already routes to `memory`, anything that hits the answer cache.
- Deterministic TEAM: explicit multi-part questions ("compare X and Y and tell me Z"),
  queries naming external/current information, queries above a length/clause threshold.
- Ambiguous → **one `ModelRole.CHEAP` call** (`aegis/src/aegis/core/models.py:20`) returning
  a width, with a hard fallback to SINGLE. The classifier must never be the reason a run
  dies, and it must never be the reason a run gets expensive.

**Default to SINGLE on every failure path.** A broken classifier that quietly fans out is
the exact failure the budget cannot absorb.

#### The classifier must be overridable — build the seam now, not later

Phase 6's composer ships a mode control (Auto · Fast · Deep · Team · Custom). **Phase 6 owns
the control; this phase owns the field it writes to**, and retrofitting an override into a
classifier that assumed it was the only decider is a rewrite of the `route` node.

The rule, and it is one line:

```
effective_depth = user_mode if user_mode != AUTO else classifier_decision
```

**Manual wins.** Three consequences that belong in this task, not Phase 6's:

1. **The classifier is skipped, not overruled after the fact,** when `user_mode != AUTO`.
   `Fast` must not pay for the cheap-model tiebreak it is trying to avoid.
2. **A manual choice can be narrowed by the platform, never widened by the user.** If
   `max_parallel_agents=4` and a user pins 6, they get 4. `Custom` is not a way around a
   budget cap, and the clamp lives here — in the same place the cap is read — rather than in
   the browser.
3. **The `routing` event carries `decided_by`** so the screen can name who decided:

   ```python
   # aegis/src/aegis/agent/events.py:217 — routing() gains, alongside depth and fanout
   decided_by: Literal['auto', 'user', 'tenant_default', 'platform_cap']
   ```

   The trace then reads *"TEAM ×3 — you selected Team mode"* or *"SINGLE — single-intent
   query, answering in one pass"*. **Never a width with no explanation.**

**The failure default is SINGLE on both paths.** If the settings resolver cannot read a mode,
if the classifier throws, if the roster is empty — SINGLE. The manual path must not introduce
a second, more permissive default than the automatic one.

Put `depth`, `fanout` and `decided_by` on the existing `routing` event (`events.py:217-230`,
today `{type, role, reason, used_llm}`). The reason string is already demoable and glass-box —
"3 sub-questions detected, fanning out to 3 agents" and "single-intent query, answering in one
pass" are both good things to have on screen, and the second one is the one that shows
maturity.

New `AgentConfig` knobs: `team_enabled`, `max_parallel_agents` (4),
`max_concurrent_agents` (3), `subagent_max_steps` (4), `subagent_timeout_s` (45),
`team_wall_clock_s` (90).

`aegis/tests/agent/test_harness_config.py:44,55` asserts a bijection between `AgentConfig`
fields and `harness.py::_KNOB_SPECS`. Every knob needs a `_KnobSpec` — and in exchange each
one gets a harness UI control for free. `max_parallel_agents` is also the platform cap Phase 6
clamps against, so it becomes a Phase 3 §3.7 settings-catalogue entry (`agent.team.max_parallel`,
`merge: tighten_only`) rather than a second copy of the number.

### 5.2 — One sub-agent, one bounded loop (0.75d)

New `aegis/src/aegis/agent/subagent.py`.

```python
SubAgentSpec(agent_id, role, label, system_prompt, tool_allowlist,
             model_role, max_steps, timeout_s)

async def run_subagent(spec, task, *, deps, writer, cancel) -> SubAgentResult
```

A small ReAct-shaped loop, ~200 lines, reusing the deps that already exist: `deps.complete`,
`deps.run_tool`, `deps.tool_definitions_for`, `deps.retrieve`. Per step it emits
`agent_status` → `reasoning` → `tool_call`/`tool_result` through the scoped writer.

Invariants — **all enforced in code, none in a prompt:**

- Tools are the spec's allowlist **intersected with** the persona allowlist
  (`backend/src/app/adapter/tools.py:462::is_allowed`). A sub-agent can never widen its own
  reach. **Phase 6's tool pins go through this same function** — one intersection in the
  codebase, not two, because two is how the second one ends up subtly more permissive.
- Any tool at or above `config.gate_min_risk` (`aegis/src/aegis/agent/deps.py:135`, default
  `HIGH`) is **not executable here**. It is returned in `SubAgentResult.proposed_actions` and
  flows into the main graph's gate. Today that is exactly one tool —
  `update_request_status`, `RiskLevel.HIGH` (`adapter/tools.py:425-434`).
- `max_steps` hard cap. `asyncio.wait_for(timeout_s)`.
- **Never raises.** Every failure becomes `SubAgentResult(status=failed|timeout, error=…)`
  — except `BudgetExceededError` (`aegis.gateway.types`), which is captured and re-raised
  after fan-in so the orchestrator's existing handler
  (`aegis/src/aegis/agent/orchestrator.py:334`) still terminates the run cleanly as blocked.
- Its own token/cost totals, returned for the node's summed delta.

**Write the gate test on day one of this task**, before the fan-out exists: a sub-agent
proposes a HIGH-risk action, the run gates, parks, resumes correctly. That interaction is
the sharp edge of this whole phase and the design constraint in §3 is what removes it — prove
the removal rather than assuming it.

### 5.3 — The fan-out node (0.5d)

New `aegis/src/aegis/agent/team.py`, plus the graph wiring.

```python
SPECIALIST_NODES = {
    "qa":     "recall_memory",   # unchanged — byte-identical to today
    "memory": "answer_memory",   # unchanged
    "team":   "plan_team",       # new
}
```

```
route ─(team)→ plan_team → run_team → synthesize → gate → approval → act → …
```

`plan_team` turns the **effective** width — the classifier's, or the user's from Phase 6 —
into a task list against the sub-agent roster (one cheap model call, with a deterministic
keyword fallback). In `Custom` mode the roster selection *is* the task list and the model call
is skipped entirely. `run_team` is the `asyncio.gather`, under
`asyncio.Semaphore(config.max_concurrent_agents)`, launches staggered ~250 ms to avoid a burst
against the gateway.

Three rules:

1. **`return_exceptions=True`, always.** One agent's failure must never cancel its siblings.
2. **The node returns one summed delta.** Because the gather is inside a node, it returns a
   single `{prompt_tokens, completion_tokens, cost_usd}` and the existing `operator.add`
   reducers keep working untouched.
3. Extend `_MODEL_RETRY` (`graph.py:1112`, `RetryPolicy(max_attempts=3)`) to sub-agent model
   calls.

The team path lands on the **existing** `gate → approval → act → reflect → generate →
guard_output → stream → persist_memory` tail. The human gate, the output rail, the answer
cache and memory persistence all keep working untouched, and **the `qa` path stays
byte-identical**, which is what keeps the golden-trace tests green.

`aegis/src/aegis/agent/topology.py` compiles the graph over inert deps, and
`backend/tests/api/test_agent_topology.py:109` asserts `web/src/config/graphTopology.json`
equals the served topology. **Regenerate that snapshot** when you add the three nodes, same
as in Phase 2.

### 5.4 — Agent identity on every event, and therefore on every `run_events` row (0.25d)

This is what makes the per-agent logs and tool calls real rather than a UI grouping guess.

**There is no per-agent log channel to build.** Phase 3 §3.6 already persists every stamped
event to `run_events`, and that table was designed with the column this task fills:

```
run_events (
  run_id      uuid,
  seq         int,
  ts          timestamptz,
  tenant_id   int,          -- RLS anchor
  user_id     int,          -- RLS anchor
  session_id  uuid,
  agent_id    text null,    -- NULL = supervisor / graph-level   ← this task
  type        text,
  payload     jsonb,
  primary key (run_id, seq)
)
```

So "per-agent logs" is `WHERE agent_id = …` over a table that already exists, tenant-scoped by
an RLS policy that already exists, durable across a restart because Phase 3 made it so. **One
optional field on the wire is the entire per-agent-log requirement.** Anything else — a
per-agent buffer, a second stream, a log file — is a fourth tracking mechanism and Phase 3 §2
already argues against a fourth.

The orchestrator stamps every event through an **injected `stamp` callable**
(`orchestrator.py:66`, called from `emit` at `:169-173`) so the pure package never imports the
host schema. That seam is exactly where identity is enforced, and it is the same seam the
Phase 3 `run_events` sink hangs off — so the wire field and the column are populated by one
change, not two.

- `run_subagent` writes through a writer bound to its `agent_id`, so every event a sub-agent
  emits carries it automatically. Do not ask each call site to remember.
- `agent_id: str | None` becomes an optional field on the shared event base
  (`_BaseEvent`, `backend/src/app/api/schemas.py`). **`None` means supervisor / graph-level**,
  which keeps every existing event valid and unchanged — the eighteen variants in the union at
  `schemas.py:389` need no other edit.
- Mirror it on the TS side in `web/src/lib/stream.ts` (the `BaseEvent` interface; the union is
  at `stream.ts:268`).
- **Index `(run_id, agent_id)`** on `run_events` in the same change, or the per-agent
  projection is a partition scan on a partitioned table.

### 5.5 — Synthesis, and timeout as a designed state (0.25d)

`synthesize(results, deps) -> str` — one model call merging the agents' findings. The
synthesiser prompt is told to attribute claims to the agent that produced them.

Emit a `synthesis` event that names **which agents contributed and which were omitted**, and
say it in the answer text too: *"synthesised from 3 of 4 agents; the policy agent timed out
at 45 s."*

This is not politeness. Partial failure otherwise reads as a bug: one agent times
out, its card sits spinning, and the audience concludes the thing is broken. A hard per-agent
timeout with a **designed** terminal state turns that into visible, graceful degradation,
which scores under both Working Prototype and Business Impact — but only if it is designed,
not discovered live.

The critic pass from plan 02 (a fifth, sequential agent reviewing the merged draft) is a
genuine quality control and it is **out of scope at 3 days**. Note it in the backlog. Do not
pad the agent count to hit "4-5"; the user asked for the right balance, not a number.

### 5.6 — Tavily as the real search client (0.5d)

New `aegis/src/aegis/retrieval/web.py`, wrapping `tavily-python` behind a `WebSearchResult`
type. **Optional extra** in `aegis/pyproject.toml` — a missing key degrades the Research
agent to internal-only **loudly**, and never crashes.

Cache results in **Memurai**, keyed on a query hash with a TTL. This is a real use of the
cache inside the pipeline, which the user asked for, and it is also rate-limit and
conference-wifi insurance. Cap the cache size — plan 02's R8 flags Memurai memory pressure on
a 16 GB box.

Fix the `TRAVILY_API_KEY` spelling in `backend/.env` and `backend/.env.example`, or read the
misspelling deliberately and comment why. Do not leave it ambiguous.

**The reference team.** Adapter-owned: `backend/src/app/adapter/roster.py` grows a
`SubAgentRoster` beside the existing `AgentRoster` — domain-agnostic mechanism,
domain-specific content, which is the seam discipline that file already documents.

| Agent | Does | Tools | Model |
|---|---|---|---|
| **Research** | External evidence | `web_search` (Tavily) | `CHEAP` |
| **Knowledge** | Internal corpus + graph | `retrieve` | `CHEAP` |
| **Data** | Structured records | the adapter's LOW/MEDIUM read tools | `CHEAP` |
| **Policy** | Rules, compliance, guardrail rationale | `retrieve` scoped to policy corpus | `REASONING` |

**If we cut to two, keep Research and Knowledge.** They are the pair that is visibly
concurrent (a slow web call overlapping a slow retrieval) and they are the pair a judge
cares about.

### 5.7 — The `TOOL_RESULT` guardrail stage (0.25d)

Required, not optional.

- `aegis/src/aegis/core/types.py:52` — add `TOOL_RESULT = "tool_result"` to `GuardStage`.
  Mirror in `web/src/lib/stream.ts:25`. The `Guardrail` event schema
  (`backend/src/app/api/schemas.py:181-201`) already carries `stage: GuardStage`, so the wire
  needs no other change.
- `aegis/src/aegis/guardrails/pipeline.py` — a `check_tool_result` path reusing the input
  rail chain (injection screening above all). `Guardrails.__init__` already takes
  `input_rails`/`output_rails` (`:118-135`), so this is a third entry point over existing
  machinery, not a new pipeline.
- Apply it to **every tool result before it enters any agent's context** — Tavily content in
  particular.
- Emit the `guardrail` event stamped with the `agent_id` from task 5.4, so the console shows
  the rail firing *inside* an agent's log.

This maps directly to OWASP LLM01 and the Agentic Top 10, and it is one of the strongest
things this phase adds to the security story. It is also 0.25 days.

---

### 5.8 — Fallback that survives a fan-out (0.4d)

**Do not build a second fallback mechanism.** `aegis/src/aegis/gateway/llm.py` already has
per-role chains (`_DEFAULT_ROLE_FALLBACKS`, `_effective_fallbacks()`, overridable via
`configure(fallbacks=...)`) routed through LiteLLM. This task adds only the three things a
fan-out needs and a single agent did not.

**1. A circuit breaker, because a dead provider now costs N timeouts, not one.**

With one agent, a down deployment costs one timeout and the chain moves on. With four
concurrent agents, all four independently discover it is down and each pays the full
timeout — the failure gets *more* expensive exactly when the system is already degraded.

Track consecutive failures per deployment id. Past a threshold, mark it degraded and skip it
for a cooldown; a single probe request re-opens it. Keep the state in-process and per-worker
— a shared breaker is a distributed-systems problem this phase does not need, and a stale
shared "down" flag is worse than a local one that re-probes.

**2. The fallback must be visible.**

Emit an event carrying the role, the deployment that failed, the one taken instead, and the
reason; log at ERROR. A silent downgrade is the same defect class as a governance hook that
skips the database when no context is bound, and as a reranker that quietly stops reranking:
the system keeps answering and nobody learns the answer got worse.

**3. Fallback must not escape the tenant's model allowlist.**

The `/v1` model resolution and `routing_table()` decide what a tenant's tier may reach. A
tenant entitled to `CHEAP` being silently served `GENERATION` because the cheap deployment
was down is **a spend decision taken on their behalf**, and it lands in `usage_ledger` as
their money. Bound the chain by the tier. When every model in tier is unavailable, the run
**fails loudly** rather than succeeding expensively.

**Tests required:**

- A deployment that fails N times in a row is skipped on the next call, and a probe re-opens
  it. Assert on which deployment was *called*, not on a log line.
- A fallback emits its event and logs at ERROR — a fallback nobody can see is the defect.
- A tier-bounded chain refuses to promote: a `CHEAP`-only tenant whose cheap deployment is
  down gets a loud failure, **not** a `GENERATION` call. Assert the expensive deployment was
  never called.
- One sub-agent's model failure does not cancel its siblings (this is `return_exceptions`
  from 5.3, asserted here against a real failing deployment rather than a synthetic raise).

---

### 5.9 — A real harness for every agent, and prompts that improve through the gate (0.5d)

Both surfaces exist. **Neither is to be rebuilt**, and a second copy of either is the
failure this task exists to prevent.

**What exists:** `aegis/src/aegis/agent/harness.py` — `harness_config()` reflects every
`AgentConfig` knob as a typed, bounded descriptor, and `run_summary()` folds the ordered
event stream into one record. Because `run_summary` consumes the emitted events verbatim,
the "how it worked" record **cannot** diverge from what streamed to the client. And
`aegis/src/aegis/ops/models.py` — `PromptVersion` / `PromptStatus`
(`draft → staged → active → archived`), with the registry cache and the eval gate. That
**is** the LLM-Ops loop; it is built, and sub-agents do not use it.

**(a) Per-agent trace.** `run_summary` gains an agent dimension so the harness renders one
record per sub-agent instead of one blurred record for the run. This is nearly free once
§5.4 lands — the events are already stamped with `agent_id`; the fold just has to group on
it. A run with four agents produces four readable records plus the synthesis, and that is
what makes "per-agent logs" a fact rather than a UI grouping guess.

**(b) Every sub-agent prompt is a `prompt_key`.** Register each `SubAgentSpec`'s system
prompt in the registry. Then improving an agent's prompt is **promoting a version through
the existing eval gate**, not editing a string in a file and hoping. The adapter's prompt
remains the floor when no `ACTIVE` version exists, exactly as the main prompt already
behaves — so a registry outage degrades to the shipped prompt rather than to none.

**(c) The user's memory and skills reach sub-agent context.** The main graph already
resolves `memory_subject` and the adapter already owns selection
(`memory_spec.render_profile`, `select_skills`). A sub-agent that cannot see the user's
durable facts is a *worse* agent than the single one it replaced, which would be an odd
thing to ship as an upgrade. Use the adapter's selection — **one selector in the codebase**,
for the same reason there is one tool-allowlist intersection.

**(d) Every new knob gets a `_KnobSpec`.** Already in the Definition of done, restated here
because it is this task that adds the knobs: a knob with no spec is a control the harness
cannot render and nobody can tune, and it will be discovered on the day somebody needs to
turn it.

**Tests required:**

- A four-agent run produces four per-agent harness records, each carrying that agent's own
  model, tokens, cost and tool calls — and the totals reconcile with the run's summed delta.
- A sub-agent with an `ACTIVE` `PromptVersion` uses it; with none, it uses the adapter floor.
  Both asserted on the prompt actually sent.
- A sub-agent's context contains the user's rendered profile, and the selection came from the
  adapter — assert the adapter selector was called, so a second copy cannot creep in.
- `harness_config()` covers every new `AgentConfig` knob — the existing bijection test
  extended, so a knob added without a spec fails at test time rather than in a demo.

---

## Budget, stated plainly

Team mode is roughly 4–6× a single-pass run. Against $100 of gateway credit and 50–100 demo
runs plus development, that arithmetic only works because of three things, and all three are
in this plan:

1. **The classifier defaults to SINGLE.** Most queries never fan out.
2. **`ModelRole.CHEAP` for three of the four agents.** Only Policy reasons.
3. **The caches doing real work** — the answer cache from Phase 1, the Tavily cache from
   task 5.6. A rehearsed demo query on a warm cache costs nothing.

Show per-agent cost live. Tokens are already visible to the jury, so the fan-out cost is
going to be seen either way — better that the *choice* is visible beside it. "Parallel agents
buy latency and coverage, and here is exactly what they cost" is a much stronger sentence
than a number nobody explained.

---

## Definition of done

- [ ] "What is my remaining budget?" runs single-pass, one plan call, no fan-out — and the
      `routing` event says why.
- [ ] A genuinely multi-part question fans out, and the event stream shows **interleaved**
      events from concurrent agents, each carrying its `agent_id`.
- [ ] Every existing event still validates with `agent_id` absent. The `qa` golden trace is
      byte-identical.
- [ ] A sub-agent proposes a HIGH-risk action → the run gates, parks, and resumes through the
      **existing** approval path. One gate, always.
- [ ] `interrupt()` is not reachable from inside a gathered task. Assert it.
- [ ] One agent killed mid-run → siblings finish, the `synthesis` event names it as omitted,
      and the answer says "3 of 4".
- [ ] `BudgetExceededError` inside a gathered task still terminates the run as `blocked`.
- [ ] Tavily content passes a `TOOL_RESULT` rail before reaching any agent context, and a
      planted injection in a search result is blocked and visible.
- [ ] Tavily key absent → Research agent degrades loudly to internal-only, run completes.
- [ ] `web/src/config/graphTopology.json` regenerated; the topology snapshot test passes.
- [ ] Every new `AgentConfig` field has a `_KnobSpec`.
- [ ] An explicit width from the user is honoured exactly; the classifier does not override
      it. Only the tenant's own budget can refuse a run.
- [ ] Four concurrent agents retrieve the tenant's chunks **once**, not four times — asserted
      on the retrieval call count, which is the supply-side optimisation Amendment A commits
      to instead of restricting the user.
- [ ] A deployment that fails repeatedly is skipped for a cooldown rather than costing every
      concurrent agent its own timeout, and a probe re-opens it.
- [ ] A fallback emits an event and logs at ERROR. There is no silent downgrade.
- [ ] A `CHEAP`-tier tenant whose deployment is down **fails loudly** and is never served a
      `GENERATION` model — asserted by the expensive deployment never being called.
- [ ] A four-agent run produces four per-agent harness records whose totals reconcile with
      the run's summed delta.
- [ ] A sub-agent uses its `ACTIVE` `PromptVersion` when one exists and the adapter floor
      when none does — asserted on the prompt actually sent.
- [ ] A sub-agent's context carries the user's rendered profile, selected by the **adapter's**
      selector rather than a second copy.

## Demo at the end of this phase

Two queries, back to back.

*"What's my remaining budget this month?"* — one lane, one answer, two seconds. The routing
line says: single-intent, answering in one pass.

*"Compare our escalation policy against what changed in the regulation this quarter, and
tell me which open requests are affected."* — the classifier says three sub-questions,
three agents launch, and three logs stream side by side with their own tool calls. The
research agent's Tavily result trips the `TOOL_RESULT` rail on screen. One agent times out
and the synthesis says so. The proposed status change stops at the human gate.

Then say the quiet part: *"the first query cost four cents because it did not need agents."*

## Risks

**Concurrent live model calls fail on stage.** Four agents × a multi-step tool loop is 10–20
gateway calls in ~20 s, against a shared hackathon gateway, plus Tavily, on conference wifi.
Mitigations: semaphore at 3, staggered launches, `_MODEL_RETRY` extended to sub-agents,
Memurai caching, and a visible `degraded` badge on adaptive back-off. **The real insurance is
replay mode, and it is not in this phase** — it depends on the run-event log in Phase 5.
Flag that dependency now, because on the 28th it is the thing you will wish you had.

**Three days is a compression of seven to nine.** Plan 02's Phase 1 is 7–9 days. This phase
is that phase with the critic agent, per-agent skills, the run-event log and the console
rendering removed. Honest accounting: if day two ends without interleaved events on the wire,
cut to two agents immediately.

**The classifier over-fires.** Every false TEAM is 4–6× cost on a query that did not need it.
Log the classification with the query on every run and read the log after the first day of
rehearsal. Tune the deterministic pass, not the prompt.

**The classifier under-fires on the demo query.** Worse than over-firing, because it happens
on stage. Pin the rehearsed complex query in a test that asserts it classifies TEAM.

**Timeout tuning is a demo variable.** 45 s per agent and 90 s wall clock are guesses until
you have run it on the demo machine over the demo network. Measure, then set. A 90-second
silence on stage is very long.

**`agent_id` leaks into the wrong events.** The writer is scoped per sub-agent through
contextvars — the same mechanism the probe verified — so a sequential node emitting after
fan-in must not inherit a stale identity. Test that the `synthesize` node's events have
`agent_id: None`.
