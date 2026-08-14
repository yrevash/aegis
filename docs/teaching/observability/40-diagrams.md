# Observability — the diagrams

Diagram 2 (the trace tree of one run) is the one to be able to draw on a whiteboard.
Diagram 6 (the three joins) is the one that impresses.

---

## 1. Why a tree, not a stream

```mermaid
flowchart LR
    subgraph LOGS["logs — a flat stream"]
        L1["14:02:01 retrieving..."]
        L2["14:02:01 [other request] guard ok"]
        L3["14:02:02 rerank done"]
        L4["14:02:02 [other request] tool call"]
        L1 --- L2 --- L3 --- L4
    end

    subgraph TRACE["a trace — a tree"]
        R["run"] --> A["guard_input"]
        R --> B["retrieve"]
        B --> B1["embeddings"]
        B --> B2["vector search"]
        B --> B3["rerank"]
        R --> C["generate"]
    end

    LOGS -.->|"no structure<br/>no correlation<br/>no duration<br/>only what someone wrote"| TRACE
```

---

## 2. One agent run, as a trace tree

```mermaid
flowchart TB
    RUN["<b>run</b> — AGENT<br/>2.4s"]

    RUN --> GI["node.guard_input — GUARDRAIL<br/>120ms<br/><i>app.guardrail.verdict</i>"]
    RUN --> RT["node.retrieve — RETRIEVER<br/>890ms<br/><i>input.value, result_count,<br/>candidate_count, cache_hit</i>"]
    RUN --> ML["node.ml_predict — CHAIN<br/>35ms"]
    RUN --> PL["node.plan — CHAIN<br/>610ms"]
    RUN --> GA["node.gate — CHAIN<br/>5ms<br/><i>app.tool.risk</i>"]
    RUN --> AC["node.act — TOOL<br/>310ms<br/><i>tool.name, app.tool.ok</i>"]
    RUN --> GN["node.generate — CHAIN<br/>740ms"]
    RUN --> GO["node.guard_output — GUARDRAIL<br/>90ms"]
    RUN --> ST["node.stream — CHAIN"]

    RT --> E1["embeddings text-embedding-3-large<br/>EMBEDDING — 95ms"]
    RT --> RR["chat gpt-4o-mini<br/>LLM — 240ms<br/><i>the reranker</i>"]
    PL --> P1["chat gpt-4o<br/>LLM — 600ms"]
    GN --> G1["chat gpt-4o<br/>LLM — 730ms"]
```

**Every node span is opened by `_timed`**, and it is the *current* span while the body
runs — which is why the model and retrieval spans nest beneath it with no plumbing.

Node kinds are assigned at wiring time: guardrail nodes are `GUARDRAIL`, `retrieve` is
`RETRIEVER`, tool execution is `TOOL`, the run root is `AGENT`, everything else `CHAIN`.

---

## 3. `_timed` — one node, one pair, one span

```mermaid
flowchart TB
    N["node body"] --> W["<b>_timed(node, label, kind, retry)</b>"]

    W --> E1["emit node_started"]
    E1 --> T0["start = perf_counter()"]
    T0 --> SP["open span(kind, 'node.NAME')<br/>+ app.graph.node, .node.label"]
    SP --> RETRY["<b>_call_with_retry(body, ...)</b><br/><i>the retry is INSIDE the wrapper</i>"]
    RETRY --> DUR["stamp app.graph.node.duration_ms"]
    DUR --> CLOSE["close the span"]
    CLOSE --> E2["emit node_finished(duration_ms)"]

    BAD["<b>the bug</b>: retry via<br/>add_node(retry_policy=...)"] -.->|"re-runs the WRAPPER"| PAIR["node_started<br/>node_started<br/>node_finished<br/><i>an unpaired record with<br/>duration_ms: None, forever</i>"]

    NOTE["<b>act has NO retry</b><br/>retrying tool execution<br/>could issue a refund twice"] -.-> RETRY
```

---

## 4. What a model-call span carries

```mermaid
flowchart TB
    C["gateway complete()"] --> S["<b>span named '{operation} {model}'</b>"]

    S --> REQ["<b>stamped at open</b><br/>openinference.span.kind = LLM<br/>gen_ai.operation.name<br/>gen_ai.provider.name<br/>gen_ai.system <i>(deprecated alias)</i><br/>gen_ai.request.model<br/>gen_ai.request.temperature<br/>gen_ai.request.max_tokens"]

    S --> USE["<b>stamped on return</b><br/>gen_ai.usage.input_tokens<br/>gen_ai.usage.output_tokens<br/>gen_ai.usage.cost <i>(non-standard)</i><br/>gen_ai.response.model"]

    S --> ERR["<b>on exception</b><br/>record_exception + status ERROR<br/>then RE-RAISE"]

    GAP["<b>NOT emitted</b><br/>gen_ai.conversation.id<br/>gen_ai.agent.name / .id<br/>gen_ai.tool.name / .call.id<br/>message-content events<br/>error.type<br/>per-attempt fallback spans"] -.-> S

    FB["request.model != response.model<br/>-> a fallback fired"] --> USE
```

The gaps are worth memorising: the request/response/usage core is there; conversation and
agent identity exist only as `app.*` attributes.

---

## 5. Degradation — why instrumenting aggressively is safe

