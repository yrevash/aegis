# The agent

The part of Aegis that decides what to do and then does it.

---

## 1. What it is

A chatbot answers. An agent **acts**.

You ask "refund order 4821". The agent works out that it needs to look up the order,
check the refund policy, and then issue the refund. It calls real functions to do each
step. If something fails, it tries again differently.

The difference that matters is cost of error. A chatbot that gets it wrong writes a bad
sentence. An agent that gets it wrong sends $4,200 to the wrong customer.

So the agent module is really about **control**: letting the model decide what to do,
while keeping the decision to actually do it in ordinary code we control.

That split is the whole idea:

```
model says "call issue_refund"  →  our code decides  →  refund actually happens
```

The model never runs anything. It writes a request — a bit of JSON naming a function and
its arguments. Our code reads that request and decides whether to honour it. Every safety
control in Aegis lives in that gap.

---

## 2. How it works in Aegis

The agent is a **graph**, not a loop. Each step is a node. Each node reads the shared
state and returns the parts it changed.

We use LangGraph for this. The reason it is a graph is that a graph can be **saved after
every step** — which is what lets a run pause for a human and pick up later, even on a
different server.

### The flow

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

### The nodes

| Node | What it does |
|---|---|
| `guard_input` | Checks the question is allowed. If blocked, the run ends here. |
| `route` | Picks which specialist handles this. Usually decided by keywords, no model call. |
| `recall_memory` | Pulls in what we know about this user. |
| `retrieve` | Finds supporting documents. |
| `ml_predict` | Adds a prediction as evidence. Optional — a failure here is ignored. |
| `plan` | Asks the model what to do. May propose tool calls. |
| `gate` | Decides whether a human must approve. |
| `approval` | Pauses the run and waits for a person. |
| `act` | Runs the tools. |
| `reflect` | Judges whether it worked. Can loop back to `plan`. |
| `generate` | Writes the answer. |
| `guard_output` | Checks the answer before anyone sees it. |
| `stream` | Sends the answer to the browser in chunks. |
| `persist_memory` | Saves what was learned. |

### Three things worth understanding

**What stops for a human is decided by the tool, not the model.** Every tool has a risk
level: reading a record is `LOW`, issuing a refund is `HIGH`. If a proposed action is
`HIGH`, the run stops for approval. We do *not* use the model's confidence, because a
model's confidence is unreliable — and it is most confident exactly when it is most
wrong. A tool we don't recognise counts as `HIGH`, so a made-up tool name can't slip past.

**Pausing is durable.** When the run stops for approval, the whole state is written to
Postgres. The browser can close, the server can restart, and the run still resumes. When
someone approves, the database does one guarded update that only the first caller wins.
That is what stops a double-click from issuing two refunds — the database decides, not the
graph.

**The repair loop always ends.** If a tool fails, `reflect` can send the run back to
`plan` to try again. The counter that limits this is increased in `plan` and capped in
config, so the model can never give itself more attempts.

---

## 3. How you use it in code

```python
from aegis.agent import run_agent, AgentDeps, AgentConfig

async for event in run_agent(
    "refund order 4821",
    deps=deps,           # required — the capabilities the graph calls
    run_id=run_id,
    session_id=session_id,
):
    ...  # each event is a plain dict, ready to send to the browser
```

`run_agent` is an async generator. It yields events as the run progresses — node started,
node finished, reasoning, tool call, approval needed, answer chunks. The API layer streams
them straight to the console.

### Giving it capabilities

The graph has no idea what a refund is, or where your database lives. Everything it needs
is passed in as `AgentDeps`:

| Field | What you pass |
|---|---|
| `complete` | A function that calls a model |
| `retrieve` | A function that finds documents |
| `check_input` / `check_output` | The guardrails |
| `run_tool` / `tool_risk` / `tool_definitions_for` | Your tools and their risk levels |
| `render_system_prompt` | Your domain's prompt |
| `memory` | Optional. Leave it out and the memory nodes do nothing. |

`deps` is required — `run_agent` raises if you leave it out, rather than running with no
capabilities and quietly doing nothing useful.

This is also how testing works: pass fake functions and the whole agent runs offline, with
no API key and no database.

### Settings you'll actually change

| Setting | Default | What it does |
|---|---|---|
| `gate_min_risk` | `HIGH` | Which risk level stops for a human |
| `max_plan_iterations` | `2` | How many repair attempts are allowed |
| `self_repair_enabled` | `True` | Turn the repair loop off entirely |
| `approval_park_timeout` | `None` | How long to wait for approval before parking the run |
| `run_ml` | `True` | Whether to attach ML evidence |

```python
config = AgentConfig(gate_min_risk=RiskLevel.MEDIUM, max_plan_iterations=3)
deps = AgentDeps(..., config=config)
```

### Resuming a paused run

```python
from aegis.agent import resume_parked_run
```

The API calls this after a human approves. It loads the saved state by run id and drives
the rest of the run — no browser connection needed.

---

## 4. Why it helps us

**Nothing dangerous happens without a person.** High-risk actions stop and wait. That is a
property of the code, not a prompt we hope the model obeys.

**A paused run survives anything.** Deploys, crashes, closed browsers. The state is in the
database, so any server can finish the job.

**The whole run is visible.** Every step emits an event, so the console shows what the
agent did, how long each step took, and what it cost — instead of a spinner.

**You can point it at a new problem without touching it.** The graph is mechanism only. A
new domain means new tools and prompts passed in as `deps`; the agent itself doesn't
change.

**It is testable.** Because everything is injected, the entire agent runs in unit tests
with no network.

**Next:** [`40-diagrams.md`](40-diagrams.md)
