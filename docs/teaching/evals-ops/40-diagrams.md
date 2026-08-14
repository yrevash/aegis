# Evals & LLM-Ops — the diagrams

Diagram 2 (the loop) and diagram 5 (the vacuous pass) are the two to be able to draw from
memory. The second one is the story that lands.

---

## 1. The three evaluation layers

```mermaid
flowchart TB
    subgraph L1["<b>Layer 1 — offline deterministic gate</b>"]
        A1["fixed labelled corpus<br/>gold docs + expected claims"] --> A2["run the REAL hybrid retriever<br/><i>local embedding, pass-through reranker</i>"]
        A2 --> A3["context precision@1<br/>context recall<br/>groundedness"]
        A3 --> A4["free, instant, DETERMINISTIC<br/>-> runs in CI on every commit"]
    end

    subgraph L2["<b>Layer 2 — LLM-as-judge</b>"]
        B1["generate an answer<br/>under the candidate prompt"] --> B2["a reasoning model grades<br/>groundedness + relevance"]
        B2 --> B3["sees paraphrase and contradiction<br/>costs money, non-deterministic,<br/><b>and can FAIL</b>"]
    end

    subgraph L3["<b>Layer 3 — trace-level eval</b>"]
        C1["a completed run's steps"] --> C2["grade each facet:<br/>step:retrieval / step:tool / step:guardrail<br/>+ answer"]
        C2 --> C3["turns 'quality dropped' into<br/>'tool selection fails 40% of the time<br/>while retrieval is fine'"]
    end

    L1 --> WHAT["<i>Layer 1 measures RETRIEVAL — necessary, not sufficient.<br/>Layer 2 measures the ANSWER.<br/>Layer 3 measures the STEPS, which is what<br/>diagnosis needs.</i>"]
```

---

## 2. The LLM-Ops loop

```mermaid
flowchart LR
    T["<b>Trace</b><br/>every run instrumented"] --> E["<b>Eval</b><br/>grade answer + each step,<br/>persist one row per facet"]
    E --> D["<b>Diagnose</b><br/>cluster failures by RATE,<br/>ask a model for a better prompt"]
    D --> DR[["a DRAFT PromptVersion<br/><i>never live</i>"]]
    DR --> G["<b>Gate</b><br/>score draft vs baseline<br/>on a REAL eval"]
    G --> R["<b>Release</b><br/>tiered by CHANGE RISK"]
    R --> T

    FLOOR["<b>the floor</b><br/>the hand-authored adapter prompt.<br/>Used when nothing is active —<br/>the loop builds on it and can<br/>NEVER go below it"] -.-> G
    FLOOR -.-> D
```

---

## 3. Prompt version lifecycle

```mermaid
flowchart TB
    START(["diagnose writes it,<br/>or a human authors it"]) --> DRAFT

    DRAFT["<b>DRAFT</b><br/><i>only a DRAFT may be RELEASED</i>"]
    STAGED["<b>STAGED</b><br/><i>only a STAGED version may be DECIDED —<br/>so a replayed decision cannot<br/>re-promote or archive</i>"]
    ACTIVE["<b>ACTIVE</b><br/><i>at most ONE per prompt_key</i>"]
    ARCHIVED["<b>ARCHIVED</b>"]

    DRAFT -->|"eval gate FAILED — rejected"| ARCHIVED
    DRAFT -->|"low risk and beat the baseline — auto-promoted"| ACTIVE
    DRAFT -->|"medium or high risk — escalated"| STAGED
    STAGED -->|"human approved"| ACTIVE
    STAGED -->|"human rejected"| ARCHIVED
    ACTIVE -->|"superseded, or rolled back FROM"| ARCHIVED
    ARCHIVED -->|"rollback target<br/><i>only if activated_at is still set</i>"| ACTIVE
```

---

## 4. The release gate, end to end

