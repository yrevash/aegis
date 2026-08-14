# The agent — the concept, from zero

No code in this file. What makes something an agent, why that immediately creates problems a
chatbot does not have, and why the answer is a state machine rather than a loop.

---

## Chatbot vs agent

A chatbot maps text to text. You send a message, it sends one back. Nothing happens in the
world.

An **agent** decides and acts:

1. **Plans** — decomposes a goal into steps.
2. **Uses tools** — calls real functions: query a database, send an email, issue a refund.
3. **Observes** results and adapts.
4. **Loops** until the goal is met or a budget is exhausted.

The difference is not sophistication. It is **consequence**. A chatbot that is wrong produces
a bad sentence. An agent that is wrong produces a refund to the wrong customer.

---

## Tool calling, and the gap that matters

The mechanism is simple and worth being precise about, because most safety design lives in
one specific gap.

You describe your functions to the model as JSON schemas: name, description, parameter types.
The model, instead of producing prose, produces a **structured request**: "call
`issue_refund` with `{customer_id: 4821, amount: 4200}`".

**The model does not execute anything.** It emits a request. *Your* code decides whether to
honour it.

That gap — between the model asking and your code doing — is where every safety control in an
agent belongs. It is the only place you have complete authority, because it is the only place
that is ordinary deterministic code.

Any design that closes that gap ("let the model call functions directly") has given away the
one control point it had.

---

## Why a loop is not enough

The obvious implementation is a `while` loop: ask the model, execute any tool it requested,
feed the result back, repeat until it stops asking.

That works, and it fails at four specific things.

**You cannot see inside it.** From outside, a run is one opaque call that takes 40 seconds.
Which step was slow? Which retrieval fed which plan? What did the model consider? A loop
gives you a transcript, not a structure.

**You cannot pause it.** If a refund needs human approval, a `while` loop can `await` a
future — and that future lives in the process's memory. Deploy, crash, or scale to a second
worker and the pause is gone along with everything else about the run.

**You cannot branch cleanly.** "If the plan proposes a risky action, go to approval; else go
straight to execution" is an `if` in a loop and an *edge* in a graph. Once you have five such
conditions, the loop is a tangle and the graph is a diagram.

**You cannot resume it anywhere else.** A loop's state is local variables. There is nothing to
hand to another machine.

---

## The state graph

Model the agent's control flow explicitly:

- **Nodes** are steps. Each takes the current state and returns an update to it.
- **Edges** are transitions. Some are unconditional; some are **conditional** — a function
  reads the state and returns which node to go to next.
- **State** is one typed record threaded through everything.

Now the flow is data. You can draw it, serve it to a UI, test each node in isolation, and —
critically — **save the state after every step**.

That last property is called **checkpointing**, and it is what turns "pause the agent" from a
hard problem into a database write.

---

## Supersteps, and why they force you to think about merges

A graph executes in **supersteps**: at each step, every node whose incoming edges are
satisfied runs, then their state updates are merged, then the next superstep begins. This is
the Pregel model, from graph processing.

Most of the time only one node runs per superstep and the merge is trivial. But the moment
you add a **fan-out** — two branches running in parallel — two nodes return updates for the
same step and something must decide how to combine them.

The default is **last write wins**: one update silently overwrites the other. For a key like
"the current answer" that is correct — the node that produced it owns it. For a key like
"total tokens spent" it is a **silent data loss bug**: two model calls happened, one is
counted.

The mechanism that fixes this is a **reducer** — a function attached to a state key saying
how to combine two updates. Attach `add` to the token counters and two parallel nodes each
returning their own delta produce the correct total.

**The subtlety that catches people:** a reducer only helps if nodes return **deltas**. If a
node reads the running total and returns `total + mine`, adding a reducer makes it *worse* —
now you double-count. Reducers and read-modify-write are mutually exclusive designs, and
mixing them is a bug that only appears under concurrency.

And reducers are not universally right. Some keys *must* be last-write-wins: a per-round
scratch buffer that gets rebuilt each time, or a snapshot of external state. Accumulating
those duplicates content on every write. The correct rule is that **every key needs a decided
answer**, and "no reducer" must be a decision rather than a default.

---

## Checkpointing and interrupts

**Checkpointing**: after each superstep, the whole state is written to a store, keyed by a
thread id. The run becomes resumable from any point.

**Interrupt**: a node can call a function that *suspends the graph* and returns a value to the
caller. The state is checkpointed at that moment. Later, someone resumes with a value, and the
node **re-executes** — this time the interrupt call returns the supplied value instead of
suspending.

