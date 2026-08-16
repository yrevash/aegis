# Retrieval — the diagrams

Five diagrams. The two worth reproducing from memory are **the hybrid recall fan-out** and
**the ingestion pipeline** — between them they carry most of a long conversation about this
module.

Everything else is explained in [`10-guide.md`](10-guide.md); a picture is only here when it
shows something prose cannot.

---

## 1. The retrieve path

*Look at the two places the pipeline returns early, and at where `query_vec` gets attached.*

```mermaid
flowchart TB
    Q["query + persona"] --> EX{"exact cache hit?"}
    EX -->|yes| CH([return<br/>cache_hit=true<br/><i>zero model calls —<br/>not even an embedding</i>])
    EX -->|no| EMB["embed the query"]

    EMB --> SEM{"cosine ≥ 0.985?"}
    SEM -->|yes| CH2([return<br/>cache_hit=true])
    SEM -->|no| REC["<b>hybrid wide recall</b><br/>see diagram 2"]

    REC --> FUSE["<b>RRF</b> — 1/(k+rank), k=60"]
    FUSE --> RR{"rerank_enabled?"}
    RR -->|yes| RERANK["one cheap call grades 0–10<br/>on spotlighted candidates"]
    RR -->|no| KEEP["keep the fused order<br/><i>graded=false, honest RRF scores</i>"]

    RERANK --> ASM
    KEEP --> ASM["<b>assemble</b><br/>spotlight → answer_context"]
    ASM --> WRITE["write to cache"]
    WRITE --> ATTACH["attach query_vec<br/><i>AFTER the cache write</i>"]
    ATTACH --> OUT([RetrievalResult])
```

The exact-cache branch returns **before** the embedding call, so the cheapest possible query
costs nothing at all.

`query_vec` is attached after the cache write, so the serialised cache never stores a
3072-float blob and a later cache hit correctly yields `None` rather than a stale vector from a
different query.

---

## 2. Hybrid recall, and the branch that decides what may be claimed

*Look at the `POOL → NOARM` edge. That branch is a whole bug's worth of design.*

```mermaid
flowchart TB
    Q["query"] --> B{"backend implements<br/>MultiListBackend?"}
    B -->|yes| SPLIT["recall_ranked()<br/><b>split origin-tagged lists</b><br/>vector · graph"]
    B -->|no| BLEND["recall()<br/>one pre-blended list<br/>tagged (VECTOR, GRAPH)"]

    SPLIT --> KW
    BLEND --> KW{"backend implements<br/>KeywordBackend?"}

    KW -->|yes| CORP["<b>corpus-wide BM25</b><br/>origins=(BM25,)<br/>scope='corpus'<br/>adds_recall=TRUE"]
    KW -->|no| POOL["<b>pool re-score</b><br/>origins=()<br/>scope='pool'<br/>adds_recall=FALSE"]

    CORP --> ARM["counted as a recall ARM<br/>appears in provenance"]
    POOL --> NOARM["<b>fused, but NOT an arm</b><br/>never in provenance"]

    ARM --> F["reciprocal_rank_fusion"]
    NOARM --> F
    SPLIT --> F
    BLEND --> F
    F --> P([fused pool = the honest N])
```

A keyword pass over documents the other arms already returned cannot surface anything new, and
its IDF over ~20 documents is not a corpus statistic. It is still fused — reordering the pool is
worth doing — but it claims **no origin**, so provenance never says "bm25" for work that added
no recall.

The empty origins tuple is the mechanism: a list can fuse without ever appearing in the claim.

---

## 3. Ingestion

*Look at the two annotated edges, `OFF` and `DED`. Each one is a fixed bug.*

