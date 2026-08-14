# Memory — the diagrams

Every path through the memory subsystem, drawn. If you can reproduce the read path and
the consolidation loop on a whiteboard, you can hold a long conversation about this.

---

## 1. The four tiers, and where each lives

```mermaid
flowchart TB
    subgraph WM["<b>Working memory</b> — assembled per turn, never stored"]
        W["one text block, inside a token budget"]
    end

    subgraph LT["<b>Long-term memory</b> — durable"]
        EP["<b>Episodic</b><br/>the turns that happened<br/><i>grows fast, mostly noise</i>"]
        SE["<b>Semantic</b><br/>distilled facts about a subject<br/><i>small, dense with signal</i>"]
        PR["<b>Procedural</b><br/>skills and policies<br/><i>authored by humans</i>"]
        PF["<b>Profile</b><br/>the structured card<br/><i>name, tier, preferences</i>"]
    end

    EP -->|consolidation distils| SE
    EP --> W
    SE --> W
    PR --> W
    PF --> W

    W --> P[["injected into the prompt"]]
```

**The arrow that matters** is `EP → SE`. Without it, memory is a transcript you have to
search. With it, memory is knowledge.

---

## 2. The read path — what happens before the agent plans

This runs on every turn, *before* retrieval.

```mermaid
flowchart TB
    Q["turn arrives<br/>subject + session + query"] --> ACT{"memory active?<br/>session present?"}
    ACT -->|no| NOOP([return nothing<br/><i>single-shot path unchanged</i>])
    ACT -->|yes| EMB["embed the query<br/><i>needed to rank semantically</i>"]

    EMB --> PAR["recall each tier in parallel"]

    PAR --> P1["<b>profile</b><br/>direct lookup by subject"]
    PAR --> P2["<b>semantic facts</b><br/>ANN over embeddings<br/>+ recency + importance"]
    PAR --> P3["<b>episodic turns</b><br/>vector + recency, fused"]
    PAR --> P4["<b>raw window</b><br/>the last N turns verbatim"]
    PAR --> P5["<b>skills</b><br/>matched to the task"]

    P1 --> ASM
    P2 --> ASM
    P3 --> ASM
    P4 --> ASM
    P5 --> ASM

    ASM["<b>assemble</b><br/>order the tiers, count tokens"] --> BUD{"over budget?"}
    BUD -->|yes| EV["evict by policy<br/><i>which tier sheds first</i>"]
    EV --> BUD
    BUD -->|no| OUT["one working-memory block<br/>+ the ids that made it in"]
```

**Two things to notice.** The embedding step is not optional — without a query vector,
semantic recall silently degrades to recency-only, which *looks* like it works and
returns whatever is newest regardless of relevance. And eviction is a **loop**, because
dropping one item may still leave you over budget.

---

## 3. The write path — what happens after the answer

```mermaid
flowchart LR
    A["answer produced<br/>+ output rail passed"] --> PER["<b>persist</b><br/>write the user turn<br/>and the assistant turn"]
    PER --> EMB["store with the query embedding<br/><i>reused from retrieval — free</i>"]
    EMB --> ENQ["enqueue a consolidation job"]
    ENQ --> DONE([turn complete])

    ENQ -.->|background, off the request path| CONS[["consolidation sweep"]]
```

The write is deliberately cheap: append two rows and queue a job. Nothing that makes a
user wait. All the expensive thinking happens in the sweep.

---

## 4. Consolidation — the interesting one

```mermaid
flowchart TB
    J["claim a pending job<br/><i>PENDING → RUNNING</i>"] --> LOAD["load the session's recent turns"]
    LOAD --> EXT["<b>extract candidates</b><br/>cheap model reads the turns,<br/>proposes durable facts"]
    EXT --> EMBC["embed each candidate"]

    EMBC --> LOOP{"for each candidate"}
    LOOP --> NN["find nearest existing facts<br/>for this subject"]
    NN --> DEC["<b>decide the operation</b><br/>cheap model compares<br/>candidate vs neighbours"]

    DEC -->|add| ADD["insert a new fact"]
    DEC -->|noop| SKIP["already known — drop it"]
    DEC -->|update / invalidate| TGT{"is the target id<br/>a REAL neighbour?"}

    TGT -->|yes| SUP["<b>supersede</b><br/>close the old fact's valid range,<br/>insert the new one as successor"]
    TGT -->|no — hallucinated id| REJ["<b>refuse</b><br/>write nothing, record it<br/><i>a model failure is not a no-op</i>"]

    ADD --> PROF
    SKIP --> PROF
    SUP --> PROF
    REJ --> PROF
    PROF["update the profile card<br/><i>from APPLIED ops only</i>"] --> FIN([job DONE])
```

