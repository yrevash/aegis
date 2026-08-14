# The agent — the implementation in Aegis

Every claim here is checkable against source. Paths are relative to the repo root.

The module lives at **`aegis/src/aegis/agent/`** — a pure graph over injected deps. It depends
on `langgraph`, the injected capability callables, `aegis.observability`, and an injected event
validator. **Never** on a host's API schema, config, adapter or data layer
(`aegis/src/aegis/agent/__init__.py:1-11`).

**`backend/src/app/agent/`** is the composition root: it binds those seams to the platform's
durable machinery.

---

## How you import it

```python
from aegis.agent import (
    run_agent,            # the async generator of stamped events; owns pause/resume
    build_agent,          # compile the graph with injected deps + checkpointer
    resume_parked_run,    # headless resume of a checkpointed run
    AgentDeps, AgentConfig, MemoryDeps, ToolOutcome,
    AgentState,
    ApprovalRegistry, get_approval_registry, ApprovalOutcome,
    ParkedRunRegistry, get_parked_runs, ParkedRun,
    GateHandedOffError, ResumeFailedError, UnknownApprovalError,
    RouterDecision, route_query, classify_deterministic, load_roster,
    graph_topology, GraphTopology, TopologyNode, TopologyEdge,
    harness_config, run_summary,
    risk_at_least, risk_rank,
    events,
)
```

Full list: `aegis/src/aegis/agent/__init__.py:62-93`.

---

## The state

**`aegis/src/aegis/agent/state.py`** — `AgentState` is a `TypedDict, total=False`
(`state.py:47`), so nodes return only the keys they change.

### The reducers

Three keys carry `Annotated[..., operator.add]`:

```python
plan_iterations:    Annotated[int, operator.add]     # state.py:128
prompt_tokens:      Annotated[int, operator.add]     # state.py:146
completion_tokens:  Annotated[int, operator.add]     # state.py:147
cost_usd:           Annotated[float, operator.add]   # state.py:148
```

The module docstring (`state.py:12-20`) states why this is not decoration:

> *"Every key without a reducer is only safe while no LangGraph superstep runs two nodes at
> once: if two parallel nodes each returned a last-write-wins key, one update is silently
> dropped, and for the totals below LangGraph raises `InvalidUpdateError` ('can receive only
> one value per step'). The accumulators are reduced so nodes may return a **delta** and
> remain correct under a fan-out."*

The delta contract is enforced by `_accrue` (`graph.py:1303-1317`):

```python
return {
    "prompt_tokens": int(usage.prompt_tokens),      # THIS call only
    "completion_tokens": int(usage.completion_tokens),
    "cost_usd": float(usage.cost_usd),
}
```

Its docstring records what it replaced: *"a read-modify-write over `state`: reading the
running total and returning `total + delta` is correct only while no two nodes ever run in
the same superstep, and silently loses one branch's spend the moment anything runs in
parallel."*

### The three keys that deliberately stay last-write-wins

Documented at `state.py:21-37`, each with the reason it would be **wrong** as a reducer:

| Key | Why not accumulated |
|---|---|
| `messages` | A per-planning-round scratch buffer, rebuilt from scratch each time `plan` runs. Accumulating would duplicate the whole prompt on every self-repair round |
| `conversation` | A snapshot of what the memory store already holds, written once by `recall_memory`. Accumulating would duplicate every prior turn on each write and defeat the memory layer's turn cap |
| `tool_results` | Replaced wholesale by `act`. The previous round is read *before* that overwrite to build the self-repair prompt, so the data is used, not lost. Accumulating would make `reflect` re-see an already-repaired failure and burn the budget |

And the rule for the future (`state.py:39-40`): *"Adding a parallel branch that writes either
of those keys requires giving it a reducer first."*

### The field that explains the retrieval bug

`messages` (`state.py:56-60`): *"It is NOT the conversation history (`plan` runs after
`retrieve`, so this key is empty for everything upstream of it) — see `conversation`."*

---

## The graph

**`aegis/src/aegis/agent/graph.py`**, built by `build_agent(deps, *, checkpointer=None)`
(`graph.py:340`). Every node body is a closure over `deps`, so injecting fakes drives the whole
graph offline (`graph.py:344-346`).

