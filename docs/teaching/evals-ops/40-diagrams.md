# Evals & LLM-Ops — the diagrams

Five diagrams. The two worth reproducing from memory are **the loop** and **the vacuous pass** —
the first is what the module is, the second is why anyone should trust it.

Everything else about this module is explained in [`10-guide.md`](10-guide.md); a picture is
only here when it shows something prose cannot.

---

## 1. The LLM-Ops loop

*Look at what Diagnose is allowed to emit, and at the dotted edge into Gate.*

```mermaid
flowchart LR
    T["<b>Trace</b><br/>every run instrumented"] --> E["<b>Eval</b><br/>grade the answer and each step,<br/>one persisted row per facet"]
    E --> D["<b>Diagnose</b><br/>cluster failures by RATE,<br/>ask a model for a better prompt"]
    D --> DR[["a DRAFT PromptVersion<br/><i>never live</i>"]]
    DR --> G["<b>Gate</b><br/>score draft against baseline<br/>on a real eval"]
    G --> R["<b>Release</b><br/>tiered by CHANGE RISK"]
    R --> T

    FLOOR["<b>the floor</b><br/>the hand-authored adapter prompt"] -.->|"the baseline whenever<br/>no version is ACTIVE"| G
```

The optimiser's only output is a **DRAFT**. It cannot stage, promote, or reach production by any
route that does not pass through Gate and Release.

The floor is why a self-modifying system is safe to run: with nothing active, the baseline is the
prompt a human wrote, so the loop builds **on** it and can never settle below it.

---

## 2. The prompt version lifecycle

*Look at the two ways into ARCHIVED — only one of them can come back.*

```mermaid
stateDiagram-v2
    [*] --> DRAFT: diagnose writes it, or a human authors it
    DRAFT --> ARCHIVED: eval gate failed
    DRAFT --> ACTIVE: beat the baseline, risk under the ceiling
    DRAFT --> STAGED: beat the baseline, risk above the ceiling
    STAGED --> ACTIVE: human approved
    STAGED --> ARCHIVED: human rejected
    ACTIVE --> ARCHIVED: superseded, or rolled back FROM
    ARCHIVED --> ACTIVE: rollback target
```

Two mirror guards keep every transition single-use: **only a DRAFT may be released**, and **only a
STAGED version may be decided**. A replayed decision therefore cannot re-promote or re-archive.

The `ARCHIVED --> ACTIVE` edge is not open to every archived row — it requires `activated_at` to
still be set. A rejected draft was never live, so it never carries that marker and can never be
rolled back *to*; the version you rolled back *from* has its marker cleared, so it cannot be
rolled back *to* either.

At most one version per `prompt_key` is ACTIVE, enforced in `promote`.

---

## 3. The release gate, end to end

*Look at the three exits above the comparison — none of them promotes anything.*

```mermaid
flowchart TB
    S["release: draft_version_id, eval_fn, autonomy"] --> G1{"status is DRAFT?"}
    G1 -->|no| E1([ValueError — never re-release])
    G1 -->|yes| BASE["baseline = the ACTIVE version's prompt,<br/>else the injected FLOOR"]

    BASE --> SC["score the draft AND the baseline<br/>through the same eval_fn"]
    SC --> RAISED{"eval_fn raised?"}
    RAISED -->|"JudgeUnavailableError"| E3(["release abandoned —<br/>the draft is left DRAFT"])
    RAISED -->|no| FIN{"both scores numeric<br/>and finite?"}
    FIN -->|no| E2([ValueError — refuse to gate on<br/>an unusable measurement])
    FIN -->|yes| RISK["classify_change<br/><i>deterministic, no model call</i>"]

    RISK --> GATE{"draft &lt; baseline + margin?"}
    GATE -->|yes| REJ["ARCHIVED, outcome rejected"]
    GATE -->|no| MODE{"autonomy"}

    MODE -->|auto| PROM["promote to ACTIVE"]
    MODE -->|manual| STAGE["STAGED, plus a durable approval row"]
    MODE -->|"tiered, the default"| CEIL{"risk at or under<br/>auto_promote_ceiling?"}
    CEIL -->|"yes — low"| PROM
    CEIL -->|"no — medium or high"| STAGE
```

