# The agent — the theory

LangGraph properly, the agent architectures the design draws on, and the distributed-systems
reasoning behind the durable gate. This is the file that turns "we used LangGraph" into
something you can defend.

---

## 1. Agent architectures: the family tree

### ReAct — Reason + Act (Yao et al., 2022)

Interleave reasoning traces with actions in one loop:

```
Thought: I need the customer's plan tier.
Action: lookup_customer(id=4821)
Observation: {tier: "enterprise", ...}
Thought: Enterprise gets a 30-day window, so the refund is in policy.
Action: issue_refund(...)
```

The insight is that reasoning and acting help each other: reasoning decides what to do next,
and observations correct the reasoning. Before ReAct, chain-of-thought reasoned without acting
and tool-use frameworks acted without reasoning.

**Its weakness is control flow.** ReAct is one undifferentiated loop. There is no place to put
"if the action is risky, stop for a human" other than an `if` inside the loop, and no way to
pause the loop durably. It is a prompting pattern, not an execution model.

### Plan-and-Execute

Separate **planning** from **execution**. Produce a plan, then execute its steps.

Advantages over pure ReAct: the plan is inspectable *before* anything runs, which is what
makes a human gate possible at all; the planner can use an expensive model while execution
uses cheap ones; and the structure is visible in the trace.

Aegis is plan-and-execute with a reflection loop, expressed as an explicit graph. The nodes
`plan → gate → act → reflect` are exactly this decomposition, and the gate exists *between*
plan and act precisely because the plan is inspectable there.

### Reflexion (Shinn et al., 2023)

Add **verbal self-reflection**: after acting, the agent evaluates the outcome in language and
feeds that reflection back into the next attempt. It is reinforcement learning where the
"policy update" is text in the prompt rather than a gradient step.

Aegis implements a bounded version: `reflect` judges the executed tool outcomes, and on a
failure the previous attempt's summaries are fed back into the next planning prompt.

**The two constraints that make it safe:**

**Termination must be structural.** The iteration counter is incremented in `plan` and checked
against a hard cap. The reflecting model chooses *whether* to retry, but it cannot extend the
budget — no model output can. Contrast a design where the model decides when to stop: that
eventually loops forever.

**The judgement must be domain-agnostic.** "Did this work?" cannot be hardcoded domain logic
in a platform that gets repointed at a new problem. It reads a structured `ok` flag off each
tool result, which every tool must supply.

### Supervisor / multi-agent routing

Instead of one agent that does everything, a **supervisor** classifies the request and hands
off to a specialist. The specialists have different tools, prompts, and pipelines.

The design question is *what does the classification*. A model call per turn is expensive,
slow, and non-deterministic. Aegis's answer is **deterministic-first**: keyword hints decide
the common cases with no model call at all, and only a genuine tie between two named
specialists escalates to a cheap model.

That has three payoffs: the common path is free, the trace stays clean, and the whole router
is offline-testable.

**Two failure modes that are easy to ship:**

*Substring matching.* Scanning for `"memory"` as a substring makes "memorandum" match and
"bill" match "billboard". Matching must be word-boundary aware.

*The rejected-role bug.* If you break a tie by scanning the model's reply for the first known
role id that appears anywhere in it, then a reply of `"not qa — use memory"` returns **qa**,
because `qa` was declared first. The *rejected* role wins. A reply naming several roles is a
non-answer, not a vote for whichever your roster happens to list first.

---

## 2. LangGraph: the execution model

### It is Pregel

LangGraph implements the **Pregel** model (Malewicz et al., Google, 2010), designed for
large-scale graph processing. Execution proceeds in **supersteps**:

1. Every node whose incoming **channels** have been updated runs.
2. Their outputs are written to channels.
3. Channel updates are merged.
4. Repeat until no node is active.

The vocabulary transfers directly: a **channel** is a state key; a **reducer** is the function
merging concurrent writes to it.

### State: `TypedDict` with `total=False`

The state is a typed record. `total=False` means every key is optional, which is what lets a
node return **only the keys it changed** rather than the whole state. LangGraph merges the
partial update into the checkpointed state.

Two properties follow. Nodes are naturally composable — a node that only touches `context`
never accidentally clobbers `answer`. And a partial update is small, so checkpoints are cheap.

### Reducers, formally

A key's default behaviour is **last write wins**. Annotating it attaches a binary merge
function:

```python
prompt_tokens: Annotated[int, operator.add]
```

Now when a superstep produces two updates for that key, LangGraph applies the reducer instead
of picking one.

**Why it is not decoration.** Every un-reduced key is safe only while no superstep runs two
nodes at once. Add a fan-out and:

- For a key with no reducer, one update is **silently dropped**.
- For an accumulator that LangGraph knows must be merged, it raises `InvalidUpdateError`
  ("can receive only one value per step").

