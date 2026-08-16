# The core — the diagrams

Five diagrams. The one worth reproducing from memory is **the dependency graph** — if you can
draw the layers and say which edges are allowed, you can explain why any module in this system
installs on its own.

Everything else about this module is in [`10-guide.md`](10-guide.md); a picture is only here when
it shows something prose cannot.

---

## 1. The dependency graph, and the edges that shouldn't exist

*Look at the dashed edges. Every one of them is a leaf importing a sibling.*

```mermaid
flowchart TB
    APP["<b>backend/src/app</b><br/>the composition root —<br/>domain logic, credentials, wiring"]

    subgraph LEAVES["leaf modules — each installs on its own"]
        GR["guardrails"]
        RT["retrieval"]
        GW["gateway"]
        MEM["memory"]
        ML["ml"]
        GOV["governance"]
        VIS["vision"]
        VOI["voice"]
        EV["evals"]
    end

    subgraph BASE["shared bases — pydantic + stdlib only"]
        CORE["<b>aegis.core</b><br/>types · interfaces · events<br/>lazy · registry · config · health · stream"]
        DATA["aegis.data"]
        MEDIA["aegis.media"]
    end

    APP --> LEAVES
    GR --> CORE
    RT --> CORE
    GW --> CORE
    MEM --> CORE
    ML --> CORE
    GOV --> CORE
    VIS --> CORE
    VOI --> CORE
    EV --> CORE

    MEM -.-> RT
    GOV -.-> GW
    VIS -.-> GR
    VOI -.-> GR
    VOI -.-> GW
    EV -.-> RT
    EV -.-> GW

    CORE --> NONE(["imports nothing internal —<br/>no leaf, ever"])
```

Solid edges are the contract: the app composes leaves, leaves depend only on a shared base, the
base depends on nothing internal. Draw only those and it is a star.

The dashed edges are real imports in the tree today, found by walking the AST. The architecture
doc names two of them; there are at least seven among the leaves, plus `ops → evals`,
`redteam → guardrails`, and the composition-layer modules `agent` and `security` which reach into
several siblings by design.

The lesson is not the count. The count was documented, correct when written, and went stale
because the isolation tests check **heaviness** — does importing this pull torch — and never
**topology**.

---

## 2. What happens when a control's library is missing

*Follow the top path to its end state. That is the one this codebase bans.*

```mermaid
flowchart TB
    START["an operator enables the image-PII rail;<br/>the presidio extra is not installed"] --> WHICH{"how does the<br/>code react?"}

    WHICH -->|"try / except ImportError<br/>HAS_PII = False"| S1["the rail becomes a no-op"]
    S1 --> S2["every image reaches the model<br/><b>unredacted</b>"]
    S2 --> S3["the verdict says PASS"]
    S3 --> S4(["nothing logs · the dashboard is green ·<br/>discovered at audit, months later"])

    WHICH -->|"require('aegis&#91;media&#93;', …)"| R1["ImportError:<br/>'This feature needs …<br/>Run: pip install aegis&#91;media&#93;'"]
    R1 --> R2(["loud · local · fixed in fifteen seconds"])

    WHICH -->|"a weaker engine exists<br/>(text PII: regex)"| D1["fall back to regex"]
    D1 --> D2["log WARNING on every selection ·<br/>active_engine() reports which is live ·<br/>AEGIS_PII_ENGINE=presidio makes it raise"]
    D2 --> R2
```

Degradation is not the banned thing. **Silent** degradation is. The bottom path is legitimate
because it announces itself and can be pinned closed; the top path is not, because a deployment
error has become a security downgrade with no evidence anywhere.

---

## 3. One step, two consumers

*Look at where the span kind is attached, and where it is read.*

