# The agent — interview questions and answers

Claim first, then the reason, then a concrete detail from this system. This is the module with
the hardest questions and the best stories.

---

### "Walk me through your agent architecture."

Plan-and-execute with a bounded reflection loop, expressed as an explicit LangGraph state
machine rather than a `while` loop.

The flow is: **input guardrail → supervisor route → recall memory → agentic retrieval → ML
predict → plan → risk gate → act → reflect → generate → output guardrail → stream → persist
memory**. Plus a `reflect → plan` edge for bounded self-repair, and a `gate → approval` branch
that pauses the run for a human.

Every node is a closure over injected capabilities — the model gateway, retrieval, guardrails,
the tool registry, memory, the audit sink — so injecting fakes drives the entire vertical slice
offline with no API key, no network and no database.

---

### "Why LangGraph and not just a loop?"

Four things a loop can't do, and the third is the one that mattered.

**Observability.** A loop is one opaque 40-second call. A graph gives you a node per step, so
the trace is a tree — the run, the retrieval inside it, the three model calls inside that.

**Explicit branching.** "If the proposed action is risky, go to approval" is an `if` buried in
a loop and an *edge* in a graph. Once you have five of those, the loop is a tangle and the
graph is a diagram you can serve to a UI.

**Durable pause.** This is the real reason. A loop can `await` a future for human approval, but
that future lives in one process's memory. Deploy, crash, or scale to a second worker and the
run is gone. LangGraph checkpoints the whole state after each superstep, so a run can pause and
resume — on a different machine.

**Testability of routing.** The conditional-edge functions are pure functions of state, so
routing decisions are unit-testable with a dict.

---

### "Explain supersteps and reducers."

LangGraph implements Pregel. Execution proceeds in **supersteps**: every node whose channels
were updated runs, their outputs are merged into the channels, repeat.

Most of the time one node runs and the merge is trivial. But with a **fan-out** — two branches
in one superstep — two nodes return updates for the same key and something must combine them.

The default is **last write wins**. For "the current answer", that's correct — the node that
produced it owns it. For "total tokens spent" it is **silent data loss**: two model calls
happened and one is counted.

A **reducer** is a merge function attached to a key. We annotate four:
`plan_iterations`, `prompt_tokens`, `completion_tokens`, `cost_usd` — all `operator.add`.

**And the half people get wrong:** a reducer only helps if nodes return **deltas**. We had four
call sites doing read-modify-write — read the running total, return `total + mine`. That's
correct only while nothing runs in parallel, and adding a reducer to *that* makes it worse:
now you double-count. Our `_accrue` helper returns only the current call's contribution.

---

### "Are there keys you deliberately didn't give a reducer?"

Three, and each would be actively wrong as an accumulator. That's the discipline — "no
reducer" has to be a **decision**, not a default.

**`messages`** is a per-planning-round scratch buffer, rebuilt from scratch each time `plan`
runs. Accumulating it would duplicate the entire prompt on every self-repair round.

**`conversation`** is the prior-turn transcript recalled from memory — a snapshot of what the
store already holds, not something nodes contribute to. Accumulating it would duplicate every
prior turn on each write and silently defeat the memory layer's turn cap.

**`tool_results`** is replaced wholesale by `act`. The previous round is read *before* that
overwrite, to build the self-repair prompt — so the data is used, not lost. Accumulating it
would make `reflect` re-see an already-repaired failure and burn the iteration budget.

And there's a standing rule in the docstring: adding a parallel branch that writes any of those
requires giving it a reducer first.

---

### "How does the human approval gate work?"

Three artifacts, each with one job.

`gate` decides on **tool risk**. If a proposed action is at or above the threshold, the graph
routes to an `approval` node, which calls LangGraph's `interrupt()`. That checkpoints the state
and suspends the graph.

The orchestrator then registers an in-process notify future, writes a **durable PENDING row**,
retains a resumable handle, and emits `approval_required` to the client.

When a human decides, the decision hits a single shared path: an optimistic
`PENDING → RESUMING` transition in the database, then an attempt to wake a live socket, and if
that fails, a headless resume from the checkpoint.

The division of responsibility is the thing to be precise about:

- **The checkpoint** makes the run resumable.
- **The database row** is the source of truth and the exactly-once lock.
- **The in-process future** is a fast path for waking an open connection — and the system is
  correct if it disappears entirely.

---

### "Where does exactly-once actually come from?"

Not LangGraph. LangGraph will happily resume the same checkpoint any number of times — a resume
is just "continue from here".

It comes from **one atomic compare-and-swap in the database**:

