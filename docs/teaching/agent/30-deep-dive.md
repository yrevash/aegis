# The agent — the deep dive

Consistency, concurrency, durability and isolation — then the bugs. Every bug here was found
by an adversarial audit, reproduced before anything was changed, and fixed with a regression
test that failed on the pre-fix code. The commits are `cba2084` and `7d3c436`.

This is the module where the bugs are hardest and the stories are best. The approval gate
alone had four distinct correctness holes.

---

## Part 1 — The properties

### Where each guarantee actually comes from

The single most valuable table in this file:

| Guarantee | Provided by | **Not** provided by |
|---|---|---|
| Run survives a restart | The LangGraph checkpoint | The parked-run registry |
| Run resumes on another worker | A **shared durable** checkpointer + `thread_id` | The in-process handle |
| A decision is applied once | The DB `PENDING → RESUMING` compare-and-swap | LangGraph |
| **The tool executes once** | The same CAS | LangGraph, which will happily resume twice |
| Live and parked paths do not both execute | The acknowledged hand-off + the CAS | Either alone |
| The self-repair loop terminates | The counter incremented in `plan`, capped in config | The reflecting model |
| Token totals are correct under fan-out | `operator.add` reducers + delta-returning nodes | Read-modify-write |

If you take one thing from this module: **LangGraph gives you durability; the database gives
you uniqueness.** Conflating them is the most common mistake in durable-agent design.

### The exactly-once argument, end to end

Trace a single approval through every path:

1. `gate` decides `gated=True`. `approval` calls `interrupt(...)`; LangGraph checkpoints and
   the graph suspends.
2. The orchestrator registers a notify future, writes a durable `PENDING` row, registers the
   parked-run handle, emits three events, and awaits.
3. A human decides. `decide_approval` runs the guarded UPDATE
   (`backend/src/app/data/approvals.py:279-288`). Exactly one caller gets `rowcount == 1`.
4. `notify_live` sets the future's result and waits up to 1 second for the waiter to
   acknowledge consumption.
   - **Acknowledged** → the live run resumes inside its own socket, under the *same* CAS.
     The row is finalised `APPROVED` and the handle popped.
   - **Not acknowledged** → the gate is disowned, `notify_live` returns `False`, and
     `resume_parked_run` drives the run headless from the checkpoint.
5. Either way, `interrupt` returns the decision payload, `approval` re-executes, and
   `_route_gate`'s successor edge sends the run to `act` — **once**, because only one side
   ever got past step 3.

The residual, stated honestly in the code (`backend/src/app/agent/orchestrator.py:396-405`):
the acknowledgement proves the run *consumed the outcome*, not that the tool *finished*. A
socket dying in the microseconds between still finalises `APPROVED`. Narrowing it further
requires the graph to acknowledge after `act`, which no synchronous HTTP decision can wait
for. **The residual is at-most-once, never double execution** — which is the direction that
matters for a refund.

### The approval state machine, and its hazard

```
PENDING ──approve──▶ RESUMING ──resume completes──▶ APPROVED
   │                    │
   │                    └──ResumeFailedError──▶ released back to PENDING
   ├──reject───▶ REJECTED   (terminal)
   └──SLA expiry──▶ EXPIRED / auto-REJECTED
```

`RESUMING` is matched by **neither** `resolve_approval` (which requires `PENDING`) **nor**
`sweep_expired` (also `PENDING`). A row left there is stranded forever — neither approved nor
rejected, and unreachable by any retry.

Hence `_safe_release` (`backend/src/app/agent/orchestrator.py:511`), guarded on
`status == RESUMING` so it is idempotent and safe against a concurrent finalise, clearing
`decided_at`/`decided_by` so the row is *genuinely* pending again.

**The generalisable rule: every intermediate state in a distributed transition needs a
compensating action.**

### Concurrency

Four races, four mechanisms:

**Two decisions for one approval.** The CAS. One wins, the rest are no-ops.

**A decision racing the run's own `wait`.** The future is registered **before** the events are
emitted (`orchestrator.py:228-231`), so a decision arriving microseconds later still finds a
registered gate.

