# Foundations — the diagrams

Five pictures. Two of them you should be able to draw on a whiteboard from memory — **the
request flow** and **the approval interrupt/resume** — because between them they carry a
forty-minute conversation about this system.

Everything here is explained in words in [`10-guide.md`](10-guide.md). A diagram is only in this
file when it shows something a paragraph cannot.

---

## 1. Where the code lives

*Look at the arrows. Every one points down, and the adapter joins at layer 2.*

```mermaid
flowchart TB
    L1["<b>1 · Console</b> — web/<br/>Next.js · React · TypeScript"]
    L2["<b>2 · Composition root</b> — backend/src/app<br/>FastAPI · auth · tenant scoping<br/><i>wires the core to the world</i>"]
    L3["<b>3 · Importable core</b> — aegis/src/aegis<br/><i>no domain logic, no web framework</i>"]
    L4["<b>4 · Stores</b><br/>Postgres · embedded vectors · Neo4j · Redis · Phoenix"]
    AD["<b>Domain adapter</b> — app/adapter/<br/>schema · tools · prompts · corpus"]

    L1 -->|"REST + SSE"| L2
    L2 -->|"imports, injects dependencies"| L3
    L3 -->|"async drivers"| L4
    AD -.->|"plugs in here"| L2
```

Nothing at layer 3 ever reaches up to layer 2. That is what makes `aegis/` a package you import
rather than an app you fork.

The dotted line is the answer to "how would you point this at a different problem?" — you write
one adapter, and the core never learns your domain.

---

## 2. The request flow, end to end

*Look at the two edges that leave the happy path: `blocked` at the top, and `reflect → plan`
looping back.*

```mermaid
flowchart TB
    START([POST /query]) --> GI["<b>guard_input</b><br/>injection · PII · schema · content"]
    GI -->|blocked| END1([refused — the run ends here])
    GI -->|clean| RT["<b>route</b><br/>which specialist?"]

    RT -->|memory question| AM["<b>answer_memory</b>"]
    RT -->|everything else| RM["<b>recall_memory</b>"]

    RM --> RE["<b>retrieve</b><br/>evidence for the answer"]
    RE --> ML["<b>ml_predict</b><br/><i>evidence only — never routes</i>"]
    ML --> PL["<b>plan</b><br/>propose tool calls"]

    PL -->|no tools proposed| GEN["<b>generate</b>"]
    PL -->|tools proposed| GA["<b>gate</b><br/>risk tier of each tool"]

    GA -->|below the threshold| ACT["<b>act</b><br/>execute the tools"]
    GA -->|at or above| AP["<b>approval</b><br/>⏸ interrupt + checkpoint"]

    AP -->|approved| ACT
    AP -->|rejected| GEN

    ACT --> RF["<b>reflect</b><br/>did it work?"]
    RF -->|"failed, budget left"| PL
    RF -->|done| GEN

    GEN --> GO["<b>guard_output</b><br/>grounding · PII · content"]
    AM --> GO
    GO --> ST["<b>stream</b>"]
    ST --> PM["<b>persist_memory</b>"]
    PM --> END2([done])
```

Three things to say about this picture, in this order:

**`ml_predict` has no edge to `gate`.** The prediction is injected into the planner as evidence
and cannot cause a stop. The only arrow into `approval` comes from `gate`, which reads the
**tool's** risk tier — not the model's confidence.

**`reflect → plan` is bounded.** `plan` increments the counter, not `reflect`, so no model output
can extend the budget. Termination is guaranteed by construction.

**Both branches converge on `guard_output`.** The memory specialist skips retrieval, ML, planning
and tools entirely — and still cannot skip the output rail.

---

## 3. Inside `retrieve`

*Look at where the two arms meet: fusion happens on ranks, after the arms, before the reranker.*

```mermaid
flowchart TB
    Q([question]) --> C{"semantic cache —<br/>seen something<br/>close enough?"}
    C -->|hit| OUT
    C -->|miss| V["<b>vector</b><br/>nearest by meaning"]
    C -->|miss| G["<b>graph</b><br/>entities and their links"]
    C -->|miss| K["<b>keyword</b><br/>BM25 · exact terms"]

    V --> F["<b>RRF fusion</b><br/><i>throws the scores away,<br/>uses only the ranks</i>"]
    G --> F
    K --> F

    F --> RR["<b>rerank</b><br/>re-read the shortlist<br/>against the question"]
    RR --> OUT["top k passages<br/>+ their provenance"]
    OUT --> P([into the prompt])
```

The three arms exist because they fail on different inputs. Vector search finds "what's the
refund process?" from "how do I get my money back?" and cannot tell `INV-2291` from `INV-2293`.
Keyword search is the exact opposite.

Fusion sits where it does because the arms produce numbers that cannot be added — a cosine of
0.71 and a BM25 of 8.4 are not the same kind of quantity. Ranks are comparable; scores are not.

Reranking sits last because it is the expensive stage. Cheap and approximate to build a
shortlist; expensive and accurate to order it.

---

## 4. Pausing a run for a human

*Look at the note in the middle. Everything above it can be thrown away and the run still
finishes.*

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant API as FastAPI
    participant G as LangGraph
    participant CP as Postgres checkpointer
    participant DB as approvals table

    U->>API: POST /query
    API->>G: run
    G->>G: gate → risk at or above threshold
    G->>CP: checkpoint the entire state
    G-->>API: __interrupt__
    API->>DB: INSERT approval (PENDING)
    API-->>U: approval_required
    Note over G,CP: the run is now paused, on disk.<br/>The process can die here safely.

    U->>API: POST /approval {approved: true}
    API->>DB: PENDING → RESUMING, only if still PENDING
    Note over DB: this guarded update is what makes<br/>the tool execute exactly once
    API->>G: Command(resume={approved})
    G->>CP: rehydrate by thread_id
    G->>G: approval node RE-EXECUTES from its first line
    G->>G: act → reflect → generate → guard_output
    G-->>API: the answer
    API->>DB: RESUMING → APPROVED
    API-->>U: done
```

**Step 12 is where exactly-once actually lives.** It is a database write with a condition on the
old status, not anything LangGraph does. LangGraph will happily resume the same checkpoint twice
if you ask it to; the database is what refuses.

**Step 16 is the detail people miss.** The approval node runs again *from its first line*, which
is why nothing may be emitted before the interrupt — or the user sees the approval request twice.

Because the checkpointer is shared Postgres and the run is found by `thread_id`, the resume does
not have to happen on the machine that started it. That is the difference between surviving a
page refresh and surviving a deploy.

---

## 5. Every model call goes through one door

*Look at the budget check: it is before the call, not after.*

```mermaid
flowchart TB
    CALLERS["<b>every model call in the system</b><br/>plan · generate · embed · rerank<br/>injection classifier · transcribe · vision"]
    CALLERS --> GW["<b>Aegis Gateway</b>"]
    GW --> BUD{"budget<br/>check"}
    BUD -->|over| REJ([budget_exceeded — terminal])
    BUD -->|within| RTE["route by job<br/>cheap · reasoning · generation · voice · vision"]
    RTE --> FLEET[["hosted model fleet"]]
    FLEET --> LED[(usage ledger)]
    FLEET --> SPAN[["OTel span"]]
```

One chokepoint is the only way budgets, routing, cost accounting and tracing get enforced once
instead of at seven call sites that will drift apart.

Which also means any code path that reaches a model without passing through here is invisible to
all four at once — and that is exactly the class of bug worth hunting.

---

**Next:** [`50-interview.md`](50-interview.md) — the questions you'll be asked.