```sql
UPDATE approvals SET status='RESUMING', decided_at=now(), decided_by=:approver
 WHERE id=:approval_id AND status='PENDING'
```

`rowcount == 1` means you won. Because the `WHERE` pins the old status, exactly one caller's
update affects the row — a double-click, a retried request, two workers, all become no-ops.

**LangGraph gives you durability; the database gives you uniqueness.** Conflating those is the
most common mistake in durable-agent design, and if I'm asked one question about this module I
want it to be this one.

---

### "Tell me about a bug in the approval flow."

The best one in the codebase, and it's a genuine distributed-systems bug.

We used `registry.resolve()` — set the future's result, return `True` if a pending gate existed
— as the test for "a live run took this decision". **That proves a future existed, not that a
run consumed it.**

The ordering makes the gap real. The streaming run registers the future *before* it emits the
approval events — deliberately, so a fast decision can't race past the wait. Then it yields
three events, and only then awaits. If the client disconnects in that window, the generator is
closed and nothing will ever take the outcome.

So: client disconnects, decision arrives, `resolve()` finds the orphan future, returns `True`,
we read that as "live run woke up" and **finalise the row APPROVED**. The audit trail says a
human approved a $4,200 refund. The refund was never issued. And because the row is APPROVED,
no resumer will ever pick it up — the run is silently dead with a clean record.

**The fix is an acknowledged hand-off.** `notify_live` sets the result, then waits up to a
second for the waiter to signal it *took* the outcome. No acknowledgement means the gate is
**disowned** — and a waiter that shows up later raises `GateHandedOffError` and parks instead of
executing. That error subclasses `TimeoutError` specifically so the orchestrator's existing
park path handles it. Exactly one of the two sides ever proceeds.

The check-and-disown is one **critical section with no `await` in it**, so a racing timeout
either sees the consumption or wins the disown — never both.

**And I'd state the residual honestly:** the acknowledgement proves the run consumed the
outcome, not that the tool finished. A socket dying in the microseconds between still
finalises APPROVED. Closing that needs the graph to acknowledge after `act`, which no
synchronous HTTP decision can wait for. The residual is **at-most-once, never double
execution** — which is the direction that matters for a refund.

---

### "What else went wrong there?"

Three more, and they're all in the same family: an intermediate state with no way out.

**A failed resume stranded the run forever.** By the time the resume runs, the caller has
already won the `PENDING → RESUMING` transition. If the drive then failed, the row sat in
`RESUMING` — which is matched by **neither** a later decision (requires `PENDING`) **nor** the
SLA sweeper (also `PENDING`). Neither approved nor rejected, invisible, unreachable.

Fixed three ways: a distinct `ResumeFailedError` so "nothing to resume" and "resume broke" are
different outcomes; peeking the handle and only popping it after the drive returns; and a
compensating release back to `PENDING`, guarded on `status == RESUMING` so it's idempotent.

**The general rule: every intermediate state in a distributed transition needs a compensating
action.**

**Both registries were unbounded.** The SLA sweeper only touches durable rows, so it never
removes an in-process entry. A run failing after parking never reaches a pop site. And each
parked entry holds a **compiled graph plus its checkpointer** — the whole state of a run. That's
not a kilobyte leak, it's a leak of entire runs.

Now both are TTL-bounded, and the argument that makes eviction safe is that **the durable
checkpoint remains** — a decision arriving after eviction rehydrates by thread id exactly as a
fresh worker would. The in-memory handle is a fast path, not the source of truth.

**A rejected gate was reported as approved.** The run summary decided approval by scanning the
whole event stream for a `tool_result`. In a multi-round run — round 1 executes a LOW-risk tool
with no gate, round 2 proposes a HIGH-risk one, the human rejects — the pre-gate result stood
in as evidence of execution. Now we scan only *after* the gate's index, and report the **last**
`approval_required`, because a run can gate more than once.

---

### "Why does the gate fire on tool risk instead of model confidence?"

Because a model's confidence is not calibrated, and its most dangerous state is being
**confidently wrong** — which is exactly when a confidence gate does not fire. It withdraws its
protection precisely when you need it most.

Risk is a property of the **action**, not of the model's mood. Issuing a refund is high-risk
whether or not the model feels sure. A model that's 99% confident about a $4,200 refund still
stops for a human.

Three practical properties that follow: it's deterministic and therefore testable; it's
auditable — "why did this stop?" answers "because `issue_refund` is HIGH-risk", not "because a
number was below a threshold"; and the risk map is a table a compliance officer can read.

**One detail I'd add:** an unregistered tool name — a hallucination, or one removed in a deploy
— resolves to **HIGH**. Defaulting to LOW would make "invent a tool name" the cheapest bypass of
the entire gate.