Neither is acceptable for a token counter.

### The delta requirement

This is the part people get wrong, and it is worth stating as a rule:

> **A reduced key must receive a delta, never a recomputed total.**

Consider `prompt_tokens: Annotated[int, add]` and a node that does:

```python
return {"prompt_tokens": state["prompt_tokens"] + usage.prompt_tokens}   # WRONG
```

With one node per superstep this happens to work. With two, the reducer *adds the two running
totals together* and you double-count. The correct form is:

```python
return {"prompt_tokens": usage.prompt_tokens}   # this call's contribution only
```

Read-modify-write and reducers are mutually exclusive designs. Mixing them produces a bug
that appears only under concurrency — the worst kind to debug.

### When a reducer is wrong

Reducers are not universally correct, and the discipline is to decide per key:

- A **per-round scratch buffer** rebuilt from scratch each time it is written must be
  last-write-wins. Accumulating it duplicates the entire prompt every round.
- A **snapshot of external state** — for example a transcript read from a store — must be
  replaced wholesale. Accumulating it duplicates every prior item on each write.
- A **current-round result set** that the next round replaces must be last-write-wins, if
  something reads the previous value *before* the overwrite.

The rule: every key needs a **decided** answer, and "no reducer" must be a decision with a
reason, not a default.

### Conditional edges

```
add_edge(a, b)                                   # unconditional
add_conditional_edges(a, router_fn, path_map)    # router_fn(state) -> key in path_map
```

The router is a **pure function of state**. That matters for testability: routing decisions
are unit-testable with a dict, no graph required. And the `path_map` makes the reachable
targets explicit, so the graph structure is statically inspectable rather than inferred from
whatever strings a function might return.

### Streaming modes

`astream` accepts several modes and you can request more than one at a time:

| Mode | Yields |
|---|---|
| `values` | The full state after each step |
| `updates` | Only the delta each node returned — **and interrupts** |
| `custom` | Whatever a node writes via the stream writer |
| `messages` | LLM tokens, for chat-style streaming |
| `debug` | Everything |

Aegis uses `["custom", "updates"]`. `custom` carries the wire events a node deliberately
emits; `updates` is monitored **solely** to detect an interrupt (it arrives as a chunk with an
`__interrupt__` key). The state itself is read once at the end via `get_state`, not streamed —
because the stream is a *presentation* concern and the state is not.

### Checkpointing

A **checkpointer** persists the state after each superstep, keyed by
`config["configurable"]["thread_id"]`. Implementations range from an in-memory saver to
Postgres and Redis.

The property that matters architecturally: **with a shared durable checkpointer, any process
can resume any run by thread id.** With an in-memory saver, only the process that started it
can. That single difference is what separates "a demo that survives a page refresh" from "a
system that survives a deploy".

### `interrupt()` and `Command(resume=...)`

`interrupt(value)` inside a node:

1. Checkpoints the state.
2. Raises a control-flow exception that propagates out of `astream` as an `__interrupt__`
   chunk carrying `value`.

Resuming with `Command(resume=payload)`:

1. Loads the checkpoint.
2. **Re-executes the node from its beginning.**
3. This time `interrupt(...)` returns `payload` instead of suspending.

**"Re-executes from the beginning" is the load-bearing detail.** Anything before the
`interrupt` call in that node body happens **twice**. So a node that interrupts must not emit
events, write audit rows, or perform side effects before interrupting. Anything that must
happen exactly once is hoisted out of the node entirely.