**A client disconnect racing a decision.** The `finally: registry.discard(approval_id)`
(`orchestrator.py:297-298`), reinforced by `aclosing` in the backend wrapper
(`backend/src/app/agent/orchestrator.py:245-262`) which propagates the disconnect immediately
rather than deferring cleanup to garbage collection.

**`notify_live`'s ack timeout racing `wait`'s consumption.** Handled by a **critical section
with no `await` in it** (`aegis/src/aegis/agent/approvals.py:183-192`): `wait` checks
`abandoned` and sets `consumed` in one uninterrupted stretch on the single-threaded event
loop, and `notify_live` re-checks `consumed.is_set()` before disowning
(`approvals.py:259-263`). Either the consumption is seen or the disown wins. Never both.

### Isolation

**The answer-cache scope.** `_cache_scope` (`aegis/src/aegis/agent/graph.py:363-371`) folds
tenant + persona + routed role into one opaque key. The docstring calls it *"a correctness +
isolation requirement, not an optimisation"*.

**The tenant seam.** `deps.current_tenant_id` (`deps.py:250-252`) defaults to ungoverned
`None`; the host wires the governance context (`backend/src/app/agent/deps.py:80-88`).

**Memory sessions are opened host-side.** `MemoryDeps.assemble`/`persist`
(`backend/src/app/agent/deps.py:106`, `:140`) open their own tenant-scoped session per call,
so the graph never threads one. The tenant scope is set on the connection before any query.

**Approvals carry a tenant.** `_enqueue_gate`
(`backend/src/app/agent/orchestrator.py:288`) stamps the owning tenant from the per-request
governance context so the durable row can be scoped for the inbox and the decision path.

### Failure modes and the degradation ladder

| Failure | Behaviour |
|---|---|
| Model call fails transiently | Retried up to 3× with backoff + jitter, inside the timing wrapper |
| Tool raises | Caught in `act` (`graph.py:914-915`), surfaced as `ok=False` with the error text; `reflect` may re-plan |
| Memory unavailable | `recall_memory`/`persist_memory` log and return `{}` (`graph.py:661-664`, `:709-710`) |
| ML unavailable | `ml_predict` logs and returns `{}` (`graph.py:730-733`). The run answers with zero ML |
| Answer cache read/write fails | Logged, planning proceeds (`graph.py:758-760`, `:1068-1070`) |
| Roster hook fails | `_resolve_roster` falls back to the core `qa`-only roster (`graph.py:1243-1245`) |
| Roster names an unroutable specialist | Falls back to `qa` **with a warning** (`graph.py:146-153`) and a build-time warning (`graph.py:157`) |
| Durable inbox write fails | Best-effort; the `approval_queued` event carries no SLA and the in-process path still resolves the gate (`backend/.../orchestrator.py:332-334`) |
| Budget cap tripped | `BudgetExceededError` → a `budget_exceeded` event and `run_finished(BLOCKED)` — not a crash (`orchestrator.py:328-343`) |
| Resume fails mid-flight | `ResumeFailedError` → the row is released to `PENDING` |
| Trace-eval fails | Logged by the done-callback; never touches the stream |

Note the split. **Safety-critical paths fail closed** (unknown tool ⇒ HIGH risk; a decision
nobody consumed ⇒ disowned to the durable resumer). **Enhancement paths degrade and log**
(memory, ML, cache, audit). Nothing in the second column can fail a run.

### Performance and cost

A single qa turn with everything on:

| Step | Model calls |
|---|---|
| `route` | 0 (deterministic) or 1 (tiebreak only) |
| `retrieve` | 1 rewrite + 1 rerank + 1 judge, ×rounds |
| `plan` | 1 |
| `generate` | 1 |
| Guardrails | up to 2 per rail stage (injection + content safety), input and output |
| `answer_memory` (specialist path) | 1, and it skips retrieve/ml/plan/gate/act entirely |

Two structural savings: an **answer-cache hit** in `plan` skips the generation call entirely
(`graph.py:747-775`), and the **deterministic router** costs nothing on the common path.

Every internal call's usage is accrued as a delta into the reduced counters — including the
retrieval loop's rewrite and judge calls (`graph.py:636-637`) — so `cost_usd` on
`run_finished` reflects reality rather than only the calls the graph made directly.