---

### "So what does your ML model do, if it doesn't gate?"

It informs. **ML informs; risk gates.**

An optional, best-effort step runs *before* planning. When the adapter resolves a subject, it
produces a calibrated prediction with a conformal interval and the top feature attributions,
and those are injected into the planner and the final answer as **supporting evidence** — so the
answer can cite "the model predicts X at 90% coverage".

It never routes. A failed or low-confidence prediction is simply omitted and the run proceeds
with zero ML. The `gate` node emits the ML explanation as an explicitly informational event
carrying no gating semantics.

That separation is what lets you use an uncertain model safely: its output is evidence in a
decision that a human or a deterministic rule makes, never the decision itself.

---

### "Your self-repair loop uses an LLM. How do you know it terminates?"

Structurally, not heuristically. The counter is incremented in **`plan`**, and `reflect` checks
`iteration < max_plan_iterations` before setting the retry flag. So the reflecting model
chooses *whether* to retry — it cannot extend the budget. No model output can.

That's the difference between a guarantee and a hope. A design where the model decides when to
stop eventually loops forever.

The judgement is also deliberately **domain-agnostic**: it reads a structured `ok` flag off each
tool result, never hardcoded domain logic. This platform gets repointed at a new problem by
rewriting an adapter, so "did this work?" can't live in the orchestration.

And the reflection event carries four distinct reasons — goal met, self-repair disabled, budget
exhausted with the count, or re-planning with the round number — so the trace says *which* of
the four ways it ended.

---

### "Which nodes do you retry, and which don't you?"

Retries on the five nodes whose body is a network call to the model gateway — route, retrieve,
plan, generate, guard_output, plus the memory specialist. Three attempts with exponential
backoff and jitter, and LangGraph's default classifier so only transient failures retry and a
deterministic error surfaces immediately.

**Never on `act`.** It executes real, externally-visible actions. A timeout on "issue refund"
doesn't mean the refund didn't happen — it means you didn't hear back. Exactly-once comes from
the DB lock, not the graph, so the graph must not independently re-run the action.

**Never on `approval`** — it re-executes on resume by design; a retry would re-interrupt.

**Never on the memory nodes** — they're already best-effort with their own degrade-to-nothing
path, so a retry just triples the latency of a failure.

---

### "You mentioned a bug in the retry itself."

Yes, and it generalises well beyond retries.

We originally wired the policy as LangGraph's node-level `retry_policy=`, which re-invokes the
**whole registered callable**. And the registered callable is our timing wrapper, which emits
`node_started` *before* calling the body.

So a transient failure produced two `node_started` events and one `node_finished` for one
logical execution. The run summary folds start/finish pairs into per-node records, so it
produced a **phantom node that never finished, with a null duration** — visible to anyone
reading the trace.

The fix was to move the retry **inside** the wrapper, around the body only. One execution is
exactly one start/finish pair, and the measured duration spans every attempt, which is the
honest wall clock a user experienced.

**The general point: where a retry sits relative to your instrumentation changes what your
instrumentation means.**

One more detail in that function: LangGraph's interrupt raises a control-flow exception, so the
retry re-raises that family unconditionally. Catching it and retrying would re-interrupt
forever.

---

### "How does a run resume on a different worker?"

Two conditions have to hold, and the second is the one people miss.

**A shared, durable checkpointer.** With the default in-memory saver, only the process that
started the run can resume it. With a Postgres saver, any process can. Our `build_agent`
defaults to a process-wide shared checkpointer so every compiled graph checkpoints into one
store.

**Resume by `thread_id`, not by object.** When a decision arrives at a worker with no
in-process handle, we rebuild the graph on the shared checkpointer and resume with
`config={"configurable": {"thread_id": run_id}}`. If the store holds no resumable checkpoint,
there's nothing to rehydrate and we report that honestly rather than pretending.

Both entry conditions — retained handle and fresh worker — converge on the same
checkpoint-driven resume, so there's one code path to reason about.

---

### "Why don't you stream tokens from the model?"

Because the output guardrail needs the complete answer.

The order is `generate → guard_output → stream`. The `stream` node paces an **already-guarded**
string onto the socket in word chunks — that's real transport-level streaming, the client
renders progressively — but no model output reaches the user before the rail cleared it.

Streaming raw tokens would put unguarded text on screen and make a block unenforceable after
the fact. You cannot unsay a leaked secret.

It's a cosmetic typing effect traded for a real safety property, and it's documented as that
trade rather than presented as a limitation. Real token streaming would need a *streaming-aware*
output rail — incremental scanning with the ability to withhold — not just a streaming API call.

