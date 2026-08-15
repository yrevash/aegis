# Foundations — the diagrams to know by heart

Two of these you should be able to draw on a whiteboard from memory: **the request
flow** and **the approval interrupt/resume**. Everything else in Aegis is detail
hanging off those.

---

## 1. The four layers

The shape of the whole system. Note the direction of every arrow: the core never
reaches upward, and only the core touches the stores.

```mermaid
flowchart TB
    B["<b>Browser</b><br/>four role portals"]
    L1["<b>1 · Console</b> — web/<br/>Next.js · React · TypeScript<br/>REST + SSE client"]
    L2["<b>2 · Composition root</b> — backend/src/app<br/>FastAPI · auth · RBAC · tenant scoping<br/>wires the core to the world"]
    L3["<b>3 · Importable core</b> — aegis/src/aegis<br/>15 modules · no domain logic · no web framework"]
    L4["<b>4 · Stores</b><br/>Postgres · embedded vectors · Neo4j · Redis · Phoenix"]
    AD["<b>Domain adapter</b> — app/adapter/<br/>schema · tools · prompts · ML target · corpus<br/><i>the only thing that changes per domain</i>"]

    B -->|"HTTPS · JWT · SSE"| L1
    L1 -->|"fetch + SSE"| L2
    L2 -->|"imports · injected deps"| L3
    L3 -->|"async drivers"| L4
    AD -.->|"plugs in here"| L2
```

**The question this answers:** "how would you point this at a different problem?"
Write one adapter. The core never learns the domain.

---

## 2. The request flow — end to end

This is the one to memorise. Every node is a real node in the LangGraph.

```mermaid
flowchart TB
    START([POST /query]) --> GI["<b>guard_input</b><br/>injection · PII · schema · content"]
    GI -->|blocked| END1([refused, run ends])
    GI -->|clean| RT["<b>route</b><br/>which specialist?"]

    RT -->|memory intent| AM["<b>answer_memory</b><br/>answer from long-term memory"]
    RT -->|everything else| RM["<b>recall_memory</b><br/>load working memory"]

    RM --> RE["<b>retrieve</b><br/>vector + graph + keyword → RRF → rerank"]
    RE --> ML["<b>ml_predict</b><br/>prediction + interval + SHAP<br/><i>evidence only</i>"]
    ML --> PL["<b>plan</b><br/>propose tool calls"]

    PL -->|no tools proposed| GEN["<b>generate</b>"]
    PL -->|tools proposed| GA["<b>gate</b><br/>risk tier ≥ threshold?"]

    GA -->|low risk| ACT["<b>act</b><br/>execute tools"]
    GA -->|high risk| AP["<b>approval</b><br/>⏸ interrupt + checkpoint"]

    AP -->|approved| ACT
    AP -->|rejected| GEN

    ACT --> RF["<b>reflect</b><br/>did it work?"]
    RF -->|failed, budget left| PL
    RF -->|done| GEN

    GEN --> GO["<b>guard_output</b><br/>grounding · PII · content"]
    AM --> GO
    GO --> ST["<b>stream</b><br/>emit the guarded answer"]
    ST --> PM["<b>persist_memory</b><br/>write the turn"]
    PM --> END2([done])
```

### The three things to say about this diagram

1. **`ml_predict` runs before `plan`, and has no edge to `gate`.** The prediction is
   *injected into the planner as evidence*. It cannot cause a stop. The only edge into
   `approval` comes from `gate`, which reads the **tool's risk tier**.
2. **`reflect → plan` is the self-repair loop**, and it is bounded. `plan` increments a
   counter capped by `max_plan_iterations`, so termination is guaranteed by
   construction rather than by hoping.
3. **Both branches converge on `guard_output`.** The memory specialist skips retrieval,
   ML, planning and tools entirely — but it cannot skip the output rail.

---

## 3. The human-approval gate — the hard part

The other diagram worth memorising. This is what makes the gate *durable* rather than
a blocking wait that dies with the process.