---

## Part 2 — The bugs

### Bug 1 — An approval could be finalised APPROVED for a tool that never ran

**The best bug in the codebase.** It is a genuine distributed-systems bug, it is subtle, and
the fix is a real protocol.

**What it was.** `decide_approval` used `ApprovalRegistry.resolve()` — set the future's result,
return `True` if a pending gate existed — as its test for "a live run took this decision".

**Why it mattered.** `resolve()` returning `True` proves a **future existed**, not that a run
**consumed** it. And the ordering makes that gap real, not theoretical:

```
1. registry.register(approval_id)      ← the future now exists
2. yield node_started
3. yield approval_queued
4. yield approval_required
5. await registry.wait(...)            ← only NOW does anyone consume it
```

The registration is deliberately before the emissions so a very fast decision cannot race past
the wait. But between (1) and (5) sit an `await` and **three `yield`s**, and a disconnected SSE
client closes the generator at any of them.

So: client disconnects at step 3. Decision arrives. `resolve()` finds the orphan future, sets
its result, returns `True`. `decide_approval` reads that as "a live run woke up", takes the
`live_woken` branch, and **finalises the row `APPROVED`**.

The audit trail says a human approved a $4,200 refund. The refund was never issued. And
because the row is `APPROVED`, no resumer will ever pick it up — the run is silently dead with
a clean record.

**The fix — an acknowledged hand-off.** `notify_live`
(`aegis/src/aegis/agent/approvals.py:221-271`):

```python
gate.future.set_result(ApprovalOutcome(...))
try:
    await asyncio.wait_for(gate.consumed.wait(), ack_timeout)   # 1.0s
except TimeoutError:
    if gate.consumed.is_set():
        return True                     # consumed in the same loop turn the timeout fired
    gate.abandoned = True
    self._forget(approval_id, gate)
    return False                        # → the durable resumer owns it
return True
```

And `wait` marks consumption **and checks for disowning in one critical section**
(`approvals.py:183-192`) — no `await` between them, so on the single-threaded event loop a
racing timeout either sees `consumed` or wins `abandoned`, never both.

A waiter that wakes to find its gate disowned raises `GateHandedOffError`
(`approvals.py:84-92`), which **subclasses `TimeoutError`** specifically so the orchestrator's
existing park path handles it: the run parks, the resumer executes, exactly one side proceeds.

**Three supporting fixes shipped with it:**

- `discard` in a `finally` (`orchestrator.py:297-298`) so a closed generator never leaves an
  orphan.
- `aclosing` in the backend wrapper (`backend/src/app/agent/orchestrator.py:245`) so a client
  disconnect propagates *immediately* rather than waiting for garbage collection to close the
  inner generator.
- `resolve()` kept, but its docstring now says it is the **fire-and-forget** form for
  in-process callers that also drive the stream, and *"the durable decision path must use
  `notify_live` instead"* (`approvals.py:203-206`).

**What to say about it.** "A registered future proves a gate exists, not that anyone will
consume it." That one sentence is the bug, and it generalises to every callback registry.

---

### Bug 2 — A failed resume stranded the run forever

**What it was.** `resume_parked_run` popped the handle first, drove the run, and flattened any
failure into `False`.

**Why it mattered.** By the time the resume runs, the caller has **already won** the
`PENDING → RESUMING` transition. If the drive then fails — a tool raises, a worker loses its
store — the row sits in `RESUMING`.

And `RESUMING` is matched by **neither**:

- `resolve_approval`, which requires `status == PENDING`, so a retried decision is a no-op;
- `sweep_expired`, which also only touches `PENDING`, so the SLA sweeper never sees it.

The run is stranded: neither approved nor rejected, invisible to the inbox, unreachable
forever. And the handle was already popped, so even the in-process fast path is gone.

**The fix, three parts:**

**A distinct exception.** `ResumeFailedError` (`aegis/src/aegis/agent/orchestrator.py:86-93`),
raised for a failed checkpoint read *and* a failed headless drive
(`orchestrator.py:401-403`, `:415-417`). Its docstring: *"Distinct from 'there is nothing to
resume' (reported as `False`)."* Note the checkpoint-read failure is deliberately **not**
treated as "no checkpoint" — *"a broken store is a failure, not an absence"*.

