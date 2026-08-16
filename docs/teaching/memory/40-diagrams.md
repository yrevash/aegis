# Memory — the diagrams

Five diagrams. The two worth reproducing on a whiteboard are **the read path** and
**consolidation** — between them they carry a long conversation about this module.

The prose behind all of it is in [`10-guide.md`](10-guide.md); a picture is only here when it
shows something prose cannot.

---

## 1. The four tiers, and the one arrow that matters

*Look at `EP -> SE`. Everything else is plumbing around that arrow.*

```mermaid
flowchart TB
    subgraph LT["<b>Long-term memory</b> — durable rows"]
        EP["<b>Episodic</b><br/>the turns that happened<br/><i>grows fast, mostly noise</i>"]
        SE["<b>Semantic</b><br/>distilled facts about a subject<br/><i>small, dense with signal</i>"]
        PR["<b>Procedural</b><br/>skills and policies<br/><i>authored by humans</i>"]
        PF["<b>Profile</b><br/>the structured card<br/><i>name, tier, preferences</i>"]
    end

    EP -->|"consolidation distils"| SE

    EP --> W
    SE --> W
    PR --> W
    PF --> W

    subgraph WM["<b>Working memory</b> — assembled per turn, never stored"]
        W["one text block, inside a token budget"]
    end

    W --> P[["injected into the prompt"]]
```

Without the distillation arrow, memory is a transcript you have to search. With it, memory is
knowledge — and *"what tier is this customer?"* becomes a lookup instead of an inference over a
paragraph containing a question mark and a complaint about shipping.

Working memory is not storage. It is a budget, and the four durable tiers compete for it.

---

## 2. The read path — what happens before the agent plans

*Look at the eviction arrow that points backwards. It is a loop, not one drop.*

```mermaid
flowchart TB
    Q["turn arrives<br/>subject + session + query"] --> ACT{"memory active?<br/>session present?"}
    ACT -->|no| NOOP(["return nothing<br/><i>the single-shot path is unchanged</i>"])
    ACT -->|yes| EMB["embed the query<br/><i>without this, fact recall silently<br/>falls back to recency-only</i>"]

    EMB --> PAR["recall each tier, one after another,<br/>on a single session"]

    PAR --> P1["<b>profile</b><br/>direct lookup by subject"]
    PAR --> P2["<b>semantic facts</b><br/>ANN over valid facts, then the<br/>composite score, then top-6"]
    PAR --> P3["<b>episodic turns</b><br/>a recency list and a vector list,<br/>fused with RRF"]
    PAR --> P4["<b>raw window</b><br/>the last 40 turns verbatim"]
    PAR --> P5["<b>skills</b><br/>matched to the task"]

    P1 --> ASM
    P2 --> ASM
    P3 --> ASM
    P4 --> ASM
    P5 --> ASM

    ASM["<b>assemble</b><br/>order the tiers by the layout,<br/>count tokens"] --> BUD{"over budget?"}
    BUD -->|yes| EV["evict one item, bottom tier first<br/><i>and oldest-first inside the raw tier</i>"]
    EV --> BUD
    BUD -->|no| OUT["one working-memory block<br/>+ the ids that made it in<br/>+ the surviving conversation"]
```

Every query on this path is scoped to subject **and** tenant, with `tenant_id=None` meaning the
null-tenant scope rather than a wildcard. The conditional form — filter *only if* a tenant was
passed — reads as defensive coding and behaves as a cross-tenant leak.

Eviction loops because dropping one item may still leave you over: the separators joining the
sections cost tokens too.

The embedding step is not optional. With no query vector, semantic recall degrades to "the six
newest facts", which looks like it is working and is not semantic in any respect.

---

## 3. The write path, and the queue seam

*Look at which arrow commits. That commit is the only reason a crash does not lose the job.*

```mermaid
flowchart LR
    A["answer produced<br/>+ the output rail passed"] --> PER["<b>persist</b><br/>write the user turn and the<br/>assistant turn, bump turn_count"]
    PER --> EMB["store with the query embedding<br/><i>reused from retrieval — free</i>"]
    EMB --> ENQ["every 4th turn: <b>enqueue</b> a<br/>PENDING job and <b>commit</b>"]
    ENQ --> DONE(["turn complete"])

    ENQ -.->|"a tracked background task"| SW["<b>sweep_pending</b> claims it<br/><i>guarded UPDATE: PENDING -> RUNNING</i>"]
    SW -.-> CONS[["consolidation"]]
```