### The nodes

| Node | Line | Span kind | Retry | Emits events? |
|---|---|---|---|---|
| `guard_input` | `:373` | GUARDRAIL | no | yes |
| `route` | `:391` | CHAIN | yes | yes |
| `answer_memory` | `:434` | CHAIN | yes | yes |
| `recall_memory` | `:640` | — | no | only when active |
| `persist_memory` | `:685` | — | no | **never** |
| `retrieve` | `:510` | RETRIEVER | yes | yes |
| `ml_predict` | `:713` | CHAIN | no | no |
| `plan` | `:735` | CHAIN | yes | yes |
| `gate` | `:829` | CHAIN | no | yes |
| `approval` | `:864` | — | **no** | **no** |
| `act` | `:885` | CHAIN | **no** | yes |
| `reflect` | `:921` | CHAIN | no | yes |
| `generate` | `:974` | CHAIN | yes | no |
| `guard_output` | `:1013` | GUARDRAIL | yes | yes |
| `stream` | `:1073` | CHAIN | no | yes |

`NODE_LABELS` (`graph.py:86-102`) is the **one place** a node's display name is written. It
feeds `_timed` (so `node_started`/`node_finished` carry it) and `graph_topology` reads it to
label the served topology — which is why anything that draws the graph cannot drift from what
runs (`graph.py:78-85`).

### The edges

```python
START → guard_input                                                    # graph.py:1171
guard_input → END if blocked else route                                # graph.py:1174-1178
route → {recall_memory | answer_memory}   (via SPECIALIST_NODES)       # graph.py:1185-1189
answer_memory → guard_output                                           # graph.py:1192
recall_memory → retrieve → ml_predict → plan                           # graph.py:1193-1196
plan → gate if tool_calls else generate                                # graph.py:1197-1201
gate → approval if gated else act                                      # graph.py:1202-1206
approval → act if approved else generate                               # graph.py:1207-1211
act → reflect                                                          # graph.py:1215
reflect → plan if reflect_retry else generate                          # graph.py:1216-1220
generate → guard_output → stream → persist_memory → END                # graph.py:1221-1226
```

**A blocked input short-circuits straight to END** (`graph.py:1174-1178`), so the router never
runs and nothing downstream executes.

### The routers

Three conditional-edge functions, all **pure functions of state** and therefore unit-testable
with a dict:

- `_route_specialist` (`graph.py:137`) — maps `agent_role` through `SPECIALIST_NODES`
  (`graph.py:128-131`), falling back to `qa` **loudly** with a warning naming the unroutable
  role (`graph.py:146-153`).
- `_route_gate` (`graph.py:187`) — `"approval"` if `state["gated"]` else `"act"`. The
  docstring states: *"ML never routes here."*
- `_route_reflect` (`graph.py:199`) — `"plan"` if `state["reflect_retry"]` else `"generate"`.

`SPECIALIST_NODES` is the deliberate seam (`graph.py:117-127`): *"adding a specialist to an
adapter roster is not enough to make it routable — it needs a handler node and an entry here."*
Before it existed, the edge was a hardcoded `"memory" → answer_memory, else recall_memory`
binary and a third specialist was silently swallowed.

### `_timed` — the instrumentation wrapper

**`graph.py:270`.** Around every non-plain node it:

1. Emits `node_started(node, label)` via the LangGraph **custom stream writer**.
2. Opens **one OpenTelemetry span** of the given kind, so retrieval/guardrail/tool/LLM spans
   opened inside the body nest beneath it and the trace reads as a tree (`graph.py:281-287`).
3. Runs the body under `_call_with_retry`.
4. Pops a `_telemetry` dict off the update (never written to state) and emits
   `node_finished` carrying model, tokens and cost (`graph.py:318-332`).

### `_call_with_retry` — and where the retry sits

**`graph.py:228`.** The retry lives **inside** the timing/emit wrapper, and the docstring
(`graph.py:234-240`) says exactly why:

> *"Wiring the same policy as LangGraph's node-level `retry_policy=` re-invokes the whole
> wrapper, which emits `node_started` **before** the body — so a transient failure produced a
> second `node_started` for one logical node execution, and `run_summary` folded that into an
> extra, permanently unpaired node record with `duration_ms: None`. Retrying only the body
> keeps the start/finish pair exactly one-to-one with the node execution, and the measured
> duration spans every attempt (which is the honest wall clock)."*