Together they give you a **durable pause**. Not "an await that dies with the process" — a row
in a database plus a checkpoint that any worker can pick up.

Two consequences worth internalising:

**A node containing an interrupt runs twice.** So it must not emit events or perform side
effects before the interrupt, or they happen twice. Anything that must happen exactly once
goes outside the node.

**Resume is by thread id, not by object.** If the checkpoint store is shared and durable, a
completely different process can rebuild the graph and continue the run. If the store is
in-memory, the run only resumes inside the process that started it.

---

## The human gate, and what should trigger it

Some actions must not be autonomous. The design question is *what decides*.

### The wrong answer: model confidence

"If the model is less than 90% sure, ask a human." This is intuitive and it is wrong, for a
reason worth stating clearly:

**A model's stated confidence is not calibrated.** A model saying 90% is not right 90% of the
time. And its most dangerous failure mode is being *confidently wrong* — which is exactly when
confidence-based gating does not fire.

So a confidence gate provides its protection precisely when you least need it and withdraws it
precisely when you most do.

### The right answer: the risk tier of the action

Every tool carries a declared risk level. Reading a record is LOW. Updating a status is
MEDIUM. Issuing a refund is HIGH. The gate fires when a **proposed action's risk tier** is at
or above a threshold.

This is a property of the **action**, not of the model's mood. A refund is high-risk whether
or not the model feels sure about it. And a model that is 99% confident about a $4,200 refund
still stops for a human, because refunds are high-risk.

It also composes with a machine-learning signal in a clean way: ML **informs the plan** — a
calibrated prediction becomes supporting evidence the answer can cite — and ML **never gates**.
A low-confidence or failed prediction is simply omitted; it never defers, abstains or
terminates a run.

### Unknown tools fail safe

One corollary. If the model requests a tool that is not in the registry — a hallucinated name,
or one removed in a deploy — what risk is it?

**HIGH.** An unregistered name must be treated as maximally risky so it cannot slip under the
autonomy ceiling. The alternative — defaulting to LOW — means a hallucinated tool name is the
easiest way to bypass your gate.

---

## Exactly-once, and where it actually comes from

Here is the question that separates people who have shipped this from people who have read
about it.

A run pauses for approval. A human clicks approve. **What guarantees the refund is issued
exactly once?**

Not the graph. The graph will happily run the tool every time you resume it. A resume is just
"continue from this checkpoint" — and nothing about a checkpoint prevents a second resume.

Consider the ways it goes wrong:

- The human double-clicks. Two decisions arrive.
- The approval request is retried by a proxy.
- The user's browser is still connected **and** a background resumer picks it up.
- Two workers both see the pending row.

**The guarantee has to come from a single atomic transition in a store that both paths share.**
A row moves from PENDING to RESUMING in one guarded update, and the update's `WHERE` clause
pins the old status. Exactly one caller's update affects a row. Everyone else finds it already
moved and becomes a no-op.

That is a database lock. The graph provides *durability*; the database provides
*exactly-once*. Conflating them is the most common mistake in this area.

---

## The live path and the parked path

There are two ways an approval can be resolved, and they have to converge:

**Live** — the user's streaming connection is still open. The decision arrives, the run wakes
up, and the tool executes inside that connection. Fast, and it makes for a good demo.

**Parked** — the connection closed, or the run timed out waiting, or the worker restarted.
The decision arrives at a *different* process, which must rebuild the graph, load the
checkpoint by thread id, and drive the run headless.

Both paths must run the tool **exactly once between them**. Which means the hand-off between
them needs to be unambiguous.

### The subtle failure: a future is not a consumer

The natural implementation of the live path is a map from approval id to a future. The
decision resolves the future; the waiting run wakes up.

But **a registered future proves a gate exists, not that anyone will consume it.**

Consider the ordering. The streaming run registers the future *before* it emits the approval
events — deliberately, so a very fast decision cannot race past the wait. Then it yields
several events, and only then does it await. If the client disconnects in that window, the
generator is closed and **nothing ever takes the outcome**.

Resolving the future returns "success". The system records the approval as APPROVED. And the
tool never ran.

The fix is an **acknowledged hand-off**: resolve, then wait briefly for the waiter to confirm
it *took* the outcome. No acknowledgement means the gate is **disowned** to the durable
resumer, and a waiter that shows up later is told the gate is no longer its to execute. Exactly
one side ever proceeds.

---

## Self-repair, and why it terminates

