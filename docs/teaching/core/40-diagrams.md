# Core — the diagrams

Diagram 1 is the whole architecture. If you can draw the star and state the three rules,
you can explain why any module in this system is installable on its own.

---

## 1. The Module Contract — a star, not a mesh

```mermaid
flowchart TB
    subgraph CORE["aegis.core — ZERO heavy dependencies"]
        T["types.py<br/>GuardResult, RiskLevel, RunStatus"]
        I["interfaces.py<br/>ChatCompleter, Guardrail"]
        E["events.py<br/>SpanKind, step events"]
        L["lazy.py<br/>require(extra, module)"]
        R["registry.py"]
        C["config.py + health.py"]
        S["stream.py + stream_names.py"]
    end

    G["aegis.guardrails"] --> CORE
    RT["aegis.retrieval"] --> CORE
    ML["aegis.ml"] --> CORE
    GW["aegis.gateway"] --> CORE
    V["aegis.vision"] --> CORE
    VO["aegis.voice"] --> CORE
    F["aegis.forecast"] --> CORE
    M["aegis.memory"] --> CORE

    CORE -.->|"imports nothing internal"| NONE(["no leaf, ever"])

    APP["backend/src/app<br/><b>the composition root</b>"] --> G
    APP --> RT
    APP --> ML
    APP --> GW
    APP --> V
    APP --> VO
    APP --> F
    APP --> M
```

**Three rules.** The core imports nothing internal. A leaf imports only the core. No
leaf-to-leaf imports. Anything two modules must agree on goes into the core — which is
the *only* thing that stops the graph becoming a mesh.

---

## 2. Where the invariant is actually violated

```mermaid
flowchart LR
    MEM["aegis.memory"] -->|"cosine_similarity, RRF fusion,<br/>spotlight, ChromaVectorStore"| RET["aegis.retrieval"]
    GOV["aegis.governance"] -->|"BudgetExceededError"| GWY["aegis.gateway"]

    MEM -.->|"and because __init__.py runs<br/>on ANY submodule import"| ALL["importing aegis.memory pulls<br/>ALL of aegis.retrieval"]

    GOV -.->|"the fix"| MOVE["move the exception into<br/>aegis.core.types —<br/>exactly as RiskLevel and RunStatus<br/>were moved out of app.api.schemas"]

    ALL -.->|mitigated by| LAZY["the heavy backends inside retrieval<br/>(lightrag, neo4j, redis)<br/>are themselves lazy"]
```

**Both are documented rather than smoothed over**, because *"the whole point of honest
infra is not claiming an invariant holds when the code says otherwise."*

What would make it structural: a test that walks the AST of every leaf, collects
`import aegis.<other>`, and asserts the set matches a small explicit allowlist.

---

## 3. `require` — the only sanctioned optional import

```mermaid
flowchart TB
    CALL["require('aegis&#91;forecast&#93;', 'statsforecast')<br/><i>inside a function body</i>"] --> TRY{"importlib.import_module"}

    TRY -->|ok| MOD["the module"]
    TRY -->|ImportError| RAISE["ImportError:<br/>'This feature needs statsforecast.<br/>Run: pip install aegis&#91;forecast&#93;'<br/><b>raise ... from exc</b>"]

    RAISE --> CHAIN["the chain shows the REAL missing<br/>transitive module underneath"]
    RAISE --> FIX["the message carries the COMMAND,<br/>not a category"]

    BAD["try: import x<br/>except ImportError: HAS_X = False"] -.->|"the banned pattern"| HARM
    HARM["1 · a DEPLOYMENT error becomes<br/>a runtime behaviour change<br/>2 · two paths, no signal which ran<br/>3 · unobservable — nothing logs<br/>4 · if x is a CONTROL, a silent downgrade<br/>5 · fails late, in production"]
```

**Placement matters as much as the mechanism.** At module top level the import would be
mandatory and the extra would be an extra in name only.

---

## 4. Structural seams — depend on shapes, not packages