```mermaid
flowchart TB
    D["document"] --> FM["strip frontmatter"]
    FM --> SEC["<b>split by headings</b><br/>build the section path<br/>'Guide > Refunds > EU'"]
    SEC --> PARA["paragraphs"]
    PARA --> OVER{"paragraph > chunk_size?"}
    OVER -->|yes| SENT["sentences"]
    SENT --> MEGA{"sentence > chunk_size?"}
    MEGA -->|yes| WIN["fixed word windows<br/><i>the only level that cuts mid-thought</i>"]
    MEGA -->|no| PACK
    OVER -->|no| PACK["<b>greedy pack</b> to chunk_size<br/>seed the next window with<br/>the trailing `overlap` words"]
    WIN --> PACK

    PACK --> OFF["word_start = running − carried<br/><i>overlap counted ONCE</i>"]
    OFF --> DED["<b>dedup</b><br/>exact: hash(section + body)<br/>near: Jaccard ≥ 0.9, <b>same section only</b>"]
    DED --> LED{"already in the<br/>idempotency ledger?"}
    LED -->|yes| DUP["chunks_duplicate++"]
    LED -->|no| VAL{"content validation"}
    VAL -->|reject| REJ["chunks_rejected++<br/>+ a reason string"]
    VAL -->|ok| WRITE["write with provenance:<br/>section · word_start · word_count<br/>content_hash · source"]
```

`OFF` subtracts the carried words because advancing by the full window length counted every
overlap twice and pushed citation offsets past the end of the document.

`DED` keys on **section + body**, matching the ledger downstream. Keying on the bare body made
"Contact support." under two headings look like one duplicate and silently left the second
section with nothing indexed.

---

## 4. The agentic (Self-RAG) loop

*Look at what increments the round counter — and what does not.*

```mermaid
flowchart TB
    Q["entry query"] --> RW{"rewrite_fn wired?"}
    RW -->|yes| RWC["rewrite against <b>history</b><br/><i>pronouns, ellipsis</i>"]
    RW -->|no| R1
    RWC --> R1["<b>round 1</b>: retrieve"]

    R1 --> J1{"<b>judge</b>: sufficient?"}
    J1 -->|"yes"| STAMP
    J1 -->|"no judge wired /<br/>unparseable"| DET["non-empty context ⇒ sufficient<br/><i>honest deterministic fallback</i>"]
    DET --> STAMP
    J1 -->|"no + rounds remain"| FU["follow-up query<br/>(the judge's, or deterministic)"]

    FU --> R2["<b>round n</b>: retrieve"]
    R2 --> MG["<b>merge</b><br/>cap = max(len base, len incoming)<br/>arms sum · graph delta unions<br/>graded = base AND incoming"]
    MG --> J2{"judge again"}
    J2 -->|"sufficient OR cap reached"| STAMP["stamp RewriteReport<br/>+ AgenticReport in place"]
    J2 -->|"insufficient + rounds remain"| FU

    STAMP --> OUT([merged result<br/>+ per-round new_sources])
```

`used_rounds` increments unconditionally on the `FU → R2` path, and the loop condition is
`used_rounds < max(1, max_rounds)`. The judge chooses the *follow-up query*; it never chooses
the number of rounds. Termination is structural, not heuristic.

The merge cap on the `MG` box is the fix for a loop that could run twice and produce
byte-identical output to a single-shot run.

---

## 5. Where the rewriter's history comes from

*Look at the dotted edge. It points backwards in time, which is the bug.*

```mermaid
flowchart LR
    RM["<b>recall_memory</b><br/>writes state['conversation']"] --> RET["<b>retrieve</b><br/>reads conversation → rewriter"]
    RET --> ML["ml_predict"] --> PL["<b>plan</b><br/>writes state['messages']"]

    PL -.->|"written AFTER retrieve —<br/>always empty at rewrite time"| RET
```

`messages` is a per-planning-round scratch buffer written by `plan`, and `plan` runs *after*
`retrieve`. There is no ordering of the graph in which it could be populated when the rewriter
needs it, so the rewriter was **structurally** unable to do its job — while reporting
`changed=False` for a perfectly innocent-sounding reason.

The fix was to source the transcript from the memory layer, which runs immediately upstream.

**Next:** [`50-interview.md`](50-interview.md) — the questions you'll be asked.