`GraphBubbleUp` — LangGraph's control-flow family, which includes interrupts — is **re-raised
unconditionally** (`graph.py:248-250`): *"Interrupts/commands are control flow, never
failures."*

`_should_retry` (`graph.py:213`) honours all three shapes LangGraph's `RetryPolicy` accepts for
`retry_on`: a callable predicate (the default `default_retry_on`, which admits only transient
classes), a single exception type, or a sequence.

### Which nodes get retries, and which must not

`_MODEL_RETRY = RetryPolicy(max_attempts=3)` (`graph.py:1114`), applied to `route`,
`answer_memory`, `retrieve`, `plan`, `generate`, `guard_output`.

The exclusions are documented at `graph.py:1108-1113`:

> - **`act`** — executes real, externally-visible tool actions. *"Exactly-once is guaranteed by
>   the approvals DB lock, not by the graph; retrying here could issue a refund twice."*
> - **`approval`** — re-executes on resume by design; a retry would re-interrupt.
> - **memory nodes** — already best-effort with their own degrade-to-nothing path.

---

## The gate

### `gate` — the decision

**`graph.py:829`:**

```python
risk_of = {c["id"]: deps.tool_risk(c["name"]) for c in calls}
top_risk = max(risk_of.values(), default=RiskLevel.LOW, key=risk_rank)
gated = any(risk_at_least(r, config.gate_min_risk) for r in risk_of.values())
```

`risk_rank` / `risk_at_least` come from `aegis/src/aegis/agent/deps.py:66-73` over
`_RISK_RANK` (`deps.py:59-63`): LOW=0, MEDIUM=1, HIGH=2.

The ML explanation is emitted here as an **informational** event carrying no gating semantics
(`graph.py:847-851`). The docstring is explicit: *"ML never gates."*

**Unknown tools fail safe.** `_default_tool_risk`
(`backend/src/app/agent/deps.py:416-426`) returns `RiskLevel.HIGH` for an unregistered name:
*"a hallucinated tool the planner invented is treated as HIGH risk so it can never slip under
the autonomy ceiling and skip the human gate."*

### `approval` — the interrupt

**`graph.py:864`:**

```python
decision = interrupt({
    "action": call.get("name", "unknown"),
    "args": call.get("args", {}),
    "risk": state.get("gate_risk", RiskLevel.LOW.value),
    "rationale": state.get("gate_reason", "Approval required."),
})
return {"approved": bool(decision.get("approved")), "approver": decision.get("approver")}
```

**No events are emitted before `interrupt`** (`graph.py:867-869`) *"because the node
re-executes on resume; the orchestrator emits `approval_required` from the interrupt value
exactly once."* This is the direct consequence of LangGraph re-running an interrupted node
from its beginning.

The node is also wired **plain**, not through `_timed` (`graph.py:1158`, and the comment at
`graph.py:1117-1119`), for the same reason.

`_gated_call` (`graph.py:859`) picks the highest-risk call as the representative for the gate.

---

## The orchestrator

**`aegis/src/aegis/agent/orchestrator.py`** — `run_agent` (`orchestrator.py:96`) is the one
coroutine a host's API layer consumes.

### The five injected seams

Documented at `orchestrator.py:18-29`:

| Seam | Default | Host binds |
|---|---|---|
| `checkpointer` | `InMemorySaver()` | The shared/durable saver |
| `stamp` | `_dict_stamp` (`:66`) | The locked wire-schema validator |
| `enqueue_approval` | `_noop_enqueue_approval` (`:76`) | The durable approvals-inbox writer |
| `on_terminal` | `_noop_on_terminal` (`:81`) | The post-run trace-eval hook |
| `default_tier` | `None` | The approver tier |

Plus `parked_runs`, injectable so a host that owns its registry can wipe it to simulate a fresh
worker (`orchestrator.py:136-138`).

`deps` is **required** — `run_agent` raises if omitted (`orchestrator.py:143-147`): *"there is
no default wiring inside `aegis.agent`."*

### The stream loop