```mermaid
sequenceDiagram
    participant U as Browser
    participant API as FastAPI
    participant G as LangGraph
    participant CP as Postgres checkpointer
    participant DB as approvals table

    U->>API: POST /query
    API->>G: run
    G->>G: gate → risk ≥ threshold
    G->>CP: checkpoint entire state
    G-->>API: __interrupt__
    API->>DB: INSERT approval (PENDING)
    API-->>U: SSE approval_required
    Note over G,CP: the run is now paused, on disk.<br/>The process can die here safely.

    U->>API: POST /approval {approved: true}
    API->>DB: PENDING → RESUMING (optimistic lock)
    Note over DB: the lock is what makes execution exactly-once.<br/>A second decision loses the race and is refused.
    API->>G: Command(resume={approved})
    G->>CP: rehydrate by thread_id
    G->>G: approval node re-executes, returns
    G->>G: act → reflect → generate → guard_output
    G-->>API: SSE answer
    API->>DB: RESUMING → APPROVED
    API-->>U: done
```

### Four details interviewers probe

- **The `approval` node emits nothing before interrupting.** It *re-executes* from the
  top on resume, so anything emitted first would be emitted twice. This is the classic
  trap and avoiding it is deliberate.
- **Exactly-once comes from the database lock, not from LangGraph.** The optimistic
  `PENDING → RESUMING` transition is what stops two workers both resuming.
- **Resume works on any worker.** The checkpointer is shared Postgres; the run is found
  by `thread_id == run_id`. Nothing lives in one process's memory.
- **If the socket dropped, the run is *parked*, not lost.** A sweeper resumes it later.

---

## 4. The trust stack — what stands between the model and a real action

```mermaid
flowchart LR
    Q[query] --> R1["<b>01</b><br/>Input rails<br/><i>injection · PII<br/>schema · scope</i>"]
    R1 --> R2["<b>02</b><br/>Retrieval<br/><i>cited<br/>provenance</i>"]
    R2 --> R3["<b>03</b><br/>Signal<br/><i>conformal<br/>SHAP</i>"]
    R3 --> R4["<b>04</b><br/>Human gate<br/><i>by tool<br/>risk tier</i>"]
    R4 --> R5["<b>05</b><br/>Governance<br/><i>budget · RLS</i>"]
    R5 --> R6["<b>06</b><br/>Audit<br/><i>OTel · append-only</i>"]
    R6 --> A[action]
```

Ordering is the information here. Note that **governance sits between the decision and
the action** — the budget is enforced *before* spend, not reconciled after.

---

## 5. Where a model call actually goes

Every call in the system — planning, generation, embedding, reranking, injection
classification, transcription, vision — passes through one chokepoint.

```mermaid
flowchart TB
    C1[plan] --> GW
    C2[generate] --> GW
    C3[embed] --> GW
    C4[rerank] --> GW
    C5[injection classifier] --> GW
    C6[transcribe] --> GW
    C7[vision] --> GW

    GW["<b>Aegis Gateway</b> (LiteLLM)"] --> BUD{"budget check<br/><i>before spend</i>"}
    BUD -->|over| REJ([budget_exceeded — terminal])
    BUD -->|within| RTE["route by role<br/>cheap · reasoning · generation · voice · vision"]
    RTE --> FLEET[[hosted model fleet]]
    FLEET --> LED[(usage ledger)]
    FLEET --> SPAN[OTel span]
```

**Why one chokepoint matters:** it is the only place budgets, routing, cost accounting
and tracing can be enforced *once* instead of in seven call sites. Any code path that
bypasses it is invisible to all four — which is exactly the class of bug worth hunting.

---

## 6. How the three modalities share one seam

```mermaid
flowchart TB
    AUD[audio upload] --> HY["<b>media hygiene</b><br/>MIME sniff from magic bytes<br/>size cap · bomb guard"]
    IMG[image upload] --> HY
    TXT[text] --> HY

    HY -->|audio| TR["transcribe<br/>hosted Whisper"]
    TR --> TXR["<b>full text rail stack</b><br/>on the transcript"]

    HY -->|image| SCR["<b>injection screen</b><br/>does it contain instructions?"]
    SCR -->|instructions found| BL([BLOCKED — model never called])
    SCR -->|clean| PII["image PII redaction"]
    PII --> VM["vision model"]

    HY -->|text| TXR
    TXR --> AG[agent]
    VM --> OR["output rails"]
    OR --> AG
```

**The single most important arrow** is `SCR → BLOCKED`. Text rendered *into an image*
is the standard attack on vision models, and it is invisible to a human reviewer. The
screen runs **before** the model sees the pixels, and if it cannot run it fails closed.

---

**Next:** [`../guardrails/00-concepts.md`](../guardrails/00-concepts.md).
