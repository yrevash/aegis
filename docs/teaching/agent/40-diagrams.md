# The agent — the diagrams

Five diagrams. The two worth reproducing from memory are **the full graph** and **the
interrupt/resume sequence** — between them they carry a forty-minute conversation.

Everything else about this module is explained in [`10-guide.md`](10-guide.md); a picture is
only here when it shows something prose cannot.

---

## 1. The full graph

*Look at where the gate sits, and at the one edge that loops back.*

```mermaid
flowchart TB
    START([START]) --> GI["<b>guard_input</b><br/>Input guardrail"]
    GI -->|blocked| END1([END])
    GI -->|clean| RT["<b>route</b><br/>Supervisor"]

    RT -->|"role = memory"| AM["<b>answer_memory</b><br/>memory specialist"]
    RT -->|"role = qa (default)"| RM["<b>recall_memory</b>"]

    RM --> RE["<b>retrieve</b><br/>agentic retrieval"]
    RE --> ML["<b>ml_predict</b><br/>best-effort evidence"]
    ML --> PL["<b>plan</b><br/>reason and propose tools"]

    PL -->|"no tool_calls"| GEN
    PL -->|"tool_calls"| GT["<b>gate</b><br/>risk decision"]

    GT -->|"risk ≥ gate_min_risk"| AP["<b>approval</b><br/>interrupt"]
    GT -->|"below the ceiling"| ACT

    AP -->|approved| ACT["<b>act</b><br/>execute tools"]
    AP -->|rejected| GEN

    ACT --> RF["<b>reflect</b><br/>self-repair judgement"]
    RF -->|"failed + budget left"| PL
    RF -->|"goal met / budget spent"| GEN["<b>generate</b><br/>compose the answer"]

    AM --> GO
    GEN --> GO["<b>guard_output</b><br/>Output guardrail"]
    GO --> ST["<b>stream</b><br/>chunk the guarded answer"]
    ST --> PM["<b>persist_memory</b>"]
    PM --> END2([END])
```

The gate sits **between `plan` and `act`** — the only place it can, because the plan is
inspectable there and nothing has happened yet.

The `reflect → plan` edge is the self-repair loop. It terminates because the counter is
incremented in `plan`, not in `reflect`, so no model output can extend the budget.

A blocked input goes **straight to END**: the router never runs.

---

## 2. What the gate actually decides

*Look at the `no` branch — that is the one an attacker would use.*

```mermaid
flowchart TB
    P["plan proposes tool_calls"] --> R["risk_of = tool_risk(name) per call"]
    R --> U{"tool registered?"}
    U -->|no| H["<b>HIGH</b><br/><i>a hallucinated name must not<br/>slip under the ceiling</i>"]
    U -->|yes| D["the declared tier"]

    H --> C{"any risk ≥ gate_min_risk?"}
    D --> C
    C -->|yes| G["gated = true"]
    C -->|no| NG["gated = false"]

    G --> AP([approval node])
    NG --> ACT([act node])

    MLX["ml prediction, if any"] -.->|"informational event only —<br/><b>no gating semantics</b>"| EV[["the wire"]]
```

The dotted edge is the design decision: **ML informs, risk gates.** A model 99% confident about
a $4,200 refund still stops, because refunds are HIGH-risk.

---

## 3. The interrupt / resume sequence

*Look at step 12 and step 20. Everything else is plumbing.*

```mermaid
sequenceDiagram
    autonumber
    participant C as SSE client
    participant O as run_agent
    participant G as LangGraph
    participant CP as checkpointer
    participant DB as approvals row
    participant R as ApprovalRegistry
    participant H as human via POST /approval

    O->>G: astream(...)
    G->>G: gate → gated=true
    G->>CP: checkpoint state
    G-->>O: __interrupt__ chunk
    O->>R: register(approval_id)
    Note over O,R: registered BEFORE emitting,<br/>so a fast decision cannot race past
    O->>DB: INSERT status=PENDING
    O->>O: parked_runs.register(run_id, graph, config)
    O-->>C: node_started · approval_queued · approval_required
    O->>R: await wait(approval_id)

    H->>DB: UPDATE ... WHERE status=PENDING
    Note over DB: the compare-and-swap:<br/>rowcount==1 ⇒ this caller won
    H->>R: notify_live(approval_id, decision)
    R->>R: set future result
    R->>R: await consumed, 1s
    R-->>O: wakes the waiter
    O->>R: mark consumed
    R-->>H: True (a live run took it)
    H->>DB: finalize APPROVED
    O->>G: Command(resume={approved, approver})
    G->>G: approval node RE-EXECUTES,<br/>and the interrupt call returns the payload
    G->>G: act → reflect → generate → guard_output → stream
    G-->>O: events
    O-->>C: events → run_finished
```

**Step 12 is where exactly-once lives** — the guarded UPDATE, not the graph.

**Step 20 is the detail people miss.** The approval node re-executes from its first line, which
is why nothing may be emitted before the `interrupt` call.

---

## 4. Two resolution paths, converging

*Look at the `release` branch — without it a run is stranded forever.*

```mermaid
flowchart TB
    DEC["POST /approval"] --> RES["decide_approval"]
    RES --> CAS["durable CAS<br/>PENDING → RESUMING / REJECTED"]
    CAS --> NL["notify_live"]

    NL -->|"acknowledged"| A["<b>live</b>: the open socket<br/>executes under the SAME lock"]
    NL -->|"not acknowledged"| B{"in-process handle<br/>for this run_id?"}

    B -->|yes| B1["resume from the retained handle"]
    B -->|"no — fresh worker,<br/>or TTL-evicted"| B2["rebuild on the SHARED<br/>checkpointer, resume by thread_id"]

    B1 --> DRIVE
    B2 --> DRIVE["drive headless to completion"]
    DRIVE -->|success| FIN["finalize → APPROVED"]
    DRIVE -->|"ResumeFailedError"| REL["<b>release</b> RESUMING → PENDING"]

    A --> FIN
```

Only one of the two paths can reach `act`, because only one of them won the compare-and-swap.

The `B2` branch is why eviction is safe: a fresh worker rebuilds from the durable checkpoint and
never needed the in-process handle at all.

---

## 5. The approval state machine

*Look at `RESUMING` — it is the only state with no sweeper watching it.*

```mermaid
stateDiagram-v2
    [*] --> PENDING: gate fires, row inserted
    PENDING --> RESUMING: approve (CAS wins)
    PENDING --> REJECTED: reject (CAS wins)
    PENDING --> EXPIRED: SLA sweeper, past deadline
    RESUMING --> APPROVED: resume completed
    RESUMING --> PENDING: resume FAILED (compensating release)
    REJECTED --> [*]
    EXPIRED --> [*]
    APPROVED --> [*]
```

`RESUMING` is matched by neither the decision path nor the SLA sweeper, so **every route out of
it has to be explicit** — forward on success, back on failure.

Every intermediate state in a distributed transition needs a compensating action.

**Next:** [`50-interview.md`](50-interview.md) — the questions you'll be asked.
