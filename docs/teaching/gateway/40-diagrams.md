# The gateway — the diagrams

Five diagrams. The two worth reproducing from memory are **the `complete` path** and **cost
resolution** — between them they carry most of a conversation about this module.

The reasoning behind each is in [`10-guide.md`](10-guide.md); a picture is only here when it
shows something prose cannot.

---

## 1. Why one chokepoint

*Look for an arrow from a caller straight to the fleet. There is none — that is the entire
design.*

```mermaid
flowchart TB
    subgraph CALLERS["every caller in the system"]
        A["agent<br/>plan · reflect · generate"]
        B["retrieval<br/>rewrite · embed · rerank"]
        C["guardrails<br/>injection classifier"]
        D["memory<br/>consolidation"]
        E["evals<br/>judge"]
        F["voice · vision"]
    end

    A --> GW
    B --> GW
    C --> GW
    D --> GW
    E --> GW
    F --> GW

    GW["<b>aegis.gateway</b><br/>complete · embed · transcribe"]
    GW --> PROV[["the model fleet"]]
```

One user question is eleven model calls across six modules. Every one of them is a place
somebody could forget a timeout, forget to ledger the cost, hard-code a model id, or skip the
budget check.

> A convention that must be followed in forty places is not a control. It is a hope.

Because there is exactly one edge into the fleet, budget enforcement, the ledger row, the
`gen_ai` span, cost provenance and role routing each exist in exactly one place — including for
the call site written next year by someone who read none of this.

---

## 2. `complete` — the full path

*Look at the two red-flag edges: `OVER -> RAISE`, and the middle clause of the JSON guard.*

```mermaid
flowchart TB
    START["complete(role, messages, ...)"] --> CTX{"governance context bound?"}
    CTX -->|"no — ungoverned"| SKIP["skip enforcement and ledgering<br/><i>no database is touched</i>"]
    CTX -->|yes| ENF["<b>enforce(ctx)</b><br/><i>reads the ledger sum</i>"]

    ENF --> OVER{"over any cap?"}
    OVER -->|yes| RAISE(["raise BudgetExceededError<br/><b>no provider contact</b>"])
    OVER -->|no| PREP

    SKIP --> PREP["count images<br/>bound max_tokens<br/>build the fallback chain"]

    PREP --> SPAN["open the gen_ai span"]
    SPAN --> CALL["_bounded_acompletion<br/><i>per-attempt timeout inside,<br/>outer wall-clock ceiling around</i>"]

    CALL --> ACC["<b>_account</b>"]

    subgraph ACCOUNT["_account — runs on every response, re-asks included"]
        A1["read prompt / completion tokens"] --> A2["_resolve_cost -> cost + provenance"]
        A2 --> A3["record_call: the process tally"]
        A3 --> A4["set_usage on the span"]
        A4 --> A5["_record_usage: durable ledger row<br/><i>best-effort, never raises</i>"]
    end

    ACC --> JSON{"JSON asked for,<br/><b>no tool calls</b>,<br/>and the reply does not parse?"}
    JSON -->|yes| REASK["ONE corrective re-ask<br/><i>never a loop</i>"]
    REASK --> ACC2["_account again"]
    ACC2 --> OUT
    JSON -->|no| OUT["LLMResult<br/>content + tool_calls + usage + model"]
```

The refusal happens **before** any provider contact. That is the difference between a cap and a
receipt: reconciliation tells you that you overspent.

The JSON guard requires *no tool calls* because a tool-call reply has empty content by design.
Drop that clause and every action the agent ever takes pays for a second round trip.

Note where the fallback chain lives — inside `_bounded_acompletion`, not around it. A fired
fallback is detected afterwards, by comparing `LLMResult.model` (the deployment that actually
responded) against the role's primary.

---

## 3. Cost resolution — never a silent zero

*Look at the `UNPRICED` box. Without it, "we could not price this" and "this was free" are the
same number.*