```mermaid
flowchart TB
    S["release(draft_version_id, eval_fn, approval_enqueue, autonomy)"] --> G1{"is it a DRAFT?"}
    G1 -->|no| E1([ValueError — never re-release])
    G1 -->|yes| BASE["baseline = the ACTIVE version's prompt,<br/>else the injected FLOOR"]

    BASE --> SC["score BOTH through eval_fn"]
    SC --> FIN{"_require_score:<br/>finite and numeric?"}
    FIN -->|no| E2([ValueError — refuse to gate<br/>on an unusable measurement])
    FIN -->|"eval_fn RAISED — JudgeUnavailableError"| E3(["release abandoned<br/>draft stays DRAFT"])
    FIN -->|yes| RISK["classify_change<br/><i>deterministic, no model call</i>"]

    RISK --> GATE{"draft &lt; baseline + margin?"}
    GATE -->|yes| REJ["status = ARCHIVED<br/>outcome = 'rejected'"]
    GATE -->|no| MODE{"autonomy?"}

    MODE -->|auto| PROM["promote"]
    MODE -->|manual| STAGE["stage + enqueue approval"]
    MODE -->|"tiered (default)"| CEIL{"risk &lt;= auto_promote_ceiling?"}
    CEIL -->|"yes (low)"| PROM
    CEIL -->|"no (medium/high)"| STAGE
```

---

## 5. The vacuous pass — the bug worth memorising

```mermaid
flowchart TB
    J["judge runs on a REASONING model"] --> DRIFT["reply wrapped in a &lt;think&gt; preamble,<br/>a markdown fence, or prose"]
    DRIFT --> OLD["<b>old parser</b>: json.loads fails<br/>-> return JudgeVerdict(0.0, 0.0)"]

    OLD --> AVG["make_eval_fn averages -> 0.0"]
    AVG --> BOTH["<b>and eval_fn is called TWICE</b><br/>draft = 0.0, baseline = 0.0"]

    BOTH --> CMP["release tests:<br/><b>0.0 &lt; 0.0 + 0.0</b>"]
    CMP --> FALSE["= <b>False</b>"]
    FALSE --> PASS["<b>the gate PASSES</b>"]
    PASS --> PROMO["low risk -> AUTO-PROMOTED<br/><i>a judge outage promotes<br/>EVERY candidate prompt</i>"]

    PROMO --> SILENT["API returns outcome='promoted',<br/>eval_score=0.0, baseline_score=0.0<br/><i>reads as 'scored badly',<br/>not 'did not run'</i>"]

    FIX1["<b>fix 1 — TOLERATE real drift</b><br/>strip think-tags and fences,<br/>extract the first BALANCED brace object"] --> DRIFT
    FIX2["<b>fix 2 — RAISE on the unusable</b><br/>JudgeUnavailableError propagates;<br/>the release is abandoned"] --> OLD
    FIX3["<b>fix 3 — reject NaN/inf</b><br/>identical shape: NaN &lt; x is always False"] --> CMP
```

**Why `0.0` looked defensive and was not:** the gate is a **comparison**, not a threshold.
A constant applied to both sides cancels. A conservative default is only conservative
relative to the operation that consumes it.

---

## 6. The unlabelled-case inflation

```mermaid
flowchart TB
    C["an eval case"] --> L{"does it carry a label<br/>for this metric?"}

    L -->|yes| M["compute the real score"]
    L -->|no| OLD["<b>old</b>: score it 1.0"]
    L -->|no| NEW["<b>new</b>: None — not measured"]

    OLD --> A1["mean over ALL cases<br/><i>adding unlabelled cases<br/>RAISES the mean</i>"]
    A1 --> BAD["10 labelled at 0.80 -> mean 0.80<br/>+ 10 unlabelled -> mean <b>0.90</b><br/><i>nothing about retrieval changed</i>"]
    BAD --> MASK["the threshold is held up by cases<br/>that measure nothing, while a real<br/>regression runs underneath"]

    NEW --> A2["mean over CONTRIBUTORS only<br/>+ report the contributor count"]
    A2 --> NONE{"zero contributors?"}
    NONE -->|yes| FAILG["metric = None -> <b>FAILS the gate</b><br/><i>a gate cannot report clearing<br/>a bar it never measured against</i>"]
    NONE -->|no| OKG["an honest mean"]

    TRIG["<i>the triggering action is a GOOD one:<br/>someone broadens the corpus<br/>before labelling it</i>"] -.-> OLD
```

