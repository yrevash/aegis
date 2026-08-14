# The agent — the diagrams

Every path through the graph, drawn. The two worth being able to reproduce from memory are
**the full graph** and **the interrupt/resume sequence** — those two carry a forty-minute
conversation between them.

---

## 1. The full graph

```mermaid
flowchart TB
    START([START]) --> GI["<b>guard_input</b><br/>Input guardrail"]
    GI -->|blocked| END1([END])
    GI -->|clean| RT["<b>route</b><br/>Supervisor"]

    RT -->|"role = memory"| AM["<b>answer_memory</b><br/>memory specialist"]
    RT -->|"role = qa (default)"| RM["<b>recall_memory</b><br/><i>plain node — silent when inactive</i>"]

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
    ST --> PM["<b>persist_memory</b><br/><i>plain node — emits nothing</i>"]
    PM --> END2([END])
```

**Four things to point at.**

A blocked input goes **straight to END** — the router never runs and nothing downstream
executes.

The gate sits **between plan and act**, which is the only place it can: the plan is inspectable
there and nothing has happened yet.

The reflect→plan edge is the self-repair loop, and it terminates because the counter is
incremented in `plan` and capped in config.

`recall_memory` and `persist_memory` are wired **plain**, not through the timing wrapper, so a
run with no memory emits a byte-identical trace to one without the nodes at all.

---

## 2. Where the risk gate fires — and where ML does not

```mermaid
flowchart TB
    P["plan proposes tool_calls"] --> R["risk_of = {call: deps.tool_risk(name)}"]
    R --> U{"tool registered?"}
    U -->|no| H["<b>HIGH</b><br/><i>fail safe — a hallucinated<br/>name cannot slip under<br/>the ceiling</i>"]
    U -->|yes| D["the declared tier"]

    H --> C{"any risk ≥ gate_min_risk?"}
    D --> C
    C -->|yes| G["gated = true<br/>reason = 'Proposed action is HIGH-risk.'"]
    C -->|no| NG["gated = false"]

    G --> AP([approval node])
    NG --> ACT([act node])

    MLX["ml_response, if any"] -.->|"emitted as an INFORMATIONAL<br/>ml_explanation event<br/><b>no gating semantics</b>"| EV[["the wire"]]
```

**The dotted edge is the design decision.** ML is evidence injected into the plan and the
answer. It never routes. A model 99% confident about a $4,200 refund still stops, because
refunds are HIGH-risk.

---

## 3. The interrupt / resume sequence — the one to memorise

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
    G->>G: approval node RE-EXECUTES;<br/>interrupt() returns the payload
    G->>G: act → reflect → generate → guard_output → stream
    G-->>O: events
    O-->>C: events → run_finished
```

**Step 12 is where exactly-once lives.** The guarded UPDATE, not the graph.

**Step 20 is the detail people miss.** The approval node re-executes from its beginning on
resume, which is why nothing may be emitted before the `interrupt` call.

---

## 4. The acknowledged hand-off — the fixed bug, drawn

```mermaid
flowchart TB
    D["decision arrives"] --> CAS{"UPDATE ... WHERE status=PENDING"}
    CAS -->|"rowcount = 0"| NOOP([no-op<br/><i>replayed or racing decision</i>])
    CAS -->|"rowcount = 1"| NL["notify_live(approval_id, decision)"]

    NL --> EX{"gate registered<br/>and unresolved?"}
    EX -->|no| PARK1["live_woken = false"]
    EX -->|yes| SET["set the future's result"]
    SET --> ACK{"waiter signals<br/>'consumed' within 1s?"}

    ACK -->|yes| LIVE(["<b>live path</b><br/>the open socket executes<br/>row → APPROVED"])
    ACK -->|"no — recheck consumed"| RC{"consumed.is_set()?"}
    RC -->|yes| LIVE
    RC -->|no| DIS["<b>disown</b><br/>gate.abandoned = true<br/>forget the gate"]

    DIS --> PARK1
    PARK1 --> RES(["<b>durable path</b><br/>rehydrate by thread_id<br/>and drive headless"])

    LATE["a waiter that wakes later"] -.->|"sees abandoned"| GHO["raises GateHandedOffError<br/>(subclasses TimeoutError)<br/>→ the run PARKS, does not execute"]

    OLD["❌ the old test:<br/>'did a future exist?'"] -.->|"an orphan future<br/>reads as a live wake-up"| BAD["row finalised APPROVED<br/><b>tool never ran</b>"]