**Peek, then pop.** The handle is only `get()`-ed before the drive and `pop()`-ed after it
returns (`backend/src/app/agent/orchestrator.py:459`, `:482`), so a failure leaves the handle
parked and the checkpoint reachable.

**A compensating release.** `_safe_release` (`orchestrator.py:511-540`) reverses exactly the
`PENDING → RESUMING` transition, guarded on `status == RESUMING` (idempotent, safe against a
concurrent finalise) and clearing the decision stamps so the row is genuinely pending — visible
to the inbox, matched by a later decision, swept by the SLA sweeper.

---

### Bug 3 — Both registries were unbounded, and each entry was a whole run

**What it was.** `ApprovalRegistry._gates` and `ParkedRunRegistry._parked` were plain dicts
with no eviction.

**Why it mattered.** Several paths never remove an entry:

- The SLA sweeper expires or auto-rejects an approval by touching **durable rows only** — it
  has no idea the in-process maps exist.
- A run that fails after parking may never reach a `pop` site.
- A live socket can vanish mid-gate.

And a `ParkedRun` holds a **compiled LangGraph plus its checkpointer** — with the default
`InMemorySaver`, that is the entire state of the run. This is not a few-kilobyte leak; it is a
leak of whole runs, growing monotonically for the process's lifetime.

**The fix.** TTL eviction on both, at 3600 seconds
(`DEFAULT_GATE_TTL_SECONDS`, `approvals.py:51`; `DEFAULT_PARKED_TTL_SECONDS`, `:54`), with an
injectable monotonic clock so the eviction is testable. `ApprovalRegistry.sweep`
(`approvals.py:307`) only evicts gates with **no active waiter** (`gate.waiting == 0`), so a
long-held live gate is never pulled out from under its waiter.

**The argument that makes eviction safe** is in the class docstring
(`approvals.py:365-367`): *"the durable checkpoint remains, so a decision that arrives after
eviction rehydrates by `thread_id` exactly as a fresh worker does."* The in-memory handle is a
fast path, not the source of truth — and once you can state that, evicting it is obviously
fine.

**A related design note.** Neither registry defines `__len__`, deliberately
(`approvals.py:299-303`, `:427-431`): *"a registry is passed around as
`registry or get_default()`, and a `__len__` would make an empty one falsy — silently swapping
an injected registry for the process-wide singleton."* That is a genuinely nasty Python
footgun and the mitigation is to expose `pending_ids()` / `ids()` instead.

---

### Bug 4 — A rejected gate was reported APPROVED in multi-round runs

**What it was.** `run_summary` decided whether a gate was approved by scanning the **whole**
event stream for a `tool_result`.

**Why it mattered.** Work through a self-repair run:

1. Round 1: the planner proposes a **LOW**-risk tool. No gate. It executes → `tool_result`.
2. `reflect` judges it insufficient and loops back to `plan`.
3. Round 2: the planner proposes a **HIGH**-risk tool. The gate fires.
4. The human **rejects**.

A reject routes straight to `generate` (`graph.py:1207-1211`), so nothing executes after the
gate. But the whole-stream scan finds round 1's `tool_result` and concludes the gate was
approved.

The run summary — the record a reviewer reads — says a human approved a HIGH-risk action they
explicitly refused.

**The fix.** Two rules (`harness.py:332-361`):

**Report the *last* `approval_required`** (`harness.py:335-338`), because a multi-round run can
gate more than once and `resolved`/`approved` describe where the run ended up.

**Scan only *after* that gate's index** (`harness.py:348-350`):

```python
executed_after_gate = any(_etype(e) == "tool_result" for e in events[gate_idx + 1:])
```

with the reasoning in the comment. And `"approved": None if parked else executed_after_gate`
(`harness.py:361`) — three states, not two: a parked run is `None`, not `False`.

---

### Bug 5 — `_accrue` was read-modify-write, which loses spend under any fan-out

**What it was.** Four call sites did:

```python
"cost_usd": state.get("cost_usd", 0.0) + usage.cost_usd
```

and the state keys had no reducers.

**Why it mattered.** Read-modify-write over graph state is correct **only** while no two nodes
run in the same superstep. Add any parallel branch and:

- Both nodes read the same running total.
- Both return `total + their_delta`.
- Last write wins — one branch's spend vanishes.

For a token counter feeding **per-tenant USD budget caps**, that is not a reporting glitch. It
is a spend ceiling that silently stops binding.

There is a second, louder failure: for a key LangGraph knows must be merged, two updates in one
superstep raise `InvalidUpdateError` ("can receive only one value per step"). So depending on
the key you get either silent loss or a crash.

**The fix.** Reducers plus deltas, and both halves are required:

```python
# state.py:128, 146-148
plan_iterations:   Annotated[int, operator.add]
prompt_tokens:     Annotated[int, operator.add]
completion_tokens: Annotated[int, operator.add]
cost_usd:          Annotated[float, operator.add]
```

```python
# graph.py:1313-1317 — THIS call's contribution only
return {"prompt_tokens": int(usage.prompt_tokens), ...}
```

**And the harder half of the fix: deciding which keys must *not* be reduced.** Three stay
last-write-wins, each with the reason recorded (`state.py:21-37`):

- `messages` — a per-round scratch buffer; accumulating duplicates the whole prompt every
  self-repair round.
- `conversation` — a snapshot of what the memory store holds; accumulating duplicates every
  prior turn and defeats the memory layer's turn cap.
- `tool_results` — replaced wholesale by `act`; accumulating would make `reflect` re-see an
  already-repaired failure and burn the iteration budget.

Plus the standing rule (`state.py:39-40`): *"Adding a parallel branch that writes either of
those keys requires giving it a reducer first."*

**The lesson worth stating:** a reducer is not "make it safe". It is a *decision about
semantics*, and it has to be paired with nodes that return deltas. Adding `operator.add` to a
key whose nodes do read-modify-write makes it strictly worse — now you double-count.

---

### Bug 6 — `roster.roles` was iterated as an attribute when it is a method

**What it was.** `_warn_unroutable_specialists` — the startup check that warns when a roster
declares a specialist the graph has no node for — did:

```python
declared = roster.roles          # a bound METHOD, not a list
roles = {str(r) for r in declared or ()}
```

**Why it mattered.** Iterating a bound method raises `TypeError`. And the whole block is
wrapped in `except Exception` — deliberately, because a roster is host data and a bad entry
must not stop the agent serving.

So the exception was swallowed. Which means:

> **The warning could never fire, for any roster, ever — which is exactly the silence it
> exists to break.**

A roster declaring a specialist with no handler node would be silently answered by the `qa`
pipeline, and the diagnostic built specifically to catch that wiring mistake was itself broken
by a one-word bug, protected by the very `except` that made it safe.

**The fix** is one character — `roster.roles()` (`graph.py:169`) — but the comment above it is
the valuable part (`graph.py:166-168`): *"`roles` is a METHOD on every roster implementation,
not an attribute. Iterating the bound method raised TypeError, which the except below then
swallowed — so this warning could never fire for any roster, which is exactly the silence it
exists to break. Call it."*

**The generalisable lesson: a broad `except` around diagnostic code can silently disable the
diagnostic.** If a warning has never fired in production, that is data — either the condition
never occurs, or the check is broken, and you cannot tell which without testing the check
itself.

---

### Bug 7 — Retried nodes emitted two `node_started` events for one execution

**What it was.** The retry policy was wired as LangGraph's node-level
`add_node(..., retry_policy=...)`, which re-invokes the **whole registered callable** — and
the registered callable is the `_timed` wrapper.

**Why it mattered.** The wrapper emits `node_started` **before** calling the body. So a
transient failure produced:

```
node_started(plan), <failure>, node_started(plan), <body>, node_finished(plan)
```

Two starts, one finish, for one logical node execution. `run_summary` folds start/finish pairs
into per-node records, so this produced an **extra, permanently unpaired node record with
`duration_ms: None`** — a phantom node in the trace that never finished, visible to anyone
reading the run.