```mermaid
flowchart TB
    subgraph CORE2["aegis.core.interfaces"]
        P["ChatCompleter (Protocol)<br/>async (messages, *, response_format) -> str"]
    end

    G2["aegis.guardrails<br/><i>needs a model</i>"] -->|"type annotation only"| P

    LITE["a litellm wrapper"] -.->|"satisfies structurally"| P
    OAI["an OpenAI wrapper"] -.-> P
    FAKE["a 3-line async test fake"] -.-> P

    P -.->|"the alternative"| ABC["an ABC the implementer<br/>must SUBCLASS —<br/>a dependency edge<br/>in the wrong direction"]

    G2 --> NOTE["no inheritance,<br/>no runtime coupling,<br/>a fake costs 3 lines"]
```

**Not every seam is a Protocol.** A rail is `Callable[[MediaPayload], GuardResult | None]`
because there is nothing keyword-only to express. `VisionAnalyst` *is* a Protocol because
it must return usage as well as text; `TranscribeCallable` is one because it takes a file
handle, not messages.

---

## 5. The streaming spine — one way to emit

```mermaid
flowchart TB
    subgraph MODS["every module"]
        M1["guardrails"]
        M2["retrieval"]
        M3["voice"]
        M4["vision"]
    end

    M1 --> EM
    M2 --> EM
    M3 --> EM
    M4 --> EM

    EM["AegisEmitter<br/><b>owns the wire rules</b>"]

    EM --> W1["camelCase via the encoder"]
    EM --> W2["data: ...\\n\\n framing"]
    EM --> W3["START -> CONTENT -> END"]
    EM --> W4["RUN_STARTED first"]

    EM --> VAL{"custom(name, value)<br/>name in stream_names.ALL?"}
    VAL -->|no| ERR["ValueError:<br/>'add it to aegis.core.stream_names'"]
    VAL -->|yes| SSE["one SSE frame"]

    ALT["fourteen ad-hoc builders"] -.->|"what you get instead"| PROB["one gets the framing wrong ·<br/>ordering rules live in fourteen heads ·<br/>guardrail_verdict vs guardrailVerdict ·<br/>the frontend has no source of truth"]
```

**Why raising on an unknown name matters:** an unregistered name reaching the frontend is
a **silent no-op**. Nothing happens and nothing complains — the hardest class of bug.

---

## 6. Bracketing that cannot be forgotten

```mermaid
flowchart TB
    A["async with emitter.step('retrieve', RETRIEVER)"] --> AE["__aenter__<br/>emit STEP_STARTED<br/>rawEvent = spanKind: RETRIEVER"]
    AE --> BODY["the step's work"]
    BODY --> OK{"raised?"}
    OK -->|no| AX["__aexit__<br/>emit STEP_FINISHED"]
    OK -->|yes| AX
    AX --> DONE(["the finish is emitted either way"])

    BODY -.->|"manual start/finish instead"| FORGET["an exception skips the finish —<br/>the client shows a spinner forever"]
```

---

## 7. The span kind that was inert

```mermaid
flowchart TB
    CS["every call site declares its kind<br/>step('retrieve', SpanKind.RETRIEVER)"] --> ST["_StepScope stores it"]
    ST --> WIRE1["...and nothing put it on the wire"]

    WIRE1 --> SYM["every span looks like a generic CHAIN<br/><i>no error, no warning,<br/>the trace is COMPLETE</i>"]
    SYM --> WORSE["<b>inert instrumentation is worse than none</b><br/>— none is obvious,<br/>inert looks finished"]

    FIX["_raw() -> spanKind, passed as rawEvent<br/>on BOTH frames"] --> TEST["a REGRESSION test asserting on the<br/>DECODED FRAME, not the scope object"]

    TEST --> RULE["for anything with an external consumer —<br/>a wire format, a log line, a metric —<br/><b>assert on the serialised output</b>"]
```

---

## 8. Infra mode — three postures, none silent