The request path is deliberately cheap: two rows and a queued job. All the expensive thinking
happens off it.

The background task calls `sweep_pending`, not `consolidate` directly. `consolidate` does not
touch the job row, so calling it directly left the job `PENDING` forever — and the interval
sweeper then consolidated the same session a second time.

The guarded claim is what makes that safe under two sweepers: `rowcount == 0` means somebody
else won, so this one skips.

---

## 4. Consolidation — the interesting one

*Look at the `no — hallucinated id` branch. That branch is the difference between a truthful
audit trail and a confidently false one.*

```mermaid
flowchart TB
    J["a claimed job"] --> LOAD["load the running summary<br/>+ the last 10 turns"]
    LOAD --> EXT["<b>extract candidates</b><br/>one cheap model call,<br/>filtered to confidence >= 0.55"]
    EXT --> EMBC["embed each candidate"]

    EMBC --> LOOP{"for each candidate"}
    LOOP --> NN["find the 10 nearest<br/>existing valid facts"]
    NN --> DUP{"top neighbour cosine >= 0.97<br/><b>and</b> the same predicate?"}
    DUP -->|yes| SKIP["<b>noop</b> — a duplicate.<br/>bump its access count,<br/>skip the second model call"]
    DUP -->|no| DEC["<b>decide the operation</b><br/>a cheap model returns<br/>op + target_id + reason"]

    DEC -->|add| ADD["insert a new fact"]
    DEC -->|noop| SKIP
    DEC -->|"update / invalidate"| TGT{"is target_id one of the<br/>neighbours it was shown?"}

    TGT -->|yes| SUP["<b>supersede</b> under a concurrency guard<br/><i>close the old row, insert the successor</i>"]
    TGT -->|"no — hallucinated id"| REJ["<b>refuse</b><br/>write nothing to the fact table,<br/>log a NOOP naming the failure,<br/>count it as <b>rejected</b>"]

    ADD --> PROF
    SKIP --> PROF
    SUP --> PROF
    REJ --> PROF
    PROF["update the profile card<br/><i>from APPLIED writes only</i>"] --> FIN(["job DONE"])
```

**The refusal.** The tempting fallback for an invented id is "use the nearest neighbour
instead". That silently invalidates an unrelated memory, records it as a legitimate
contradiction, and leaves the audit log claiming it was intentional. A hallucinated id is a
model failure, and repairing a model failure by guessing turns one wrong answer into a
permanent wrong record.

**The dedup needs both conditions.** `tier = gold` and `tier = silver` embed extremely close
together and are a contradiction, not a duplicate.

**`PROF` reads applied writes, not candidates.** A candidate ruled `noop`, refused, or lost to
a concurrency race must not still rewrite the profile — which is the always-injected block at
the very top of the prompt, where the model attends most.

---

## 5. The life of a fact

*Look at the exits. Every one of them keeps the row.*

```mermaid
stateDiagram-v2
    [*] --> Current: ADD, with valid_at set
    Current --> Refined: UPDATE — same truth, better wording
    Current --> Contradicted: INVALIDATE — the world changed
    Current --> Archived: PRUNE — decayed and never recalled
    Refined --> [*]
    Contradicted --> [*]
    Archived --> [*]
```

`Current` is the only state hot recall sees, and the filter is one predicate:
`invalid_at IS NULL AND expired_at IS NULL`. That is why a superseded row stops competing
without being destroyed.

| Leaving `Current` | What is written on the old row | The successor |
|---|---|---|
| Refinement (`UPDATE`) | `expired_at = now` — transaction time only | inserted, `supersedes_id` set |
| Contradiction (`INVALIDATE`) | `invalid_at` = when it stopped being true, **and** `expired_at = now` | inserted, `supersedes_id` set |
| Prune (`PRUNE`) | `expired_at = now` | none |

The two supersession edges are the ones people conflate. A **refinement** closes transaction
time only, because the fact never stopped being true — only our wording changed. A
**contradiction** closes both clocks. Get that wrong and your valid-time history reports a
subscription change that never happened.

`PRUNE` is soft archival, not a delete, so "forgetting" here means *stops being recalled*,
never *ceases to exist*.

**Next:** [`50-interview.md`](50-interview.md) — the questions you'll be asked about this.