**The fix.** `_call_with_retry` (`graph.py:228`) retries **only the body**, inside the wrapper
(`graph.py:312`). One node execution is exactly one `node_started`/`node_finished` pair, and
the measured duration spans every attempt — *"which is the honest wall clock"*
(`graph.py:234-240`).

Two details in the same function worth knowing:

`GraphBubbleUp` is re-raised unconditionally (`graph.py:248-250`). Interrupts are control
flow, not failures — catching one and retrying would re-interrupt forever.

`_should_retry` (`graph.py:213`) honours all three shapes `RetryPolicy.retry_on` accepts: a
callable predicate, a single type, or a sequence. The default `default_retry_on` admits only
transient classes, so a deterministic failure surfaces immediately instead of taking three
times as long to fail.

---

### Bug 8 — The console drew a graph that contradicted the code

**What it was.** The frontend hardcoded a 9-node DAG.

**Why it mattered, in two ways.**

**It was wrong about the most important design decision in the system.** The hardcoded diagram
drew the human gate branching off the **ML** node — implying ML decides when to stop for a
human. The code does the opposite: the gate fires on **tool risk**, and ML never routes
(`graph.py:187-197`, `graph.py:829-857`). Anyone learning the architecture from the console
learned it backwards.

**It could not light 7 real nodes.** The graph had grown past 9 nodes, so a third of the
execution was invisible in the live view.

A drifted architecture diagram is worse than no diagram, because it is confidently wrong and
nobody double-checks a picture.

**The fix.** Topology is served from the compiled graph:
`graph_topology()` (`aegis/src/aegis/agent/topology.py:97`) calls `graph.get_graph()` and
returns nodes and edges as plain JSON, with `START`/`END` folded into `entry`/`terminal` flags.

Two guards keep it honest:

**A tripwire on labels.** `NODE_LABELS[nid]` (`topology.py:127`) raises `KeyError` for an
unlabelled node — *"a deliberate tripwire, so a new node cannot ship without its label"*
(`topology.py:114-117`).

**A CI test against an offline snapshot**, so a topology change that drifts from the stored
shape fails the build rather than the demo.

And the topology is built from `_inert_deps()` (`topology.py:75`) — every callable is
`_unreachable` — because *"the topology is a property of the wiring, not of the particular
capabilities injected into it"*.

---

### Bug 9 — Specialist routing was a hardcoded binary, so a third specialist vanished

**What it was.** The conditional edge out of `route` was
`"memory" → answer_memory, else recall_memory`.

**Why it mattered.** The supervisor router classifies into an arbitrary number of roster
specialists. But the *graph* can only dispatch to a node that exists. With a hardcoded binary,
a third specialist an adapter declared was **silently swallowed into the qa pipeline with no
signal anywhere** — not an error, not a warning, not a trace attribute. It simply never
happened.

**The fix.** A table (`SPECIALIST_NODES`, `graph.py:128-131`) that the path map is derived from
(`graph.py:1185-1189`), plus `_route_specialist` (`graph.py:137`) which falls back to `qa`
**loudly**:

```python
logger.warning(
    "Roster specialist %r has no handler node; falling back to %r. Add a node "
    "and a SPECIALIST_NODES entry to make it routable.", role, _FALLBACK_ROLE)
```

Plus `_warn_unroutable_specialists` (`graph.py:157`) at **build time**, so the wiring gap
surfaces once at startup rather than once per run.

The design statement (`graph.py:122-124`): *"adding a specialist to an adapter roster is not
enough to make it routable — it needs a handler node and an entry here."* The seam is
deliberately explicit rather than magic.

---

### Bug 10 — The router matched substrings, and the tiebreak returned the rejected role

Two defects in the supervisor.

**Substring matching.** `"memory" in query` made *"memorandum"* match the memory specialist and
*"bill"* match *"billboard"*. A specialist could win on a word it has nothing to do with.

Fixed by `_phrase_present` (`router.py:91`), which is word-boundary aware:

```python
pattern = r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])"
```

Boundaries are alphanumeric-aware rather than `\b`-only, so a multi-word or punctuated hint
("out of office", "p&l") still matches (`router.py:93-97`).

