# Phase 4 — Adaptive multi-agent

**3 days. The money shot, done honestly.**

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
(`graph.py:890`).

The "supervisor" is the `route` node calling `aegis/src/aegis/agent/router.py::route_query`.
It is a **deterministic keyword classifier that picks exactly one role**, with a cheap-LLM
tiebreak only on a genuine tie (`router.py:171-211`). `SPECIALIST_NODES` (`graph.py:128-131`)
maps exactly two:

```python
SPECIALIST_NODES: dict[str, str] = {
    "qa": "recall_memory",
    "memory": "answer_memory",
}
```

Today a query is: one plan call, N serial tool calls, one generate call. One identity, one
log, one lane.

### 2. The accumulator work is already done, which is the good news

`AgentState` (`aegis/src/aegis/agent/state.py:128,146-148`) already carries `operator.add`
reducers on `plan_iterations`, `prompt_tokens`, `completion_tokens`, `cost_usd`, and its
docstring says outright that they exist so state "remains correct under a fan-out" — a
fan-out that does not exist yet. Fan-out costs no state-schema churn.

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

`routing`, `reflection` and `memory` are emitted by the backend and are **not in
`web/src/lib/stream.ts`'s union at all** — `runReducer.ts` ends with `default: return next`,
so they land in `state.events` and are dropped.

Two consequences. First, additive protocol changes are provably safe: an unknown `type`
never breaks the client, so backend can land before frontend. Second, the `routing` event
(`aegis/src/aegis/agent/events.py:243-256`) is exactly where the depth decision belongs, and
rendering it is free demo value already sitting on the floor.

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
| **Now** | A cheap classifier that decides fan-out width — including width zero. |
| **Now** | Genuinely concurrent sub-agents with their own prompts, tool allowlists, budgets and failure containment. |
| **Now** | `agent_id` on every event; per-agent logs and tool calls. |
| **Now** | A synthesis event naming contributing **and omitted** agents. |
| **Now** | Per-agent timeout as a *designed* terminal state. |
| **Now** | Tavily behind a seam, cached in Memurai. |
| **Now** | `GuardStage.TOOL_RESULT`. |
| **Waits** | Persisting the run-event log to Postgres (`run_events`). Plan 02's highest-leverage item and it belongs to Phase 5's console work, not here. |
| **Waits** | Replay mode. Depends on the above. It is the best demo insurance in the whole plan — flag it loudly for Phase 5. |
| **Waits** | Skills attached to sub-agents. The mechanism already exists (filesystem markdown, adapter-selected); wiring it per-agent is not a 3-day item. |
| **Waits** | MCP. Excellent, not required to win a blind problem. |

### The cut, decided now

Per the master plan: **if we slip, the team drops from 4 agents to 2 — research +
synthesise.** Still genuinely concurrent (research runs while knowledge retrieval runs),
still genuinely visible, half the work. Tasks 4.1–4.5 and 4.7 are load-bearing at any width.
Task 4.6's roster entries are what shrink.

---

## Tasks

### 4.1 — The depth classifier (0.5d) — **do this first**

This is the task the user actually asked for, and building it first stops the fan-out from
becoming the default by accident.

Extend `RouterDecision` (`aegis/src/aegis/agent/router.py:41-53`) with the width:

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

Put `depth` and `fanout` on the existing `routing` event (`events.py:243-256`). The
reason string is already demoable and glass-box — "3 sub-questions detected, fanning out to
3 agents" and "single-intent query, answering in one pass" are both good things to have on
screen, and the second one is the one that shows maturity.

New `AgentConfig` knobs: `team_enabled`, `max_parallel_agents` (4),
`max_concurrent_agents` (3), `subagent_max_steps` (4), `subagent_timeout_s` (45),
`team_wall_clock_s` (90).

`aegis/tests/agent/test_harness_config.py:44,55` asserts a bijection between `AgentConfig`
fields and `harness.py::_KNOB_SPECS`. Every knob needs a `_KnobSpec` — and in exchange each
one gets a harness UI control for free.

### 4.2 — One sub-agent, one bounded loop (0.75d)

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
  reach.
- Any tool at or above `config.gate_min_risk` is **not executable here**. It is returned in
  `SubAgentResult.proposed_actions` and flows into the main graph's gate. Today that is
  exactly one tool — `update_request_status`, `HIGH` (`adapter/tools.py:426-433`).
- `max_steps` hard cap. `asyncio.wait_for(timeout_s)`.
- **Never raises.** Every failure becomes `SubAgentResult(status=failed|timeout, error=…)`
  — except `BudgetExceededError` (`aegis.gateway.types`), which is captured and re-raised
  after fan-in so the orchestrator's existing handler
  (`aegis/src/aegis/agent/orchestrator.py:328`) still terminates the run cleanly as blocked.