`interrupt` raises a control-flow exception (LangGraph's `GraphBubbleUp` family), which has a
consequence for retry logic: a retry wrapper that catches broad exceptions will catch the
interrupt and re-run the node, re-interrupting forever. Control flow must be re-raised
unconditionally.

---

## 3. The gate: durable human-in-the-loop

### Why the naive design fails

```python
future = asyncio.Future()
pending[approval_id] = future
outcome = await future     # ← the run is now pinned to this process's memory
```

Four failure modes: a redeploy loses it; a crash loses it; a second worker cannot see it; and
a client disconnect leaves an orphan future nobody will ever resolve.

### The durable design

Three artifacts, and each has exactly one job:

| Artifact | Job |
|---|---|
| **The approvals row** (a database record) | The **source of truth** for the decision, and the exactly-once lock |
| **The checkpoint** | The **resumable run state** |
| **The in-process future** | A **fast path** for waking a still-open connection |

The critical property: **the first two are durable and the third is not, and the system is
correct if the third disappears entirely.**

### Exactly-once, formally

The mechanism is an **optimistic concurrency-controlled state transition**:

```sql
UPDATE approvals
   SET status = 'RESUMING', decided_at = now(), decided_by = :approver
 WHERE id = :approval_id
   AND status = 'PENDING'        -- ← the guard
```

`rowcount == 1` means you won; `0` means someone else did. Because the `WHERE` pins the old
status, the transition is atomic under the database's isolation guarantees, and the winner is
unique regardless of how many processes race.

This is **compare-and-swap** expressed in SQL. It is the same primitive as an atomic CAS
instruction, and it is where the exactly-once guarantee actually lives.

**LangGraph does not provide this.** LangGraph will happily resume a checkpoint any number of
times. The graph supplies *durability*; the database supplies *uniqueness*. Being precise
about that division is the single most valuable thing to be able to say about this design.

### The state machine

```
PENDING ──approve──▶ RESUMING ──resume completes──▶ APPROVED
   │                    │
   │                    └──resume FAILS──▶ back to PENDING
   ├──reject───▶ REJECTED  (terminal)
   └──SLA expiry──▶ EXPIRED / auto-REJECTED
```

`RESUMING` is an intermediate "armed" state and it creates a real hazard: **it is matched by
neither the decision path (which requires `PENDING`) nor the SLA sweeper (also `PENDING`).**
So if a resume fails and the row is left in `RESUMING`, the run is stranded forever — neither
approved nor rejected, and unreachable by any retry.

Any intermediate state in a distributed transition needs a **compensating action**. Here that
is a guarded release back to `PENDING`, itself pinned on `status == RESUMING` so it is
idempotent and safe against a concurrent finalise.

### The two-phase hand-off problem

Two resolution paths must not both execute:

**Live** — the connection is open, the run wakes and executes inside it.
**Parked** — the connection is gone; a resumer loads the checkpoint and drives it headless.

The temptation is to use "did a future exist for this approval?" as the test. That is wrong,
and the reason is an ordering argument:

1. The run registers the future.
2. The run yields several events.
3. The run awaits the future.

Between (1) and (3) the client can disconnect, closing the generator. The future exists and
**no one will ever await it**. Resolving it returns "success", the row is finalised APPROVED,
and the tool never runs.

The correct protocol is an **acknowledged hand-off** — essentially a two-phase commit between
the notifier and the waiter:

1. Notifier sets the result.
2. Notifier waits, briefly, for the waiter to signal it **took** the outcome.
3. Acknowledged → the live path owns it.
4. Not acknowledged → the gate is **disowned**; the notifier reports failure and the durable
   resumer takes over; a waiter that shows up later is told the gate is no longer its to
   execute and parks instead.

The acknowledgement check and the disown must be one **critical section** — no `await`
between checking "was it consumed?" and setting "abandoned" — so a racing timeout either sees
the consumption or wins the disown, never both. On a single-threaded event loop that is
achievable without a lock, by simply not yielding between the two.

**The residual, honestly.** The acknowledgement proves the run *consumed the outcome*, not
that the tool *finished*. A socket dying in the microseconds between the two still finalises
APPROVED. Narrowing it further would require the graph to acknowledge after `act`, which no
synchronous HTTP decision can wait for. The residual failure is **at-most-once**, never double
execution — which is the direction that matters for a refund.

---

## 4. Risk-tiered gating

### Why not confidence

A model's token probabilities are **not calibrated**: a model saying 90% is not right 90% of
the time. And its most dangerous state is *confidently wrong*, which is precisely when a
confidence gate does not fire. A confidence gate withdraws its protection exactly when you
need it most.

There is a principled way to get calibrated confidence — **conformal prediction**, which
measures error on a held-out set and derives intervals with a real coverage guarantee. Aegis
uses it in the ML module. But even a calibrated confidence is the wrong *signal* for this
decision: the question is not "how sure is the model" but "how bad is it if this is wrong".

### Risk as a property of the action

Each tool declares a tier. `LOW < MEDIUM < HIGH`, ordered by rank so comparison is
`rank(risk) >= rank(floor)`. The gate fires when any proposed action clears the floor.

Three properties this has and confidence does not:

- **Deterministic.** Same tool, same decision, every time. Testable.
- **Auditable.** "Why did this stop?" has the answer "because `issue_refund` is HIGH-risk",
  not "because a number was below a threshold".
- **Reviewable by non-engineers.** A risk map is a table a compliance officer can read.

### Fail-safe on unknown tools

An unregistered tool name — a hallucination, or one removed in a deploy — must resolve to
**HIGH**. Defaulting to LOW would make "invent a tool name" the cheapest bypass of the entire
gate.

### The composition with ML

The rule is: **ML informs; risk gates.** A calibrated prediction with its interval and top
feature attributions is injected into the planner and the final answer as *supporting
evidence*, so the answer can cite it. It never routes. A failed or low-confidence prediction
is simply omitted, and the run proceeds with zero ML involvement.

That separation is what lets you use an uncertain model safely: its output is evidence in a
decision a human or a deterministic rule makes, never the decision itself.

---

## 5. Reliability patterns

### Retry: exponential backoff with jitter

```
delay_n = min(initial · backoff^(n−1), max_interval) + jitter
```

Jitter matters at scale: without it, `n` clients failing simultaneously retry simultaneously,
producing a thundering herd that keeps the recovering service down.

**What to retry:** transient failures — connection resets, timeouts, 5xx. A well-designed
policy classifies these rather than retrying everything, so a deterministic failure (a 400, a
schema error) surfaces immediately instead of taking three times as long to fail.

**What never to retry:**

- **Tool execution.** Non-idempotent by definition. A timeout on "issue refund" means you did
  not hear back, not that it did not happen. Exactly-once comes from the DB lock; the graph
  must not independently re-run the action.
- **A node containing an interrupt.** It re-executes by design on resume; a retry would
  re-interrupt.
- **Anything already best-effort with its own degrade path.** Retrying a memory recall that is
  designed to return nothing on failure just triples the latency of the failure.

### Where the retry must sit relative to instrumentation

This is a subtle and generalisable point.

If you wrap a node as `emit_start → body → emit_finish` and then apply the retry to the
**whole wrapper**, a transient failure produces:

```
node_started, (failure), node_started, body, node_finished
```

Two starts, one finish, for one logical execution. Anything folding those events into a
per-node record produces a phantom node that never finished, with a null duration.

Putting the retry **inside** the wrapper, around the body only, gives one start/finish pair
per execution — and the measured duration spans every attempt, which is the honest wall clock
a user experienced.

### Background tasks: tracked, not fire-and-forget

`asyncio.create_task(coro)` returns a task the event loop holds only weakly. If nothing keeps
a reference, it can be garbage-collected mid-flight, and any exception it raised is silently
swallowed.

The correct pattern is: hold a reference in a module-level set, and attach a done-callback
that discards it and **logs any exception**. Fire-and-forget is not durability; a durable job
row is.

### TTL-bounded registries

Any in-process map keyed by run must be bounded, because the paths that would remove an entry
are not guaranteed to run — a background sweeper touching only database rows, a run failing
after registration, a client vanishing.

Eviction is safe **if and only if** the durable artifact remains. A parked-run handle can be
evicted because the checkpoint is still there and a late decision rehydrates by thread id
exactly as a fresh worker would. That is the argument that makes the eviction sound, and it is
worth stating as an argument rather than a hope.

---

## 6. Observability

### OpenTelemetry span trees

A **span** is one timed operation with attributes; spans nest into a tree. The agent run is
the root; each node is a child; retrieval, guardrail, tool and model calls nest beneath their
node.

**OpenInference** semantic conventions give spans a `kind` — `AGENT`, `CHAIN`, `RETRIEVER`,
`TOOL`, `GUARDRAIL`, `LLM` — so tracing tools can interpret an LLM application's trace without
bespoke parsing.

The wiring requirement: the node's span must be **current** while the body runs, so spans
opened inside it nest correctly. Otherwise you get a flat list of sibling spans and lose the
structure that made the trace worth collecting.

### Topology served, not hardcoded

A UI that draws the agent's flow can either hardcode the diagram or read it from the compiled
graph. Hardcoding drifts — and a drifted architecture diagram is worse than none, because it
is confidently wrong.

Serving `graph.get_graph()` as data means the diagram cannot lie. Two refinements worth
copying:

- **A tripwire on labels.** If the topology builder raises when a node has no registered
  human label, a new node cannot ship unlabelled.
- **A test against an offline snapshot.** If a stored snapshot must match the real compiled
  graph, a topology change is caught in CI rather than in a demo.

### Trace-level evaluation

Beyond "was the answer good", grade the **steps**: did retrieval return anything, did a
guardrail fire, did each tool succeed. That is what lets you attribute a quality regression to
a stage.

It belongs **off the hot path** — fired after the terminal event, on its own session, with
failures logged rather than raised. A grading failure must never disturb the run it is
grading.

---

## 7. Where each idea came from

| Idea | Source |
|---|---|
| ReAct — interleaved reasoning and acting | Yao et al. (2022) |
| Reflexion — verbal self-reflection loops | Shinn et al. (2023) |
| Plan-and-Execute separation | The LangChain/BabyAGI lineage |
| Supersteps / channels / reducers | Pregel — Malewicz et al. (2010) |
| Durable execution, checkpoint + resume | The workflow-engine tradition (Temporal, Azure Durable Functions) |
| Optimistic concurrency control | Kung & Robinson (1981) |
| Exponential backoff with jitter | AWS Architecture Blog; standard distributed-systems practice |
| Conformal prediction (why calibrated confidence still isn't the gate) | Vovk et al. |
| OpenTelemetry + OpenInference conventions | CNCF; Arize |

**Next:** [`20-in-aegis.md`](20-in-aegis.md) — the exact implementation.