```mermaid
flowchart TB
    ENV["AEGIS_MODE"] --> M{"declared mode"}

    M -->|full| F{"every URL set?"}
    F -->|no| RAISE["RuntimeError naming the<br/>missing AEGIS_* variables<br/>+ the lite escape hatch"]
    F -->|yes| FULL(["run on real infra"])

    M -->|lite| LITE(["in-memory, deliberately,<br/>LOUDLY announced"])

    M -->|auto| A{"every URL set?"}
    A -->|no| W1["WARN which are unset"] --> L1(["resolve to LITE"])
    A -->|yes| PROBE["actually probe<br/>redis + postgres + vector store"]
    PROBE -->|"any down"| W2["WARN which one and why"] --> L2(["resolve to LITE"])
    PROBE -->|"all up"| F2(["resolve to FULL"])

    NEVER["silently use RAM<br/>and call it durable"] -.->|"the posture this bans"| WHY["works in every test environment ·<br/>loses data on the first<br/>production restart"]
```

**The footgun:** in `auto`, `settings.mode` is the string `"auto"` **forever**. The
resolved mode is the *return value* of an async probe. Code reading the declared one when
it means the resolved one takes the lite branch on a fully-provisioned box.

---

## 9. Health probes — measured, never guessed

```mermaid
flowchart TB
    RQ["/readyz"] --> P["probe_redis / probe_postgres / probe_vector_store"]

    P --> INJ{"a client was injected?"}
    INJ -->|yes| USE["use it · owned = None"]
    INJ -->|no| BUILD["build one via require() · owned = it"]

    USE --> PING["ping / SELECT 1 / get_collections"]
    BUILD --> PING

    PING --> RES{"answered?"}
    RES -->|yes| UP["status = up"]
    RES -->|"any exception"| DOWN["status = down + the detail<br/><i>a probe REPORTS failure,<br/>never raises</i>"]

    UP --> FIN["finally: close ONLY 'owned'"]
    DOWN --> FIN

    FIN -.->|"close the injected one instead"| BAD1["the caller's shared client is dead"]
    FIN -.->|"close nothing"| BAD2["/readyz is POLLED —<br/>one leaked connection per call<br/>exhausts the pool it reports on"]
```

---

## 10. Imported, not forked

```mermaid
flowchart LR
    subgraph FORK["fork it"]
        F1["copy the repo"] --> F2["delete, edit, diverge"]
        F2 --> F3["upstream fixes never arrive ·<br/>your changes never go back ·<br/>two systems in six months"]
    end

    subgraph IMPORT["import it"]
        I1["pip install pkg&#91;guardrails&#93;"] --> I2["call it from your app"]
        I2 --> I3["upgrades are a version bump ·<br/>your code stays yours"]
    end

    IMPORT --> REQ["requires all three:"]
    REQ --> R1["no domain logic in the package"]
    REQ --> R2["each module installs independently"]
    REQ --> R3["dependencies INJECTED, not imported"]

    R1 --> PAY["retargeting the platform at a<br/>NEW problem = writing one adapter"]
    R2 --> PAY
    R3 --> PAY
```

**The payoff is a capability, not a tidiness benefit:** the difference between "we could
rebuild this for another domain in a few weeks" and "we can retarget it by implementing
one interface."

---

## 11. Isolation, tested per module

```mermaid
flowchart TB
    T1["tests/core/test_core_is_dep_free.py"] --> A1["import aegis.core AND aegis.core.stream<br/>in a SUBPROCESS<br/>assert 10 banned modules absent"]
    T2["tests/voice/test_isolation.py"] --> A2["no litellm/torch/numpy/pandas<br/>no app.*<br/>+ the whole guarded path RUNS<br/>on the base install"]
    T3["tests/vision/test_isolation.py"] --> A3["+ no torch/transformers/timm<br/>+ no PIL<br/><i>'a policy that is not tested<br/>is folklore'</i>"]
    T4["tests/forecast/test_isolation.py"] --> A4["types + series import with<br/>no statsforecast/pandas/numpy"]

    A1 --> WHY["<b>subprocess is required</b>:<br/>another test in the same process<br/>may already have imported the banned<br/>module, and the guard would pass<br/>by accident"]

    A1 --> GAP["these check HEAVINESS,<br/>not TOPOLOGY —<br/>which is why the two leaf-to-leaf<br/>imports were found by reading,<br/>not by CI"]
```

---

**Next:** [`50-interview.md`](50-interview.md).