**The rejected-role bug.** The cheap-LLM tiebreak scanned the roster in declaration order for a
role id appearing anywhere in the reply. So a reply of:

> `not qa — use memory`

returned **`qa`** — because `qa` appears in the reply and is declared first. The model
explicitly rejected `qa` and the router chose it.

Fixed by strict matching (`router.py:246-263`): an exact reply wins; otherwise the reply must
mention **exactly one** role on word boundaries; a reply naming several is logged and treated
as inconclusive, falling back to the default. *"A reply naming several is a non-answer, not a
vote for whichever the roster happens to list first"* (`router.py:225-226`).

---

### Bug 11 — Two memory-adjacent wiring gaps in the graph

Both belong to memory but were fixed in `agent/`, and both are the same *shape*: a value that
could never be available at the point it was needed.

**Both memory branches always passed `query_vec=None`.** `recall_memory` runs **upstream** of
`retrieve`, and `retrieve` is the only node that sets `query_vec`. `answer_memory` is on a
branch that never reaches `retrieve` at all. So semantic fact recall silently degraded to
recency-only — six newest facts, plausible-looking, not semantic.

Fixed by `_recall_vector` (`graph.py:1278-1300`), which prefers a vector in state and otherwise
calls the injected `deps.embed_query` hook — with the analysis in the docstring
(`graph.py:1281-1287`).

**The query rewriter got no history.** `retrieve` passed `state["messages"]`, which is a
per-planning-round scratch buffer written by `plan` — and `plan` runs *after* `retrieve`. There
is no ordering of the graph in which it could be populated at rewrite time.

Fixed by sourcing the transcript from `recall_memory`, which runs immediately upstream
(`graph.py:522-528`), keeping `messages` as a fallback so the no-memory path is byte-identical.

**The shared lesson, and it is a good one:** before assuming a state key is available, check
**who writes it and when**. Both of these were invisible because "empty" is a legitimate value
for both keys — so "not yet written" and "genuinely nothing" were indistinguishable.

---

## Part 3 — Things worth noticing that are not bugs

**Memory nodes are wired plain, not through `_timed`** (`graph.py:1139-1144`). A `_timed`
wrapper emits `node_started`/`node_finished` even on a no-op, which would change the trace for
every single-shot run. A plain node returning `{}` emits nothing.

**`approval` is also wired plain** (`graph.py:1158`) because it re-executes on resume — the
orchestrator emits its `node_started` once from the interrupt value instead.

**`stream_answer` paces an already-guarded string** (`graph.py:1073-1091`). The gateway call is
non-streaming **on purpose**: `generate → guard_output → stream` means no model output reaches
the user before the rail cleared it. *"You cannot unsay a leaked secret."* The docstring also
says what real token streaming would require — a streaming-aware output rail with the ability
to withhold.

**`aclosing` is load-bearing** (`backend/src/app/agent/orchestrator.py:240-245`), not tidiness.
A bare `async for` would leave the inner generator to garbage collection on a client
disconnect, deferring the `finally` that discards the notify future.

**Background tasks are tracked, not fire-and-forget.** Both `_TRACE_EVAL_TASKS`
(`backend/src/app/agent/orchestrator.py:60`) and `_CONSOLIDATION_TASKS`
(`backend/src/app/agent/deps.py:67`) hold references so the loop cannot GC a task mid-flight,
with done-callbacks that surface swallowed exceptions.

**`count_approved` excludes `RESUMING`** (`backend/src/app/data/approvals.py:220-226`). An
in-flight row is not yet an approved action, so the "actions approved" tile does not
over-report.

**The parked handle is popped on every terminal path** — completion (`orchestrator.py:304`),
budget block (`:332`), and error (`:349`) — because *"an errored run is terminal, so a retained
handle pins a compiled graph plus its checkpointer with nothing left to resume."*

**`get_default_spec`-style loud failure appears here too:** `run_agent` raises if `deps` is
omitted (`orchestrator.py:143-147`) rather than constructing a default. A graph with no
capabilities would run and do nothing useful, successfully.

**Next:** [`40-diagrams.md`](40-diagrams.md) — every path, drawn.