```python
# orchestrator.py:204-223
while True:
    interrupt_value = None
    async for mode, chunk in graph.astream(stream_input, config,
                                           stream_mode=["custom", "updates"]):
        if mode == "custom":
            ... collect guardrail events and node latencies ...
            yield emit(chunk)
        elif mode == "updates" and _is_interrupt(chunk):
            interrupt_value = chunk["__interrupt__"][0].value
    if interrupt_value is None:
        break
```

`_is_interrupt` (`orchestrator.py:354`) tests for the `__interrupt__` key. `emit`
(`orchestrator.py:169-173`) stamps every payload with `run_id` and a monotonic `seq`.

Two side-channels collected without touching the emitted stream: `guardrail_events`
(`orchestrator.py:213`) for the post-run trace-eval, and `node_latencies`
(`orchestrator.py:215-220`) folded into the in-process latency window at
`orchestrator.py:327`.

### The gate rendezvous

**`orchestrator.py:228-298`.** The ordering is load-bearing:

```python
approval_id = uuid4().hex
registry.register(approval_id)          # BEFORE emitting — a fast decision cannot race past
...
try:
    sla_deadline = await enqueue_approval(approval_id, ...)   # the durable row
    parked_runs.register(run_id, graph, config)               # the resumable handle
    yield emit(events.node_started("approval", "Human approval gate"))
    yield emit(events.approval_queued(...))
    yield emit(events.approval_required(...))
    try:
        outcome = await registry.wait(approval_id, timeout=deps.config.approval_park_timeout)
    except TimeoutError:
        yield emit(events.run_finished(RunStatus.AWAITING_APPROVAL))
        return
finally:
    registry.discard(approval_id)
stream_input = Command(resume={"approved": outcome.approved, "approver": outcome.approver})
```

The comment at `orchestrator.py:237-243` explains the `finally`: *"between it and the `wait`
below sit an await plus three yields, and a disconnected SSE client closes the generator at any
of them. The `finally` guarantees the future is discarded in that case, so a decision arriving
afterwards cannot be mistaken for a live wake-up."*

The `TimeoutError` handler also catches `GateHandedOffError` (a subclass), and the comment
(`orchestrator.py:287-291`) says why parking is exactly right there: *"a decision that we
failed to take in time now belongs to the resumer... the action still runs exactly once, over
there."*

### Terminal handling

Completion (`orchestrator.py:303-327`): pop the parked handle, read final state via
`get_state`, emit `run_finished` with usage, fire `on_terminal`, record latencies.

`BudgetExceededError` (`orchestrator.py:328-343`): a per-tenant cap tripped at the gateway
before spend — a clean `budget_exceeded` event and `run_finished(BLOCKED)`, not a crash.