```mermaid
sequenceDiagram
    autonumber
    participant M as a module
    participant S as _StepScope
    participant E as AegisEmitter
    participant SK as sink (SSE)
    participant UI as the console
    participant OT as the tracer

    M->>S: async with emitter.step("retrieve", RETRIEVER)
    S->>E: STEP_STARTED + rawEvent={spanKind: RETRIEVER}
    E->>SK: one encoded SSE frame
    SK-->>UI: a step row opens
    SK-->>OT: a RETRIEVER span opens

    M->>M: the step's work (may raise)

    M->>S: leave the block
    S->>E: STEP_FINISHED + rawEvent={spanKind: RETRIEVER}
    E->>SK: one encoded SSE frame
    SK-->>UI: the row closes
    SK-->>OT: the span closes
```

**Step 8 happens whether or not the body raised**, because it is emitted from `__aexit__`. With
manual start/finish calls an exception skips it and the console spins forever.

**The `rawEvent` on steps 2 and 7 is the bug that was fixed.** The span kind used to be stored on
the scope object and never put on the wire — every call site declared it correctly, nothing
errored, and every span still rendered as a generic chain. The regression test asserts on the
*decoded frame*, not the scope object, because that is the only boundary the consumer actually
reads.

---

## 4. Where an event name can vanish

*Compare the two checks. One side raises; the other side has nothing.*

```mermaid
flowchart LR
    PY["aegis.core.stream_names<br/><b>ALL — 22 names</b>"] --> C{"custom(name, value)<br/>is_known(name)?"}
    C -->|no| ERR["<b>ValueError</b><br/>'add it to aegis.core.stream_names'"]
    C -->|yes| FRAME["one SSE frame on the wire"]

    FRAME --> TS["web/src/lib/streamNames.ts<br/><b>17 names</b>"]
    TS --> C2{"is the name<br/>in the mirror?"}
    C2 -->|yes| RENDER["rendered"]
    C2 -->|"no — guardrail_media,<br/>voice_chunk, voice_transcript,<br/>vision_screen, vision_analysis"| DROP["<b>silently dropped</b><br/>nothing happens ·<br/>nothing complains"]
```

The server side cannot emit a name it does not know. The client side has no equivalent guard, and
the mirror is five names behind — all five of them live emissions from the media, voice and
vision rails.

The file's own header says *"parity is asserted by the count below"*. The count is
`STREAM_NAME_SET.size`, derived from the TypeScript list itself, so it can only count what is
already there. A comment claiming a check is not a check.

---

## 5. Declared mode is not resolved mode

*Look at the two boxes on the right. They are different values, and only one of them is true.*

```mermaid
flowchart TB
    ENV["AEGIS_MODE"] --> M{"declared mode"}

    M -->|lite| L0(["resolved = LITE<br/>in-memory, deliberately, announced"])

    M -->|full| F{"every AEGIS_*_URL set?"}
    F -->|no| RAISE["<b>RuntimeError</b><br/>names the missing variables<br/>+ the lite escape hatch"]
    F -->|yes| F0(["resolved = FULL"])

    M -->|auto| A{"every AEGIS_*_URL set?"}
    A -->|no| W1["WARN which are unset"] --> L1(["resolved = LITE"])
    A -->|yes| PROBE["<b>actually probe</b><br/>redis · postgres · vector store"]
    PROBE -->|"any down"| W2["WARN which one, and why"] --> L2(["resolved = LITE"])
    PROBE -->|"all answered"| F2(["resolved = FULL"])

    F2 -.-> TRAP["<b>settings.mode is still 'auto'</b><br/>on this fully-provisioned box.<br/>`if settings.mode is AegisMode.full`<br/>is False — forever."]
```

`resolve_mode()` is `async` because probing is I/O, so the resolved mode is a **return value**,
not a field. It exists only where a caller kept it.

Both values are deliberately preserved: *"the operator asked for auto and we resolved to lite"* is
a more useful statement than *"the mode is lite"*. The cost is that callers must know which of
the two they are holding.

**Next:** [`50-interview.md`](50-interview.md) — the questions you'll be asked.
