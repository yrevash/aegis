# The Gateway — the diagrams

If you can draw diagram 2 (the `complete` path) and diagram 4 (cost resolution) from
memory, you can talk about this module for half an hour.

---

## 1. Why one chokepoint

```mermaid
flowchart TB
    subgraph CALLERS["every caller in the system"]
        A["agent: plan / answer / reflect"]
        B["retrieval: rerank, rewrite"]
        C["guardrails: injection classifier"]
        D["memory: consolidation"]
        E["evals: judge"]
        F["voice / vision"]
    end

    A --> GW
    B --> GW
    C --> GW
    D --> GW
    E --> GW
    F --> GW

    GW["<b>aegis.gateway</b><br/>complete / embed / transcribe"]

    GW --> POL["budget enforced BEFORE spend"]
    GW --> LED["one durable ledger row"]
    GW --> SPAN["one gen_ai span"]
    GW --> COST["cost priced, provenance tagged"]
    GW --> ROUTE["role to deployment, with fallbacks"]

    POL --> PROV[["the model fleet"]]
    ROUTE --> PROV
```

**The claim this diagram makes:** there is no arrow from a caller straight to the
provider. That is the entire design.

---

## 2. `complete` — the full path

```mermaid
flowchart TB
    START["complete(role, messages, ...)"] --> CTX{"governance context bound?"}
    CTX -->|"no — ungoverned"| SKIP["skip enforcement<br/>and ledgering"]
    CTX -->|yes| ENF["<b>enforce(ctx)</b><br/><i>reads the ledger sum</i>"]

    ENF --> OVER{"over any cap?"}
    OVER -->|yes| RAISE([raise BudgetExceededError<br/>no provider call])
    OVER -->|no| PREP

    SKIP --> PREP["count images<br/>bound max_tokens<br/>build fallback chain"]

    PREP --> SPAN["open gen_ai span"]
    SPAN --> CALL["_bounded_acompletion<br/><i>per-attempt timeout +<br/>outer wall-clock ceiling</i>"]

    CALL --> ACC["<b>_account</b>"]

    subgraph ACCOUNT["_account — runs per real attempt"]
        A1["read prompt / completion tokens"] --> A2["_resolve_cost"]
        A2 --> A3["record_call: process tally"]
        A3 --> A4["set_usage on the span"]
        A4 --> A5["_record_usage: durable ledger row<br/><i>best-effort, never raises</i>"]
    end

    ACC --> JSON{"JSON asked for,<br/>no tool calls,<br/>and reply invalid?"}
    JSON -->|yes| REASK["ONE corrective re-ask<br/><i>never a loop</i>"]
    REASK --> ACC2["_account again"]
    ACC2 --> OUT
    JSON -->|no| OUT["LLMResult<br/>content + tool_calls + usage + model"]
```

**The two edges to point at.** `OVER -->|yes| RAISE` is the whole budget story: the
refusal happens before any provider contact. And the `JSON` guard requires *no tool
calls* — a tool-call reply has empty content by design, and without that condition every
tool call would pay for a second round trip.

---

## 3. Role routing and fallback

```mermaid
flowchart LR
    R["caller asks for a ROLE"] --> M["model_for(role)<br/><i>env override wins</i>"]
    M --> P["primary deployment"]

    P --> TRY{"attempt succeeds?"}
    TRY -->|yes| DONE([response])
    TRY -->|no| FB["next in the role's<br/>fallback chain"]
    FB --> TRY2{"succeeds?"}
    TRY2 -->|yes| DONE
    TRY2 -->|no| FAIL([error propagates])

    DONE --> CHK{"response.model ==<br/>the role's primary?"}
    CHK -->|no| FIRED["fallback_fired = true<br/><i>measured, not guessed</i>"]
    CHK -->|yes| NORM["normal"]
```

Default chains: `GENERATION → [REASONING, CHEAP]`, `REASONING → [GENERATION, CHEAP]`,
`CHEAP → [GENERATION]`. Each attempt carries its own timeout; the outer ceiling is
`timeout × (len(chain) + 1)`.

---

## 4. Cost resolution — never a silent zero

```mermaid
flowchart TB
    C["one completed call"] --> P{"provider cost map<br/>priced it > 0?"}
    P -->|yes| PROV["cost, source = PROVIDER"]
    P -->|no| E{"measured units x<br/>configured rate > 0?"}

    E -->|yes| EST["cost, source = ESTIMATED"]
    E -->|no| B{"did the call consume<br/>ANY billable work?"}

    B -->|"yes — tokens, seconds,<br/>images, or billable_work"| UNP["<b>0.00, source = UNPRICED</b><br/>+ WARNING naming the role,<br/>model and every measured unit"]
    B -->|no| ZERO["0.00, source = ESTIMATED<br/><i>a genuine, unambiguous zero</i>"]
```

**`UNPRICED` is the point of this diagram.** Without that branch, "we could not price
this call" and "this call was free" are the same number, and a cost dashboard that
cannot tell them apart is a cost dashboard that lies.

---

## 5. Billing units — why tokens are not enough