---

## 7. Self-grading — the judge pointed at the wrong thing

```mermaid
flowchart LR
    subgraph WRONG["the bug"]
        W1["retrieved context"] --> W2["judge_answer(query, <b>context</b>, <b>context</b>)"]
        W2 --> W3["'is every claim in the ANSWER<br/>supported by the CONTEXT?'"]
        W3 --> W4["the answer IS the context<br/>-> ~1.0 by construction"]
        W4 --> W5["surfaced as a MODEL-GRADED score.<br/><i>plausible, never moves, no signal</i>"]
    end

    subgraph RIGHT["the fix"]
        R1["retrieved context"] --> R2["<b>generate</b> an answer under<br/>the candidate system prompt"]
        R2 --> R3["judge_answer(query, context, <b>answer</b>)"]
        R3 --> R4["a real measurement<br/><i>two model calls per case</i>"]
    end

    TELL["<i>the tell: a metric that never<br/>changes is not measuring</i>"] -.-> W5
```

The same structure is what makes the release scorer real: it **generates under the
candidate prompt** and judges that, so the score moves with the prompt. A scorer that does
not is the vacuous-pass bug in different clothes.

---

## 8. Change-risk classification

```mermaid
flowchart TB
    IN["(old_prompt, new_prompt,<br/>old_config, new_config)"] --> H1{"changed line fraction<br/>&gt; 0.40?"}
    IN --> H2{"any safety term's WHOLE-WORD<br/>COUNT changed?<br/><i>ignore, guardrail, safety, tool,<br/>approval, never, policy, system prompt</i>"}
    IN --> H3{"a config key containing<br/>model / tool / permission /<br/>role / scope changed?"}

    H1 -->|yes| HIGH["<b>HIGH</b>"]
    H2 -->|yes| HIGH
    H3 -->|yes| HIGH

    H1 -->|no| L{"fraction &lt;= 0.15<br/><b>AND</b> config unchanged or a<br/>bounded tweak of temperature /<br/>top_k / top_p?"}
    L -->|yes| LOW["<b>LOW</b>"]
    L -->|no| MED["<b>MEDIUM</b>"]

    HIGH --> ESC["escalate to a human"]
    MED --> ESC
    LOW --> AUTO["auto-promote<br/><i>ceiling = low by default</i>"]

    THREAT["<b>the threat model</b><br/>an optimiser told 'stop making these<br/>mistakes' will DROP a constraint that<br/>was causing refusals — which IMPROVES<br/>the eval score, because the eval measures<br/>helpfulness, not the constraint"] -.->|"this is what<br/>term counting catches"| H2

    WHYCOUNT["<i>COUNTS, not presence: dropping one of<br/>three 'never' constraints leaves the<br/>word present</i>"] -.-> H2
```

---

## 9. Rollback ordering