`classify_change` runs on the **diff**, not on the score, and makes no model call — the classifier
deciding whether a model's proposal is safe must not itself be a model.

Note the ordering: risk is classified after both scores exist but the eval result cannot influence
it. A HIGH-risk change goes to a human however well it scored.

The default margin is `0.0`, so a draft has to be strictly better. That single fact is what makes
the next diagram possible.

---

## 4. The vacuous pass — the bug worth memorising

*Follow the top chain first, then read the two branches off the parser.*

```mermaid
flowchart TB
    J["a reasoning judge replies with its JSON<br/>wrapped in a think-tag, a fence, or prose"] --> P{"the parser"}

    P -->|"lenient: return JudgeVerdict 0.0"| Z["every case scores <b>0.0</b>"]
    Z --> BOTH["eval_fn is called <b>TWICE</b><br/>draft 0.0, baseline 0.0"]
    BOTH --> CMP["the gate tests<br/><b>0.0 &lt; 0.0 + 0.0</b>"]
    CMP --> F["<b>False</b> — the reject branch never fires"]
    F --> PROMO["low risk, so <b>AUTO-PROMOTED</b><br/><i>one judge outage promotes<br/>EVERY candidate prompt</i>"]
    PROMO --> SILENT["the API reports outcome promoted,<br/>eval_score 0.0, baseline_score 0.0<br/><i>which reads as scored badly,<br/>not as did not run</i>"]

    P -->|"<b>fix 1</b> — tolerate real drift:<br/>strip think-tags and fences,<br/>take the first BALANCED brace object"| OK(["a genuine score,<br/>including a genuine 0.0"])
    P -->|"<b>fix 2</b> — still unreadable:<br/>raise JudgeUnavailableError"| STOP(["the release is abandoned"])

    NAN["<b>fix 3</b> — NaN is the same bug:<br/>NaN &lt; x is False for every x"] -.->|"rejected by _require_score<br/>before the comparison"| CMP
```

`0.0` looked defensive and was not, for one precise reason: **the gate is a comparison, not a
threshold.** A constant applied to both sides cancels, and the test degenerates into `0 < 0`.

Fix 1 and fix 2 are one design, not two: a uniformly strict parser turns routine formatting drift
into false failures, and a uniformly lenient one produces this. A real `0.0` still parses as
`0.0`.

> A control that cannot fail is worse than no control, because it produces the paperwork of
> safety with none of the substance.

---

## 5. Exactly-once release decisions

*Look at the order — the row is claimed before the draft is touched.*

```mermaid
flowchart TB
    D["a human decides a staged release"] --> LOAD["load the approval row"]
    LOAD --> CLAIM["<b>atomic claim</b><br/>UPDATE approvals SET status = ...<br/>WHERE id = ? AND status = PENDING"]

    CLAIM --> RC{"rowcount"}
    RC -->|0| ALREADY(["outcome already_decided —<br/>return the <b>RECORDED</b> decision,<br/>not the requested one"])
    RC -->|1| APPLY["apply_release_decision"]

    APPLY --> ST{"is the version STAGED?"}
    ST -->|no| RAISE["raise — the WHOLE transaction rolls back,<br/>claim included, so the row stays<br/>PENDING and decidable"]
    ST -->|yes| ACT{"approved?"}
    ACT -->|yes| PROM["promote"]
    ACT -->|no| ARCH["archive"]
    PROM --> COMMIT["commit"]
    ARCH --> COMMIT
```

Claim first, act second. Claiming afterwards would leave a window in which two workers both
applied the decision.

The failure being closed is a **reject replayed after an approve**: it would archive the
now-ACTIVE version, leaving the prompt key with no active version at all, so every run silently
drops to the floor prompt. Nothing errors — the system just stops using the prompt it promoted.

`rowcount == 0` returning the recorded decision rather than the requested one is what makes the
endpoint genuinely idempotent instead of merely harmless.

**Next:** [`50-interview.md`](50-interview.md).