```mermaid
flowchart TB
    CALL["a completed call"] --> U{"billing_unit(role)"}

    U -->|TOKENS| T["units = prompt_tokens / 1000"]
    U -->|AUDIO_MINUTES| A["units = audio_seconds / 60"]
    U -->|IMAGES| I["units = image count"]

    T --> COST
    A --> COST
    I --> COST

    COST["cost = units x input_rate<br/>+ completion_tokens/1000 x output_rate"]
    COST --> LED["Usage carries tokens<br/>AND audio_seconds AND images"]
    LED --> ROW["ledger row carries all three"]
    ROW --> CAP["the USD cap sums cost_usd,<br/>so a per-minute charge BINDS"]

    OLD["the old model:<br/>tokens only"] -.->|"Whisper reports 0 tokens"| BAD["$0.00 in the ledger<br/>uncapped transcription"]
```

The dotted branch is the bug. `VOICE` is priced `(0.006, 0.0)` — per **audio minute**,
with no output-token rate, because Whisper produces no billable output tokens.

---

## 6. The savings calculation, and the three ways it flatters you

```mermaid
flowchart TB
    CALL["one call: measured usage"] --> BASE["_baseline_cost:<br/>price the same work at<br/>the frontier baseline role"]
    CALL --> ACT["actual cost"]

    BASE --> NT{"non-token work?<br/>(audio / images)"}
    NT -->|yes| MAXC["baseline = max(token_baseline, actual)<br/><i>a frontier chat model cannot<br/>transcribe — book ZERO saving,<br/>never a fabricated negative</i>"]
    NT -->|no| TOK["baseline = token price"]

    MAXC --> SAVE
    TOK --> SAVE
    ACT --> SAVE
    SAVE["saving = max(0, baseline - actual)"]

    SAVE --> AGG["cumulative tally"]
    SAVE --> PER["per-call, from THIS call's Usage only<br/><i>reads no shared state</i>"]

    T1["trap 1: a 90B model<br/>classified as 'small'"] -.-> AGG
    T2["trap 2: embeddings in the<br/>small-model-share denominator"] -.-> AGG
    T3["trap 3: before/after delta<br/>across an await"] -.-> PER
```

All three dotted traps were real bugs here. Note that all three move the number in the
**favourable** direction — which is why nobody notices them.

---

## 7. `transcribe` — the non-token path

```mermaid
flowchart TB
    T["transcribe(audio, ...)"] --> ENF["enforce BEFORE spend<br/><i>identical, modality-agnostic gate</i>"]
    ENF --> H["_audio_handle<br/><i>path: open and close here<br/>handle: leave alone</i>"]
    H --> CALL["atranscription(file=handle,<br/>response_format='verbose_json')"]

    CALL --> D{"provider reported<br/>a duration?"}
    D -->|yes| USE["billable_seconds = reported"]
    D -->|no| CALLER{"caller supplied<br/>duration_seconds?"}
    CALLER -->|yes| USE2["billable_seconds = supplied"]
    CALLER -->|no| WARN["<b>WARNING</b>: per-minute charge<br/>cannot be determined"]

    USE --> COST
    USE2 --> COST
    WARN --> COSTU["_resolve_cost(billable_work=True)<br/>-> UNPRICED, not free"]

    COST["_resolve_cost with audio_seconds"] --> LED
    COSTU --> LED
    LED["record_call + ledger row<br/>carrying audio_seconds"]
```

`response_format="verbose_json"` is not a formatting preference. It is what carries
`duration` — **the billing unit**.

---

## 8. Composition: how the backend wires the hooks

```mermaid
flowchart TB
    subgraph HOST["backend — app.core.llm (strangler shim, import time)"]
        CFG["_SettingsGatewayConfig<br/><i>properties, read fresh per call</i>"]
        GOV["_GovernanceHook"]
        OBS["OtelObservabilitySink<br/><i>from aegis.observability</i>"]
    end

    CFG --> CONF["gateway.configure(...)"]
    GOV --> CONF
    OBS --> CONF

    CONF --> GWM["aegis.gateway module state"]

    GOV --> G1["get_context: contextvar,<br/>None unless a tenant is bound"]
    GOV --> G2["enforce: app.data.governance"]
    GOV --> G3["record: usage_ledger row"]

    G2 --> FAIL{"enforcement read raised?"}
    FAIL -->|"BudgetExceededError"| PROP["propagate — a real breach"]
    FAIL -->|"anything else"| CLOSED["<b>FAIL CLOSED</b>: deny<br/>limit_type='enforcement_error'"]
    FAIL -.->|"budget_fail_open=true"| OPEN["allow, log a warning"]
```

`_SettingsGatewayConfig` uses **properties, not a snapshot**, so mutating the settings
singleton at runtime (or in a test) is honoured on the next call.

---

## 9. Where the context is bound on a request

```mermaid
flowchart TB
    REQ["POST /query"] --> AUTH["require_auth: decode the JWT"]
    AUTH --> RG["_resolve_governance(auth)"]
    RG --> T{"tenant bound?"}
    T -->|no| EMPTY["empty GovernanceContext<br/><i>chokepoint enforces nothing</i>"]
    T -->|yes| LIM["effective_limits(tenant, user)<br/><i>user cap clamped inward<br/>to the tenant cap</i>"]

    LIM --> CTXOBJ["GovernanceContext"]
    EMPTY --> CTXOBJ

    CTXOBJ --> GEN["<b>inside the SSE generator task</b><br/>set_governance_context(ctx)"]
    GEN --> RUN["run_agent(...) — every model call<br/>inside now sees the context"]
    RUN --> FIN["finally: reset_governance_context(token)"]
```

**The placement is load-bearing.** The context is bound *inside* the streaming task, not
around it. An SSE generator runs in its own context; binding outside would not be
visible at the chokepoint.

---

**Next:** [`50-interview.md`](50-interview.md) — the questions you will be asked.