Any other exception (`orchestrator.py:344-351`): pop the handle (*"an errored run is terminal,
so a retained handle pins a compiled graph plus its checkpointer with nothing left to
resume"*), emit `error`, emit `run_finished(ERROR)`.

### `resume_parked_run`

**`orchestrator.py:367`.** A pure helper over an already-resolved gate: the caller has won the
`PENDING → RESUMING` transition and supplies the compiled graph plus a config carrying
`thread_id == run_id`.

The critical distinction (`orchestrator.py:396-417`):

```python
try:
    resumable = bool(graph.get_state(config).next)
except Exception as exc:
    raise ResumeFailedError(...)      # a broken store is a FAILURE, not an absence
if not resumable:
    return False                       # nothing to resume
...
try:
    async for _ in graph.astream(resume_cmd, config, stream_mode=["custom", "updates"]):
        pass                           # drive headless; the tool runs exactly once
except Exception as exc:
    raise ResumeFailedError(...)
return True
```

`ResumeFailedError` (`orchestrator.py:86-93`) exists **specifically** so the caller can tell
"nothing to resume" from "resume broke": *"the caller holds a durable row already flipped to
`RESUMING` and MUST release it, otherwise the run is stranded — `RESUMING` matches neither the
decision path nor the SLA sweeper."*

---

## The approval registries

**`aegis/src/aegis/agent/approvals.py`.**

### `ApprovalRegistry` — the notify cache

`_Gate` (`approvals.py:95-104`) holds the `future`, a `consumed` event, `registered_at`,
a `waiting` count, and an `abandoned` flag.

| Method | Line | Contract |
|---|---|---|
| `register` | `:128` | Create the future, sweep first |
| `wait` | `:143` | Await, then **mark consumed** — or raise `GateHandedOffError` if disowned |
| `resolve` | `:196` | **Fire-and-forget.** Docstring: *"the durable decision path must use `notify_live` instead"* |
| `notify_live` | `:221` | The **acknowledged** hand-off |
| `discard` | `:273` | Forget a gate nothing will consume; marks it abandoned |
| `is_pending` / `pending_ids` | `:291`, `:297` | Introspection |
| `sweep` | `:307` | TTL eviction of gates with no active waiter |

**`notify_live`** (`approvals.py:221-271`) is the protocol:

```python
gate.future.set_result(ApprovalOutcome(decision=decision, approver=approver))
try:
    await asyncio.wait_for(gate.consumed.wait(), ack_timeout)   # default 1.0s
except TimeoutError:
    if gate.consumed.is_set():        # consumed in the same loop turn the timeout fired
        return True
    gate.abandoned = True
    self._forget(approval_id, gate)
    return False
return True
```

**`wait`'s critical section** (`approvals.py:183-192`):

```python
# No ``await`` between the wake-up and the acknowledgement below: the check and the
# hand-off are one critical section on the single-threaded event loop, so a racing
# notify_live timeout either sees ``consumed`` or wins ``abandoned`` — never both.
if gate.abandoned:
    raise GateHandedOffError(...)
gate.consumed.set()
return outcome
```

`GateHandedOffError` (`approvals.py:84-92`) subclasses `TimeoutError` *specifically* so the
orchestrator's existing park path handles it: the run parks, the resumer executes, **exactly
one of the two sides ever proceeds**.

`wait` with a `timeout` uses `asyncio.wait_for(asyncio.shield(gate.future), timeout)`
(`approvals.py:178`) — the shield keeps the underlying future alive so a timeout parks the run
rather than cancelling the decision.

### `ParkedRunRegistry` — the resumable handles

`ParkedRun` (`approvals.py:344`) holds `graph`, `config`, `parked_at`.
`ParkedRunRegistry` (`approvals.py:359`) has `register` / `pop` / `get` / `sweep` / `ids`, all
TTL-bounded at 3600s (`DEFAULT_PARKED_TTL_SECONDS`, `approvals.py:54`).

The eviction argument is in the class docstring (`approvals.py:361-367`):

> *"Several paths never pop... Since each entry pins a compiled graph plus its checkpointer
> (the entire run state), an unbounded map leaks whole runs. Eviction is safe: the durable
> checkpoint remains, so a decision that arrives after eviction rehydrates by `thread_id`
> exactly as a fresh worker does."*

**Neither registry defines `__len__`** — deliberately (`approvals.py:299-303`,
`:427-431`): *"a registry is passed around as `registry or get_default()`, and a `__len__`
would make an empty one falsy — silently swapping an injected registry for the process-wide
singleton."* They expose `pending_ids()` / `ids()` instead.

---

## The DI contract

**`aegis/src/aegis/agent/deps.py`** holds only the contract — no heavy imports, nothing
host-specific.

`AgentDeps` (`deps.py:223`), eleven required callables plus eight optional fields:

| Field | Type | Default |
|---|---|---|
| `complete` | `CompleteFn` | required |
| `retrieve` | `RetrieveFn` | required |
| `check_input` / `check_output` | `GuardFn` | required |
| `predict_explain` | `PredictFn` | required |
| `tool_definitions_for` | `ToolDefsFn` | required |
| `run_tool` | `RunToolFn` | required |
| `tool_risk` | `RiskFn` | required |
| `render_system_prompt` | `RenderPromptFn` | required |
| `features_for` / `describe_prediction` | | required |
| `agent_roster` | `RosterFn` | core `qa`-only fallback |
| `config` | `AgentConfig` | `AgentConfig()` |
| `memory` | `MemoryDeps \| None` | `None` ⇒ memory nodes are silent no-ops |
| `answer_cache` | `AnswerCache \| None` | `None` |
| `current_tenant_id` | `TenantFn` | `_no_tenant` |
| `record_audit` | `AuditFn \| None` | `None` |
| `embed_query` | `EmbedQueryFn \| None` | `None` ⇒ memory recall degrades to recency |

`MemoryDeps` (`deps.py:166`) and `AnswerCache` (`deps.py:203`) are **structural Protocols** —
the concrete implementations live host-side.

`AgentConfig` (`deps.py:91-163`):

| Knob | Default | Meaning |
|---|---|---|
| `gate_min_risk` | `HIGH` | **The only gating signal** |
| `run_ml` | `True` | Best-effort ML evidence |
| `stream_chunk_words` | 4 | Words per `token` event |
| `max_plan_iterations` | 2 | **Hard cap** guaranteeing termination |
| `self_repair_enabled` | `True` | Master switch for the loop |
| `approval_park_timeout` | `None` | `None` waits forever — the live demo gate |
| `default_persona_id` | `"default"` | |
| `query_rewrite_enabled` | `True` | |
| `agentic_retrieval_enabled` | `True` | |
| `agentic_retrieval_max_rounds` | 2 | |
| `answer_cache_enabled` | `True` | |

`as_dict()` (`deps.py:143`) surfaces every knob as JSON so the harness UI can render and
round-trip it without importing the dataclass. The docstring calls it *"the complete,
authoritative list of tweakable knobs"*.

---

## The self-repair loop

`plan` (`graph.py:735`) increments the counter:

```python
"plan_iterations": 1,   # reducer-summed (operator.add)   # graph.py:821
```

and on a retry feeds back the previous round's outcomes (`graph.py:783-795`):

```python
prior = state.get("tool_results")
if prior and state.get("reflect_retry"):
    attempts = "\n".join(f"- {r['summary']} ({'ok' if r['ok'] else 'FAILED'})" for r in prior)
    user_content += "\n\nA previous action attempt did not fully achieve the goal:\n..."
```

`reflect` (`graph.py:921`) decides:

```python
done = bool(results) and all(r["ok"] for r in results)
budget_left = iteration < budget
will_retry = config.self_repair_enabled and (not done) and budget_left
```

**The termination argument** (`graph.py:928-932`): *"The counter is incremented in `plan`, so
this node can only ever *reduce* the remaining budget: the loop is guaranteed to terminate."*

The judgement is **domain-agnostic** — it reads `.ok` off each `ToolOutcome` (a structural
Protocol at `deps.py:76-80`), never hardcoded domain logic. And a `reflection` event
(`graph.py:963-971`) streams the iteration, budget, `done`, `will_retry` and a human reason,
with four distinct reason strings for the four ways the decision can go
(`graph.py:945-961`).

---

## The supervisor router

**`aegis/src/aegis/agent/router.py`.** Deterministic-first
(`router.py:14-20`): the common cases are decided by phrase matching with **no model call at
all**, and only a genuine tie escalates to `ModelRole.CHEAP`.

`classify_deterministic(query, roster)` (`router.py:126`) scores each specialist as
`(distinct hits, total matched characters)` (`router.py:150-153`) — hit count primary,
specificity as tiebreak — and returns `(None, reason)` only on a dead heat.

`_phrase_present` (`router.py:91`) is **word-boundary aware**:

```python
pattern = r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])"
```

The docstring records the bug (`router.py:93-97`): *"A bare substring test made 'memory' match
'memorandum' and 'bill' match 'billboard'."* Boundaries are alphanumeric-aware rather than
`\b`-only so a multi-word or punctuated hint ("out of office", "p&l") still matches.

`_match_score` (`router.py:103`) de-duplicates hints (a roster listing the same phrase twice
must not out-score one listing it once) and orders matches longest-first so the most specific
phrase leads the hand-off reason.

`_llm_tiebreak` (`router.py:214`) is **deliberately strict** (`router.py:220-226`):

> *"Scanning the roster in order for a role id anywhere in the reply made `'not qa — use
> memory'` return `qa`: the *rejected* role won because it was declared first. So an exact
> reply wins; otherwise the reply must mention exactly one role on word boundaries, and a
> reply naming several is a non-answer."*

`load_roster()` (`router.py:57`) returns the core `qa`-only `_FallbackRoster` (`router.py:69`).
The host wires the real adapter roster through `deps.agent_roster`.

---

## Topology

**`aegis/src/aegis/agent/topology.py`.** `graph_topology(agent=None)` (`topology.py:97`)
returns `{"nodes": [...], "edges": [...]}` as plain JSON.

It compiles a **shape-only** graph from `_inert_deps()` (`topology.py:75`) whose every callable
is `_unreachable` (`topology.py:65`), because *"the topology is a property of the wiring, not
of the particular capabilities injected into it."*

Two design details worth copying:

- `START`/`END` are folded into `entry`/`terminal` flags rather than appearing as nodes
  (`topology.py:120-131`).
- **It raises `KeyError` if a node has no `NODE_LABELS` entry** (`topology.py:114-117`) — *"a
  deliberate tripwire, so a new node cannot ship without its label."*

---

## The harness

**`aegis/src/aegis/agent/harness.py`.** `harness_config()` (`:56`) exposes every knob with its
type, default and allowed values as data. `run_summary(events)` (`:196`) folds the **same
emitted events** into a structured per-run record: nodes with durations, reasoning, guardrails,
routing, gate, tools, iterations, ML, memory, answer, totals.

**The gate section** (`harness.py:332-364`) has two carefully-reasoned rules:

The reported gate is the **last** `approval_required` (`harness.py:335-338`), because a
multi-round self-repair run can gate more than once and `resolved`/`approved` describe where the
run ended up.

And execution is scanned **only after** that gate (`harness.py:342-350`):

> *"Scanning the whole stream mis-reports a REJECTED gate as approved whenever any earlier
> round already executed a tool: round 1 runs a LOW-risk tool (no gate), round 2 proposes a
> HIGH-risk one, the human rejects, and the pre-gate `tool_result` would otherwise stand in as
> evidence of execution. A reject routes straight to `generate`, so nothing executes after the
> gate it decided."*

`"approved": None if parked else executed_after_gate` (`harness.py:361`) — three states, not
two: parked is `None`, not `False`.

---

## The backend composition root

### `run_agent`

**`backend/src/app/agent/orchestrator.py:206`** — a thin wrapper binding the five seams:

```python
async with aclosing(_core_run_agent(
    query, persona=persona, role=role, deps=deps, registry=registry,
    run_id=run_id, session_id=session_id, memory_subject=memory_subject,
    checkpointer=get_agent_checkpointer(),
    stamp=events.stamp,
    enqueue_approval=_enqueue_gate,
    on_terminal=_fire_trace_eval,
    default_tier=_default_tier(),
    parked_runs=get_parked_runs(),
)) as stream:
    async for event in stream:
        yield event
```

**`aclosing` is load-bearing, not tidiness** (`orchestrator.py:240-245`): *"when the SSE client
disconnects, this wrapper is closed at its `yield` and a bare `async for` would leave the inner
generator to garbage collection — deferring the gate cleanup that discards the notify future.
Closing it here propagates the disconnect immediately, so a decision arriving next can never
mistake a dead run's registration for a live waiter."*

### `decide_approval` — the one shared resolve path

**`backend/src/app/agent/orchestrator.py:337`.** Three steps, documented at
`orchestrator.py:346-361`:

```python
resolution = await _safe_resolve(approval_id, decision, approver)   # the durable CAS
live_woken = await registry.notify_live(approval_id, decision, approver=approver)
won = bool(resolution and resolution.won)
accepted = won or live_woken
```

Then four branches (`orchestrator.py:386-411`):

| Condition | Action |
|---|---|
| won + APPROVE + **not** live | `resume_parked_run` — continue from the checkpoint |
| won + APPROVE + live | `_safe_finalize` → `APPROVED`, pop the handle |
| REJECT, or a live socket took it | Pop the resumable handle |

The comment on the live-approve branch (`orchestrator.py:394-405`) states the residual
honestly: *"the ack proves the run consumed the outcome, not that the tool has finished. A
socket that dies in the microseconds between the two still finalises APPROVED... the failure
mode is at-most-once (never double execution), which is the direction that matters."*

### `resume_parked_run` — two entry conditions, one path

**`backend/src/app/agent/orchestrator.py:418`:**

```python
handle = get_parked_runs().get(run_id)
if handle is not None:
    graph, config = handle.graph, handle.config
else:
    graph = _durable_graph(deps or AgentDeps.default())        # fresh worker
    config = {"configurable": {"thread_id": run_id}}
```

`_durable_graph` (`orchestrator.py:267`) rebuilds the graph on the **shared** checkpointer, so
a run parked on one compiled graph resumes by `thread_id` from any other.

**The handle is only *peeked*, and popped after the drive returns** (`orchestrator.py:459`,
`:482`), so a `ResumeFailedError` can release the row back to `PENDING`
(`orchestrator.py:472-480`) with the handle still parked and the checkpoint still reachable.
The docstring (`orchestrator.py:443-449`): *"Popping first and swallowing the failure left the
row wedged in `RESUMING`, which matches neither the decision path nor the sweeper: neither
approved nor rejected, and unreachable forever."*

`_safe_release` (`orchestrator.py:511`) is the compensating half of the lock — guarded on
`status == RESUMING`, clearing the decision stamps so the row is genuinely pending again.

### The durable lock

**`backend/src/app/data/approvals.py:250`** — `resolve_approval`:

```python
target = ApprovalStatus.RESUMING if decision is APPROVE else ApprovalStatus.REJECTED
result = await session.execute(
    update(Approval)
      .where(Approval.id == approval_id, Approval.status == ApprovalStatus.PENDING)
      .values(status=target, decided_at=_now(), decided_by=approver)
)
won = (result.rowcount or 0) == 1
```

The docstring (`approvals.py:255-262`): *"Because the `WHERE` clause pins `status == PENDING`,
only the first caller wins — a replayed or racing decision finds the row no longer pending and
becomes a no-op, so a run can never be resumed (or executed) twice."*

`finalize_resumed` (`approvals.py:301`) is guarded on `status == RESUMING`. `sweep_expired`
(`approvals.py:325`) applies the SLA policy, each transition guarded on `PENDING`.

`count_approved` (`approvals.py:220`) counts only `APPROVED`, not `RESUMING` — *"an in-flight
`RESUMING` row is not yet counted"*, which keeps the "actions approved" tile honest.

### The checkpointer

`build_agent` (`backend/src/app/agent/graph.py:29`) defaults to
`app.data.session.get_agent_checkpointer()`, so every compiled graph in the process — and,
with the `PostgresSaver`, every worker — checkpoints into **one** store.

`_build_postgres_checkpointer` stays host-side (it needs `app.config` and
`langgraph-checkpoint-postgres`) and is a process-wide singleton whose context manager is
retained so garbage collection cannot close the connection.

### Trace-eval, off the hot path

`_fire_trace_eval` (`backend/src/app/agent/orchestrator.py:173`) is the injected `on_terminal`
hook. It is gated on `stores_enabled`, schedules a **tracked** task (`_TRACE_EVAL_TASKS`,
`orchestrator.py:60`) with a done-callback that surfaces any swallowed exception
(`orchestrator.py:63-70`), and swallows scheduling failures so *"the grade must never disturb
the run's terminal event."*

`_build_eval_steps` (`orchestrator.py:84`) projects the final state plus the collected
guardrail events into trajectory steps mirroring the graph's OTel span kinds: `RETRIEVER`,
`TOOL` (carrying `detail.ok`), `GUARDRAIL`.

---

## The events

**`aegis/src/aegis/agent/events.py`** — plain dict builders, one per wire event:
`node_started` (`:22`), `node_finished` (`:27`), `reasoning` (`:50`), `guardrail` (`:55`),
`retrieval` (`:82`), `tool_call` (`:101`), `tool_result` (`:114`), `ml_explanation` (`:124`),
`approval_required` (`:149`), `provenance` (`:168`), `approval_queued` (`:196`),
`reflection` (`:219`), `routing` (`:243`), `memory` (`:259`), `token` (`:278`),
`run_started` (`:283`), `run_finished` (`:288`), `error` (`:307`), `budget_exceeded` (`:312`).

They return dicts, never host types. The host's `events.stamp` validates them against the
locked `StreamEvent` wire schema, so the graph never imports an API schema.

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — the failure modes and the bugs.