```mermaid
flowchart TB
    C["one completed call"] --> P{"the provider's own cost map<br/>priced it above zero?"}
    P -->|yes| PROV["cost · source = PROVIDER"]
    P -->|no| E{"measured units x the<br/>configured rate is above zero?"}

    E -->|yes| EST["cost · source = ESTIMATED<br/><i>the normal path — custom deployment ids<br/>are in no public cost map</i>"]
    E -->|no| B{"did the call consume<br/>ANY billable work?"}

    B -->|"yes — tokens, audio seconds,<br/>images, or billable_work"| UNP["<b>0.00 · source = UNPRICED</b><br/>+ a WARNING naming the role, the model<br/>and every measured unit"]
    B -->|no| ZERO["0.00 · source = ESTIMATED<br/><i>a genuine, unambiguous zero</i>"]
```

A cost dashboard that cannot tell a hole in its accounting from a free call is a dashboard that
lies. One enum field keeps them apart.

The `billable_work` flag on the third branch exists for exactly one case: a transcription with
no tokens, no reported duration and no caller-supplied duration. Without it, the call that most
needs the warning is the one call that could not trigger it.

---

## 4. What the host injects, and what happens when enforcement fails

*Look at the `FAIL CLOSED` branch — it is what stops a database blip from disabling every cap
in the system.*

```mermaid
flowchart TB
    subgraph HOST["backend — app.core.llm, a strangler shim, runs once at import"]
        CFG["_SettingsGatewayConfig<br/><i>properties, read fresh per call</i>"]
        GOV["_GovernanceHook"]
        OBS["OtelObservabilitySink<br/><i>from aegis.observability</i>"]
    end

    CFG --> CONF["gateway.configure"]
    GOV --> CONF
    OBS --> CONF
    CONF --> GWM["aegis.gateway module state"]

    GOV --> G1["get_context: the contextvar,<br/>None unless a tenant is bound"]
    GOV --> G2["enforce: app.data.governance"]
    GOV --> G3["record: a usage_ledger row"]

    G2 --> FAIL{"the enforcement read raised?"}
    FAIL -->|BudgetExceededError| PROP["propagate — a real breach"]
    FAIL -->|"anything else"| CLOSED["<b>FAIL CLOSED</b>: deny, with<br/>limit_type='enforcement_error'"]
    FAIL -.->|"budget_fail_open=true"| OPEN["allow, and log a warning every time"]
```

Each of the three is injected behind a `Protocol`, so `import aegis.gateway` never drags in a
settings module, a database driver or an OpenTelemetry SDK. The standalone defaults are honest
no-ops — no enforcement, no ledger — and say so in their docstrings.

`_SettingsGatewayConfig` is properties rather than a snapshot, so mutating the settings
singleton at runtime or in a test is honoured on the very next call.

---

## 5. Where the context is bound on a request

*Look at where `set_governance_context` sits: inside the streaming task, not around it.*

```mermaid
flowchart TB
    REQ["POST /query"] --> AUTH["require_auth: decode the JWT"]
    AUTH --> RG["_resolve_governance(auth)"]
    RG --> T{"tenant bound?"}
    T -->|no| EMPTY["an empty GovernanceContext<br/><i>the chokepoint then enforces nothing</i>"]
    T -->|yes| LIM["effective_limits: the user cap<br/>clamped inward to the tenant cap"]

    LIM --> CTXOBJ["GovernanceContext"]
    EMPTY --> CTXOBJ

    CTXOBJ --> GEN["<b>inside the SSE generator task</b><br/>set_governance_context(ctx)"]
    GEN --> RUN["run_agent — every model call<br/>inside the run now sees it"]
    RUN --> FIN["finally: reset_governance_context(token)"]
```

The placement is load-bearing. An SSE generator runs in its own task context, so a context
variable bound *around* the task would not be visible at the chokepoint — every model call in
the run would come back ungoverned, silently, with no error anywhere.

**Next:** [`50-interview.md`](50-interview.md) — the questions you will be asked.