```

**The two dotted edges are the bug and its closure.** A registered future proves a gate exists,
not that anyone will consume it. `GateHandedOffError` subclasses `TimeoutError` precisely so
the orchestrator's existing park path handles it — exactly one side ever proceeds.

---

## 5. Two resolution paths, converging

```mermaid
flowchart TB
    DEC["POST /approval"] --> RES["decide_approval"]
    RES --> CAS["durable CAS<br/>PENDING → RESUMING / REJECTED"]
    CAS --> NL["notify_live"]

    NL -->|"acknowledged"| A["<b>live</b>: the open /query socket<br/>executes under the SAME lock"]
    NL -->|"not acknowledged"| B{"in-process handle<br/>for this run_id?"}

    B -->|yes| B1["resume from the retained<br/>ParkedRun handle"]
    B -->|"no — fresh worker,<br/>or TTL-evicted"| B2["rebuild the graph on the<br/>SHARED checkpointer,<br/>resume by thread_id"]

    B1 --> DRIVE
    B2 --> DRIVE["drive headless to completion"]
    DRIVE -->|success| FIN["finalize → APPROVED<br/>pop the handle"]
    DRIVE -->|"ResumeFailedError"| REL["<b>release</b> RESUMING → PENDING<br/><i>handle stays parked;<br/>checkpoint stays reachable</i>"]

    A --> FIN2["finalize → APPROVED"]
```

**The `REL` branch is the second fixed bug.** Without it, a failed resume left the row in
`RESUMING` — matched by neither a later decision (requires `PENDING`) nor the SLA sweeper (also
`PENDING`) — stranded forever.

---

## 6. The approval state machine

```mermaid
stateDiagram-v2
    [*] --> PENDING: gate fires, row inserted
    PENDING --> RESUMING: approve (CAS wins)
    PENDING --> REJECTED: reject (CAS wins)
    PENDING --> EXPIRED: SLA sweeper, past deadline
    PENDING --> REJECTED: SLA auto-reject (HIGH risk)
    RESUMING --> APPROVED: resume completed
    RESUMING --> PENDING: resume FAILED (compensating release)
    REJECTED --> [*]
    EXPIRED --> [*]
    APPROVED --> [*]
```

**`RESUMING` is the hazard.** It is matched by neither the decision path nor the sweeper, so
every route out of it must be explicit — completion forward, failure back. Every intermediate
state in a distributed transition needs a compensating action.

---

## 7. Reducers under a fan-out

```mermaid
flowchart TB
    subgraph SS["one superstep, two nodes"]
        N1["node A<br/>spends 120 tokens"]
        N2["node B<br/>spends 340 tokens"]
    end

    N1 --> M{"how are the two<br/>updates merged?"}
    N2 --> M

    M -->|"no reducer"| LWW["<b>last write wins</b><br/>total = 340<br/><i>120 silently lost</i>"]
    M -->|"no reducer, reduced key"| ERR["<b>InvalidUpdateError</b><br/>'can receive only one<br/>value per step'"]
    M -->|"operator.add + DELTAS"| OK["<b>total = 460</b> ✓"]
    M -->|"operator.add + read-modify-write"| DBL["<b>total = 920</b><br/><i>both returned running totals;<br/>the reducer added them</i>"]