---

### "How do you keep the architecture diagram honest?"

We serve it from the compiled graph instead of drawing it.

The console used to hardcode a 9-node DAG. Two problems. It couldn't light 7 real nodes,
because the graph had grown. And — worse — it drew the human gate branching off the **ML** node,
implying ML decides when to stop for a human. The code does the opposite. Anyone learning the
architecture from the console learned the single most important design decision backwards.

**A drifted architecture diagram is worse than no diagram, because it's confidently wrong and
nobody double-checks a picture.**

Now `graph_topology()` calls `graph.get_graph()` and returns nodes and edges as JSON. Two
guards keep it honest: the label lookup **raises** for a node with no registered label, so a new
node can't ship unlabelled; and a CI test compares the real compiled graph against a stored
snapshot, so a topology change fails the build rather than the demo.

---

### "There's a bug in that area I'd like to hear about."

The one-character one, because the lesson is bigger than the fix.

There's a startup check that warns when a roster declares a specialist the graph has no handler
node for. It did `declared = roster.roles` — but `roles` is a **method**, not an attribute.
Iterating a bound method raises `TypeError`. And the whole block is wrapped in
`except Exception`, deliberately, because a roster is host data and a bad entry must not stop
the agent serving.

So the exception was swallowed, and **the warning could never fire, for any roster, ever —
which is exactly the silence it exists to break.**

The fix is `roster.roles()`. The lesson is that **a broad `except` around diagnostic code can
silently disable the diagnostic.** If a warning has never fired in production, that's data:
either the condition never occurs, or the check is broken, and you can't tell which without
testing the check itself.

---

### "How is your supervisor router implemented?"

**Deterministic-first.** The common cases — a normal question, or a plainly self-referential
"what do you know about me" — are decided by keyword phrase matching with **no model call at
all**. Only a genuine tie between two named specialists escalates to a cheap model.

Three payoffs: the common path is free, the money-shot trace stays clean, and the whole router
is offline-testable.

Scoring is `(distinct hits, total matched characters)` — hit count primary, specificity as the
tiebreak — so a specialist matching one long precise phrase isn't automatically beaten by one
matching two generic words.

**Two bugs worth naming.** Matching was a bare substring test, so "memory" matched "memorandum"
and "bill" matched "billboard" — a specialist could win on a word it has nothing to do with.
Now it's word-boundary aware, alphanumeric-aware rather than `\b`-only so multi-word and
punctuated hints still match.

And the tiebreak scanned the roster in declaration order for a role id appearing anywhere in the
reply. So `"not qa — use memory"` returned **qa** — the *rejected* role won because it was
declared first. Now an exact reply wins, otherwise the reply must mention exactly one role on
word boundaries, and a reply naming several is logged as inconclusive. A reply naming several
roles is a non-answer, not a vote for whichever the roster happens to list first.

---

### "How would you test all this?"

Four levels.

**Unit-test the pure functions.** The three conditional-edge routers are pure functions of
state — testable with a dict, no graph. The router's deterministic classifier, the risk
ordering, the scoring maths.

**Drive the whole graph offline with fakes.** Every capability is an injected callable, so a
test can run a complete turn — guardrails, retrieval, planning, gating, tools, reflection — with
no API key, no network, no database. The graph is *identical* to production, which is what makes
those tests worth anything.

**Test the concurrency explicitly.** A second decision for the same approval must be a no-op. A
disconnected client's gate must be disowned, not treated as a live wake-up. A failed resume must
release the row back to `PENDING`, not leave it in `RESUMING`. Two nodes returning token deltas
in one superstep must sum. TTL eviction must not pull a gate out from under an active waiter —
that's why the clocks are injectable.

**Assert the honesty claims.** The topology must match the real compiled graph. A node without a
label must raise. The run summary must report a rejected gate as rejected even when an earlier
round executed a tool. Those are the properties that quietly become false, so they need tests
that fail loudly.

---

### "What's the hardest part of building this?"

Two things, and they're related.

**Every interesting bug is invisible.** The approval finalised for a tool that never ran, the
warning that could never fire, the rewriter that could never receive history, the token counter
that loses a branch's spend — none of them throw. They produce working software with
correct-looking output and a clean audit trail. The only defence is being explicit about what
each mechanism actually guarantees, and testing the *failure directions* rather than the happy
path.

**Knowing which layer owns which guarantee.** LangGraph gives you durability. The database gives
you uniqueness. The acknowledged hand-off gives you the live/parked exclusion. The counter in
`plan` gives you termination. Every one of those is a different mechanism, and every bug in this
module came from expecting one of them to provide a guarantee it never offered.