```mermaid
flowchart TB
    I["init_observability(phoenix_enabled=...)"] --> P{"phoenix enabled?"}
    P -->|no| CON["console SDK provider<br/><b>SimpleSpanProcessor</b><br/><i>synchronous — a batch flush thread<br/>races stdout at teardown</i>"]
    P -->|yes| TRY{"phoenix.otel importable<br/>and registers?"}
    TRY -->|yes| PHX["Phoenix provider, batch=True"]
    TRY -->|"any exception"| CON

    NEVER["<b>init never ran at all</b><br/>(tests, lite mode, import order)"] --> GLOBAL["get_tracer() resolves against<br/>OTel's global <b>no-op</b> provider"]
    GLOBAL --> NR["span() -> a NON-RECORDING span<br/>set_attribute -> a safe no-op<br/>no network, no error"]

    NR --> WHY["<i>this is what makes it acceptable<br/>to instrument everywhere —<br/>instrumentation guarded by<br/>'if tracing_enabled' gets deleted</i>"]
```

---

## 6. The three joins on one trace id

```mermaid
flowchart TB
    RUN["one agent run<br/><b>trace_id = 4f2a...</b>"] --> SP["<b>spans</b><br/>what the system DID<br/>-> Phoenix"]
    RUN --> LED["<b>usage_ledger rows</b><br/>what it COST<br/>tenant, user, model, tokens,<br/>audio_seconds, images, cost_usd<br/><i>trace_id, indexed</i>"]
    RUN --> AUD["<b>audit_log row</b><br/>who AUTHORISED it<br/>action, actor, model, approved_by<br/><i>trace_id, indexed</i>"]
    RUN --> EV["<b>AG-UI event stream</b><br/>what the user SAW<br/><i>trace_id on every event</i>"]

    SP --- J
    LED --- J
    AUD --- J
    EV --- J
    J{{"joinable on trace_id"}}

    Q["<i>'who approved this refund,<br/>what did it cost,<br/>and what did the system<br/>actually do to get there?'</i>"] --> J
```

Without a shared id these are four accounts of the same event that can never be
reconciled.

---

## 7. The latency window — and exactly what it is not

```mermaid
flowchart TB
    RUN["a completed run"] --> SUM["run_summary(events)['nodes']"]
    SUM --> REC["record_run_latency(nodes)"]

    REC --> CO["_coerce_run"]
    CO --> SKIP["skip missing / None / non-numeric /<br/>NaN / inf durations<br/><i>e.g. a paused approval node</i>"]
    CO --> PAIRS["(node, duration_ms) pairs"]

    PAIRS --> WIN["<b>deque(maxlen=512)</b><br/>under a threading.Lock"]

    WIN --> SNAP["_snapshot_window<br/><i>copy under the lock</i>"]
    SNAP --> CALC["per-node p50 / p95 / max / count<br/>+ run percentiles"]

    CALC --> E{"window empty?"}
    E -->|yes| HON["<b>empty: true</b>, None percentiles<br/><i>never zeros — 0.0 reads as<br/>'very fast', not 'no data'</i>"]
    E -->|no| OUT["LatencySummary<br/>+ source + window_capacity"]

    NOT1["per-PROCESS — 4 workers,<br/>4 windows, 4 different p95s"] -.-> WIN
    NOT2["VOLATILE — resets on restart"] -.-> WIN
    NOT3["run duration = SUM of node durations<br/><b>over-counts on fan-out</b><br/><i>chosen so it matches<br/>run_summary exactly</i>"] -.-> CALC
```

---

## 8. Where the tree comes from — context propagation

```mermaid
flowchart TB
    CV["a contextvar holding<br/>the CURRENT span"] --> NEW["start_as_current_span:<br/>1. create with current as parent<br/>2. set as current<br/>3. restore on exit"]

    NEW --> OK1["<b>await inside the block</b><br/>same task, same context -> correct"]
    NEW --> OK2["<b>create_task inside the block</b><br/>the task COPIES the context<br/>at creation -> correct parent"]
    NEW --> BAD["<b>a task created BEFORE the span</b><br/>snapshotted an older context<br/>-> its spans attach elsewhere"]

    BAD --> FLAT["<i>this is why background work<br/>shows up as a separate trace</i>"]

    NEW --> DIST["across a process boundary:<br/>W3C traceparent header"]
```

---

## 9. The claims a trace can actually prove

```mermaid
flowchart TB
    subgraph P1["'the image was screened BEFORE the model saw it'"]
        A1["hygiene"] --> A2["injection screen"] --> A3["image PII"] --> A4["vision model"]
        A5["a pipeline that called the model<br/>and then decided cannot<br/>produce this ordering"] -.-> A2
    end

    subgraph P2["'no model output reached the user unguarded'"]
        B1["generate<br/><i>non-streaming ON PURPOSE</i>"] --> B2["guard_output"] --> B3["stream<br/><i>paces an already-guarded string</i>"]
    end

    subgraph P3["'the gate fired on TOOL RISK, not confidence'"]
        C1["node.gate span<br/>app.tool.risk = high"] --> C2["approval"]
        C3["node.ml_predict"] -.->|"<b>no edge</b>"| C1
    end
```

The third one was a live defect in the **console**, not the code: it hardcoded a 9-node DAG
drawing the human gate branching off ML, and could not light 7 of the real nodes. The
topology is now served from the real compiled graph, with a test that fails if the offline
snapshot drifts.

**A diagram that disagrees with the system is worse than no diagram, because people
believe it.**

---

**Next:** [`50-interview.md`](50-interview.md).