```

**Both halves are required.** A reducer without delta-returning nodes is *worse* than no
reducer — it double-counts. `_accrue` returns only the current call's contribution, which is
what makes the reducer correct.

And three keys deliberately have **no** reducer: `messages` (a per-round scratch buffer),
`conversation` (a snapshot of external state), and `tool_results` (replaced wholesale, and read
before the overwrite). "No reducer" is a decision there, not a default.

---

## 8. The self-repair loop, and why it terminates

```mermaid
flowchart TB
    PL["<b>plan</b><br/>plan_iterations += 1<br/><i>reducer-summed</i>"] --> GT["gate"]
    GT --> ACT["act → tool_results"]
    ACT --> RF{"<b>reflect</b>"}

    RF --> D{"all results ok?"}
    D -->|yes| G1(["generate<br/>'goal met'"])
    D -->|no| SW{"self_repair_enabled?"}
    SW -->|no| G2(["generate<br/>'self-repair disabled'"])
    SW -->|yes| B{"iteration < max_plan_iterations?"}
    B -->|no| G3(["generate<br/>'budget exhausted (2/2)'"])
    B -->|yes| RETRY["reflect_retry = true"]
    RETRY --> PL

    PL -.->|"the counter is incremented HERE,<br/>so reflect can only REDUCE<br/>the remaining budget"| B
```

**Termination is structural.** The reflecting model chooses *whether* to retry; it cannot
extend the budget. And the four terminal reasons are distinct strings, so the `reflection`
event says which of the four ways it ended.

On a retry, the previous round's failed outcomes are fed back into the planning prompt —
Reflexion's verbal self-reflection, expressed as a graph edge.

---

## 9. The supervisor router

```mermaid
flowchart TB
    Q["guardrail-cleaned query"] --> DET["<b>deterministic classify</b><br/>word-boundary phrase match<br/>score = (hits, matched chars)"]
    DET --> R{"outcome?"}

    R -->|"one winner"| W(["that role<br/><b>used_llm = false</b>"])
    R -->|"nothing matched"| DF(["roster default (qa)"])
    R -->|"two specialists tied"| TB{"cheap model available?"}

    TB -->|no| DF2(["default<br/>'no tiebreak model'"])
    TB -->|yes| LLM["cheap-LLM tiebreak<br/>closed menu, one token back"]

    LLM --> M{"reply matching"}
    M -->|"exact role id"| PICK(["that role"])
    M -->|"exactly one role<br/>mentioned on boundaries"| PICK
    M -->|"names several roles"| INC(["inconclusive → default<br/><i>'not qa — use memory'<br/>must NOT return qa</i>"])
    M -->|unrecognised| INC

    PICK --> DISP
    W --> DISP
    DF --> DISP
    DF2 --> DISP
    INC --> DISP{"SPECIALIST_NODES[role]"}
    DISP -->|found| NODE(["that handler node"])
    DISP -->|"missing"| WARN(["qa pipeline<br/><b>+ a loud warning</b>"])
```

**Two fixed bugs are visible here.** Word-boundary matching (so "memory" no longer matches
"memorandum"), and strict tiebreak parsing (so a reply rejecting a role cannot elect it).

---

## 10. Where the retry sits, and why it matters

```mermaid
flowchart TB
    subgraph WRONG["❌ retry around the WRAPPER"]
        W1["emit node_started"] --> W2["body"]
        W2 -->|"transient failure"| W1
        W2 -->|success| W3["emit node_finished"]
        W3 --> WOUT["<b>2 starts, 1 finish</b><br/>run_summary sees a phantom node<br/>with duration_ms: null"]
    end

    subgraph RIGHT["✓ retry INSIDE the wrapper"]
        R1["emit node_started"] --> R2["body"]
        R2 -->|"transient failure"| R2
        R2 -->|success| R3["emit node_finished"]
        R3 --> ROUT["<b>1 start, 1 finish</b><br/>duration spans every attempt<br/>= the honest wall clock"]
    end