- Its own token/cost totals, returned for the node's summed delta.

**Write the gate test on day one of this task**, before the fan-out exists: a sub-agent
proposes a HIGH-risk action, the run gates, parks, resumes correctly. That interaction is
the sharp edge of this whole phase and the design constraint in §3 is what removes it — prove
the removal rather than assuming it.

### 4.3 — The fan-out node (0.5d)

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

`plan_team` turns the classifier's width into a task list against the sub-agent roster (one
cheap model call, with a deterministic keyword fallback). `run_team` is the
`asyncio.gather`, under `asyncio.Semaphore(config.max_concurrent_agents)`, launches
staggered ~250 ms to avoid a burst against the gateway.

Three rules:

1. **`return_exceptions=True`, always.** One agent's failure must never cancel its siblings.
2. **The node returns one summed delta.** Because the gather is inside a node, it returns a
   single `{prompt_tokens, completion_tokens, cost_usd}` and the existing `operator.add`
   reducers keep working untouched.
3. Extend `_MODEL_RETRY` (`graph.py:1114`, `RetryPolicy(max_attempts=3)`) to sub-agent model
   calls.

The team path lands on the **existing** `gate → approval → act → reflect → generate →
guard_output → stream → persist_memory` tail. The human gate, the output rail, the answer
cache and memory persistence all keep working untouched, and **the `qa` path stays
byte-identical**, which is what keeps the golden-trace tests green.

`aegis/src/aegis/agent/topology.py` compiles the graph over inert deps, and
`backend/tests/api/test_agent_topology.py:100` asserts `web/src/config/graphTopology.json`
equals the served topology. **Regenerate that snapshot** when you add the three nodes, same
as in Phase 2.

### 4.4 — Agent identity on every event (0.25d)

This is what makes the per-agent logs and tool calls real rather than a UI grouping guess.

The orchestrator stamps every event through an **injected `stamp` callable**
(`orchestrator.py:66-73`, called from `emit` at `:169-173`) so the pure package never imports
the host schema. That seam is exactly where identity is enforced.

- `run_subagent` writes through a writer bound to its `agent_id`, so every event a sub-agent
  emits carries it automatically. Do not ask each call site to remember.
- `agent_id: str | None` becomes an optional field on the shared event base. **`None` means
  supervisor / graph-level**, which keeps every existing event valid and unchanged.
- Add it to the `StreamEvent` union in `backend/src/app/api/schemas.py:448` and mirror in
  `web/src/lib/stream.ts:334`.

One optional column on the wire is the entire per-agent-log requirement. It is also what
makes Phase 5's `WHERE agent_id = …` projection trivial when the run-event log lands.

### 4.5 — Synthesis, and timeout as a designed state (0.25d)

`synthesize(results, deps) -> str` — one model call merging the agents' findings. The
synthesiser prompt is told to attribute claims to the agent that produced them.

Emit a `synthesis` event that names **which agents contributed and which were omitted**, and
say it in the answer text too: *"synthesised from 3 of 4 agents; the policy agent timed out
at 45 s."*

This is not politeness. Plan 02's R2 is that partial failure reads as a bug: one agent times
out, its card sits spinning, and the audience concludes the thing is broken. A hard per-agent
timeout with a **designed** terminal state turns that into visible, graceful degradation,
which scores under both Working Prototype and Business Impact — but only if it is designed,
not discovered live.

The critic pass from plan 02 (a fifth, sequential agent reviewing the merged draft) is a
genuine quality control and it is **out of scope at 3 days**. Note it in the backlog. Do not
pad the agent count to hit "4-5"; the user asked for the right balance, not a number.

### 4.6 — Tavily as the real search client (0.5d)

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

### 4.7 — The `TOOL_RESULT` guardrail stage (0.25d)

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
- Emit the `guardrail` event stamped with the `agent_id` from task 4.4, so the console shows
  the rail firing *inside* an agent's log.

This maps directly to OWASP LLM01 and the Agentic Top 10, and it is one of the strongest
things this phase adds to the security story. It is also 0.25 days.

---

## Budget, stated plainly

Team mode is roughly 4–6× a single-pass run. Against $100 of gateway credit and 50–100 demo
runs plus development, that arithmetic only works because of three things, and all three are
in this plan:

1. **The classifier defaults to SINGLE.** Most queries never fan out.
2. **`ModelRole.CHEAP` for three of the four agents.** Only Policy reasons.
3. **The caches doing real work** — the answer cache from Phase 1, the Tavily cache from
   task 4.6. A rehearsed demo query on a warm cache costs nothing.

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