Agents get things wrong. A tool fails, a plan is insufficient, an assumption was bad. A
**reflection** step — from the Reflexion line of work — judges the outcome and can loop back
to re-plan with the failure fed back in.

Two hard requirements:

**It must terminate.** An LLM deciding whether to loop again will eventually loop forever. The
counter must be incremented by the *planning* step and checked against a hard cap, so that no
model output can extend the budget. That is a structural guarantee, not a heuristic one.

**The judgement must be domain-agnostic.** "Did this work?" cannot be hardcoded domain logic
in a platform that gets pointed at a new problem. The signal has to come from the tool
results themselves — a structured `ok` and a summary that every tool returns.

---

## Streaming, and one ordering that is a safety property

Users want to see the agent working, not a spinner. So the run emits a stream of events: node
started, node finished, reasoning text, tool call, tool result, approval required, answer
tokens.

Two things about that stream matter beyond UX.

**Sequence numbers and validation.** Events are stamped with a run id and a monotonic sequence
number so a client can order and deduplicate them, and validated against a locked wire schema
so a change to an internal payload cannot silently break every consumer.

**The answer is not streamed from the model.** This is a deliberate, defensible trade. The
answer is generated in full, passed through the output guardrail, and only then paced onto the
socket in chunks. The client still sees progressive rendering. But no model output reaches the
user before the rail has cleared it — because **you cannot unsay a leaked secret**. Streaming
raw tokens would make a block unenforceable after the fact.

Real token streaming would need a *streaming-aware* output rail — incremental scanning with
the ability to withhold — not merely a streaming API call.

---

## Retries, and the one node you must never retry

Model calls fail transiently: connection resets, timeouts, 5xx. Retrying them is obviously
correct.

Retrying **tool execution** is obviously wrong. A timeout on "issue refund" does not mean the
refund did not happen — it means you did not hear back. Retry and you may refund twice.
Exactly-once comes from the database lock, not from the graph, so the graph must not
independently re-run the action.

There is a second, subtler retry problem. If a retry re-invokes the *wrapper* that emits
"node started", then one logical node execution emits two start events and one finish — and
anything folding those events into a per-node record produces a phantom, permanently unpaired
node. The retry has to live **inside** the timing and emission wrapper, so one execution is
exactly one start/finish pair and the measured duration spans every attempt.

---

## Unbounded registries are a leak of whole runs

An agent that parks runs needs somewhere to keep the handle for resuming them. The obvious
implementation is a dictionary.

The problem is that several paths never remove an entry. An approval can expire, or be
auto-rejected by a background sweeper that only touches database rows. A run can fail after
parking. A client can vanish mid-gate.

And each entry holds a **compiled graph plus its checkpointer** — which is the entire state of
a run. An unbounded map does not leak a few kilobytes; it leaks whole runs.

TTL eviction is safe here for a specific reason: **the durable checkpoint remains.** A
decision arriving after eviction rehydrates by thread id exactly as a fresh worker would. The
in-memory handle is a fast path, not the source of truth — and once you can say that
sentence, evicting it is obviously fine.

---

## The composition seam

One more idea, because it explains the shape of the code.

The graph should not know about your API schema, your database, your config, or your domain.
Everything it needs — the model gateway, retrieval, guardrails, the tool registry, the audit
sink, the tenant scope — arrives as **injected callables**.

Two payoffs, and the second is the one that matters more:

**Testability.** Inject fakes and the entire vertical slice runs offline: no API key, no
network, no database. Every branch of the graph is exercisable in a unit test.

**Portability.** The graph is *mechanism*. Pointing the platform at a new problem means
rewriting the adapter that supplies domain meaning, not forking the orchestration.

---

## What you should now be able to explain

- What distinguishes an agent from a chatbot, and where the safety-critical gap is
- Why a `while` loop fails on four specific properties
- What supersteps are, why reducers exist, and why "no reducer" must be a decision
- Why reducers require deltas rather than read-modify-write
- What checkpointing and interrupt give you, and why an interrupted node runs twice
- Why gating on model confidence is wrong and gating on tool risk is right
- Why an unknown tool must be treated as HIGH risk
- Where exactly-once actually comes from, and why it is not the graph
- Why a registered future does not prove a consumer exists
- Why the self-repair loop terminates by construction
- Why the answer is not streamed from the model
- Why `act` must never be retried, and why a retry must live inside the emission wrapper
- Why an unbounded parked-run registry leaks entire runs, and why TTL eviction is safe

**Next:** [`10-theory.md`](10-theory.md) — LangGraph properly, and the research behind the loop.