```

And the exclusions:

```mermaid
flowchart LR
    RET["_MODEL_RETRY<br/>max_attempts=3"] --> Y["route · answer_memory · retrieve<br/>plan · generate · guard_output"]
    RET -.->|"NEVER"| N1["<b>act</b> — executes real actions.<br/>Exactly-once is the DB lock,<br/>not the graph. A retry could<br/>issue a refund twice."]
    RET -.->|"NEVER"| N2["<b>approval</b> — re-executes on<br/>resume by design; a retry<br/>would re-interrupt."]
    RET -.->|"NEVER"| N3["<b>memory nodes</b> — already<br/>best-effort with their own<br/>degrade path."]
```

---

## 11. The event stream

```mermaid
flowchart LR
    N["node body"] -->|get_stream_writer| CUS["custom stream"]
    CUS --> OR["run_agent"]
    G["graph"] -->|updates mode| OR
    OR --> IS{"__interrupt__?"}
    IS -->|yes| GATE["gate rendezvous"]
    IS -->|no| ST["stamp(payload, run_id, seq)"]
    ST --> SSE([SSE client])

    OR -.->|"collected side-channel"| GE["guardrail_events → trace-eval"]
    OR -.->|"collected side-channel"| NL["node latencies → latency window"]
```

Two stream modes, two jobs: `custom` carries the wire events nodes deliberately emit;
`updates` is watched **solely** to detect an interrupt. The final state is read once via
`get_state`, not streamed — the stream is a presentation concern.

Every event is stamped with `run_id` and a monotonic `seq` and validated against the host's
locked wire schema by the injected `stamp` seam, so the graph never imports an API schema.

---

## 12. The injected seams

```mermaid
flowchart TB
    subgraph CORE["aegis.agent — pure"]
        RA["run_agent"]
        BA["build_agent"]
        ST2["AgentState"]
    end

    subgraph SEAMS["five injected seams"]
        S1["checkpointer"]
        S2["stamp — event validator"]
        S3["enqueue_approval"]
        S4["on_terminal"]
        S5["default_tier"]
    end

    subgraph DEPS["AgentDeps — the capability contract"]
        D1["complete · retrieve"]
        D2["check_input · check_output"]
        D3["tool_definitions_for · run_tool · tool_risk"]
        D4["predict_explain · features_for · describe_prediction"]
        D5["memory · answer_cache · embed_query"]
        D6["agent_roster · current_tenant_id · record_audit"]
    end

    SEAMS --> RA
    DEPS --> BA

    HOST[["backend/src/app/agent<br/>the composition root"]] --> SEAMS
    HOST --> DEPS
    TEST[["tests — fakes"]] --> DEPS
```

Inject fakes and the entire vertical slice runs offline: no API key, no network, no database.
Inject the real bindings and you get the durable platform. **The graph is identical in both
cases** — which is what makes the tests meaningful.

---

## 13. Topology, served rather than drawn

```mermaid
flowchart LR
    IG["_inert_deps()<br/><i>every callable is _unreachable</i>"] --> BG["build_agent"]
    BG --> GG["graph.get_graph()"]
    GG --> TP["graph_topology()"]
    TP --> J[["JSON: nodes + edges<br/>entry/terminal flags<br/>conditional edge markers"]]
    J --> UI["the console draws THIS"]

    LBL["NODE_LABELS[nid]"] -.->|"KeyError if missing —<br/>a new node cannot ship<br/>without a label"| TP
    SNAP["CI snapshot test"] -.->|"fails if the real graph drifts"| TP
```

The console used to hardcode a 9-node DAG that drew the gate branching off **ML** —
contradicting the code, where the gate fires on tool risk and ML never routes — and could not
light 7 real nodes. A drifted architecture diagram is worse than none, because it is
confidently wrong.

**Next:** [`50-interview.md`](50-interview.md) — the questions you'll be asked.