### The two edges worth pointing at in an interview

**`TGT → REJ`.** The extraction model returns which existing fact to supersede. If it
invents an id, the tempting fallback is "use the nearest neighbour instead" — and that
silently invalidates an unrelated memory and records it as a legitimate contradiction.
Bitemporal history is then permanently wrong, and nothing in the write log says so. A
hallucinated id is a **model failure** and must be refused, not repaired by guessing.

**`PROF` reads applied ops, not candidates.** If the profile is updated from the raw
candidate list, a fact the reconcile step ruled `noop` — or one whose update lost a
concurrency race — still mutates the profile. The profile then disagrees with the facts
it is supposed to summarise.

---

## 5. Bitemporal supersession, on a timeline

What "nothing is deleted" actually looks like.

```mermaid
flowchart LR
    subgraph MAR["March — learned tier = premium"]
        F1["<b>fact A</b>: tier = premium<br/>valid_from: Mar 1<br/>valid_to: ∞<br/>recorded: Mar 1"]
    end

    subgraph JUL["July — learns about the downgrade"]
        F1B["<b>fact A</b>: tier = premium<br/>valid_from: Mar 1<br/><b>valid_to: Jul 1</b> ← closed<br/>recorded: Mar 1"]
        F2["<b>fact B</b>: tier = standard<br/>valid_from: Jul 1<br/>valid_to: ∞<br/><b>recorded: Jul 15</b>"]
    end

    F1 -->|superseded, never deleted| F1B
    F1B --> F2
```

Now both questions are answerable:

- *"What tier were they on in May?"* → fact A. **Valid time.**
- *"What did we believe on 10 July?"* → still premium; we only learned on the 15th.
  **Transaction time.** This is the audit question.

---

## 6. Recall scoring

```mermaid
flowchart TB
    C["candidate memories"] --> R["<b>relevance</b><br/>cosine(query, memory)"]
    C --> T["<b>recency</b><br/>exponential decay<br/>on age"]
    C --> I["<b>importance</b><br/>intrinsic significance"]

    R --> FLOOR{"absolute similarity<br/>above the floor?"}
    FLOOR -->|no| DROP["drop it<br/><i>recalling nothing is<br/>a valid answer</i>"]
    FLOOR -->|yes| NORM["normalise across candidates"]

    T --> NORM
    I --> NORM
    NORM --> SUM["weighted sum<br/>w_rel·rel + w_rec·rec + w_imp·imp"]
    SUM --> TOPK["take top-k within budget"]
```

**The floor must come before normalisation.** Min-max scaling maps the best candidate
to 1.0 no matter how bad it is — so a set whose best cosine is 0.15 still produces a
confident top-ranked "relevant" memory. Filter on the *absolute* score first.

---

## 7. Tenant isolation on every read

```mermaid
flowchart TB
    RQ["recall request<br/>subject + tenant"] --> APP["application filter<br/>WHERE subject AND tenant"]
    APP --> RLS["<b>row-level security</b><br/>DB-enforced policy"]
    RLS --> ROWS["only this tenant's rows"]

    APP -.->|"❌ the trap:<br/>filter only IF tenant is not None"| LEAK["an unscoped call<br/>matches EVERY tenant"]
```

The dotted edge is the real bug pattern. Written as `if tenant_id is not None: add
filter`, it reads as defensive coding and behaves as a cross-tenant leak whenever a
caller passes nothing. The correct form is symmetric: a null tenant must match *null
tenant rows only*, not all rows.

---

**Next:** [`50-interview.md`](50-interview.md) — the questions you'll be asked about this.