```mermaid
flowchart TB
    subgraph NAIVE["the bug — order archived by activated_at DESC"]
        N0["v1 at t1 &middot; v2 at t2 &middot; <b>v3 ACTIVE at t3</b>"] --> N1["roll back once:<br/>v3 archived, keeps t3<br/>-> v2 becomes ACTIVE"]
        N1 --> N2["roll back again:<br/>candidates are v1 at t1 and <b>v3 at t3</b><br/>and t3 &gt; t1"]
        N2 --> N3["<b>v3 is re-promoted</b><br/><i>the broken version goes<br/>straight back to production —<br/>during an incident, on the<br/>operator's own action</i>"]
        N3 --> N4["oscillates between v2 and v3;<br/>v1 is unreachable"]
    end

    subgraph FIXED["the fix — activated_at means 'is a valid revert target'"]
        F0["v1 at t1 &middot; v2 at t2 &middot; <b>v3 ACTIVE at t3</b>"] --> F1["roll back once:<br/>v3 archived, <b>activated_at = NULL</b><br/>-> v2 ACTIVE"]
        F1 --> F2["roll back again:<br/>only candidate with a non-null<br/>marker is v1"]
        F2 --> F3["<b>v1 becomes ACTIVE</b><br/><i>history walks backwards</i>"]
    end

    FREE["<b>free consequences</b><br/>a REJECTED draft (archived, never live)<br/>has a null marker -> never a revert target.<br/>The historical fact is kept in `notes`."] -.-> F1
```

---

## 10. Exactly-once release decisions

```mermaid
flowchart TB
    D["decide_release(approval_id, approved)"] --> LOAD["load the approval row"]
    LOAD --> CLAIM["<b>atomic claim</b><br/>UPDATE approvals SET status=...<br/>WHERE id=? AND status='PENDING'"]

    CLAIM --> RC{"rowcount"}
    RC -->|0| ALREADY["outcome = 'already_decided'<br/>return the <b>RECORDED</b> decision,<br/>not the requested one"]
    RC -->|1| APPLY["apply_release_decision(draft, approved)"]

    APPLY --> ST{"is the version STAGED?"}
    ST -->|no| RAISE["raise -> the WHOLE transaction<br/>rolls back, claim included<br/>-> the row stays PENDING and decidable"]
    ST -->|yes| ACT{"approved?"}
    ACT -->|yes| PROM["promote"]
    ACT -->|no| ARCH["archive"]
    PROM --> COMMIT["commit"]
    ARCH --> COMMIT

    FAIL["<b>the failure being closed</b><br/>a reject replayed AFTER an approve<br/>would archive the now-ACTIVE version,<br/>leaving the prompt_key with NO active<br/>version -> every run silently drops<br/>to the floor prompt"] -.-> CLAIM
```

---

## 11. Diagnose — rate, not volume

```mermaid
flowchart TB
    Q["read the N most recent FAILING<br/>EvalResult rows for this prompt_key"] --> TALLY["tally failures by metric"]
    Q --> WIN["window by <b>id</b>, not ts<br/><i>ids are monotonic and compare<br/>identically on every dialect;<br/>a naive-string CURRENT_TIMESTAMP<br/>does not compare against a<br/>tz-aware bind parameter</i>"]

    WIN --> TOT["count ALL graded rows per metric<br/>over the same window<br/><b>= the denominator</b>"]
    TOT --> CLAMP["clamp: total = max(total, failures)<br/><i>a row written after the window<br/>query would make a rate &gt; 1</i>"]

    TALLY --> RATE["rate = failures / graded"]
    CLAMP --> RATE

    RATE --> ORDER["order the breakdown by RATE"]
    ORDER --> SHOW["always name the known facets,<br/>even at 0%<br/><i>'retrieval is fine, tools are not'<br/>is legible; an absence is not</i>"]

    SHOW --> OPT["optimiser prompt:<br/>'fix the highest RATE,<br/>not the highest count'"]
    OPT --> PARSE{"usable JSON with<br/>a non-blank system_prompt?"}
    PARSE -->|no| NODRAFT(["no draft — never a crash<br/>and never a garbage prompt"])
    PARSE -->|yes| DRAFT([write a DRAFT])

    BAD["<i>a facet graded 500x with 20 failures<br/>outranks one graded 25x with 15<br/>on raw COUNT — pointing the optimiser<br/>at the healthy facet</i>"] -.-> TOT
```

---

**Next:** [`50-interview.md`](50-interview.md).
