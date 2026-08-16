# ML — the diagrams

Five diagrams. The two worth reproducing from memory are **the three splits** and
**`get_model`'s missing third step** — between them they carry the whole argument about
what this module is allowed to say.

Everything else is explained in [`10-guide.md`](10-guide.md); a picture is only here when
it shows something prose cannot.

---

## 1. Where ML sits in a run

*Look at the dotted arrow into `gate`. It is a **non**-edge, and it is the design.*

```mermaid
flowchart TB
    RT["retrieve"] --> ML["<b>ml_predict</b><br/>one graph node"]
    ML --> PLAN["plan"]
    PLAN --> GATE{"<b>gate</b><br/>decides on TOOL RISK"}
    GATE -->|"below the ceiling"| ACT["act"]
    GATE -->|"at or above it"| HUMAN["pause for a human"]
    HUMAN --> ACT
    ACT --> GEN["generate"]

    ML -.->|"prediction, interval and drivers<br/>injected as EVIDENCE"| GEN
    ML -.->|"<b>no edge — ever</b>"| GATE
```

The prediction reaches the answer. It never reaches the decision to stop.

That is what makes a failed prediction harmless: `ml_predict` swallows the exception,
returns `{}`, and the run answers with zero ML rather than failing.

---

## 2. Training, end to end

*Look at the two places it refuses: too few calibration rows, and a synthetic model asking
to be saved.*

```mermaid
flowchart TB
    SPEC["resolve_spec"] --> SRC{"where does the frame<br/>come from?"}
    SRC -->|"passed in"| P1["data_source = provided"]
    SRC -->|"the spec's provider"| P2["data_source = spec_provider"]
    SRC -->|neither| P3["data_source = <b>synthetic</b><br/>the noise synthesiser"]

    P1 --> ENC
    P2 --> ENC
    P3 --> ENC

    ENC["fit the preprocessor on the<br/>FULL feature vocabulary"] --> PAR["derive encoded to parent map<br/>from the fitted layout, then assert it"]
    PAR --> SPLIT["split three ways<br/>stratified for classification"]

    SPLIT --> MIN{"calibration rows enough<br/>for the requested level?"}
    MIN -->|no| ERR(["<b>ValueError</b> naming the arithmetic —<br/>4 rows, 90 percent needs 9"])
    MIN -->|yes| FIT["fit the ensemble on TRAIN"]

    FIT --> CONF["conformalise on CALIBRATION"]
    CONF --> EVAL["evaluate on TEST:<br/>metric + <b>empirical coverage</b>"]

    EVAL --> SAVE{"a path was given?"}
    SAVE -->|"yes, and not synthetic"| W(["save the artifact"])
    SAVE -->|"yes, but SYNTHETIC"| REF(["<b>refuse</b>, warn, return unsaved"])
    SAVE -->|no| SKIP(["return unpersisted"])
```

The preprocessor is fitted on the whole frame on purpose: one-hot categories are not
target-dependent, so there is no label leakage, and every level the data contains gets a
column.

The refusal on the right is the entire §10 bug closed at its source — a persisted noise
model is reloaded by every process, forever.

---

## 3. The three splits, and the number each one earns

*Look at what TEST is touched by: nothing. That is why its coverage number can disappoint
you.*

```mermaid
flowchart LR
    ALL["all labelled rows"] --> T["<b>TEST</b><br/>held out first"]
    ALL --> REST["the remainder"]
    REST --> TR["<b>TRAIN</b><br/>fits the ensemble"]
    REST --> CAL["<b>CALIBRATION</b><br/>fits the conformal quantile"]

    TR --> M["the model"]
    CAL --> QQ["the half-width q"]

    M --> T
    QQ --> T

    T --> R1["accuracy or R-squared"]
    T --> R2["<b>empirical coverage</b>"]

    BAD["calibrating on TRAIN rows"] -.->|"the model has seen them —<br/>residuals are optimistic,<br/>the guarantee is void"| CAL
```

Two splits give you a model and an interval. The third is the only one that can tell you
the interval did not do what you asked for.

> Requested 0.9 and achieved 0.76 are different facts. Report one as the other and you
> have printed your configuration and called it a result.

---

## 4. Conformal prediction, mechanically

*Look at the rank check — that is where an unattainable level gets caught before it can
serve anything.*

```mermaid
flowchart TB
    subgraph CALIB["calibration, once"]
        C1["absolute error on each<br/>calibration row"] --> C2["sort the errors"]
        C2 --> C3["q = the k-th smallest,<br/>k = ceil of n+1 times c"]
        C3 --> C4{"is k within n?"}
        C4 -->|no| IMP(["<b>the level is unattainable</b><br/>no such rank exists"])
        C4 -->|yes| OK["q is the half-width"]
    end

    subgraph SERVE["per prediction"]
        S1["yhat from the ensemble"] --> S2["interval spans yhat minus q<br/>to yhat plus q"]
        S2 --> S3["coverage at least c —<br/><b>MARGINAL, not conditional</b>"]
    end

    OK --> S2

    EX["holds by EXCHANGEABILITY alone —<br/>no distributional assumption"] -.-> S3
    BRK["shuffling a time series,<br/>or distribution shift"] -.->|"voids it, invisibly"| EX
```

Every prediction gets the **same** half-width, because the score is a plain absolute
residual. Split conformal cannot say "this row is harder than that one"; CQR is the
upgrade if it ever needs to.

For classification the analogue is a prediction **set**: a singleton is a confident call,
a two-label set is genuine ambiguity, an empty set is degenerate.

---

## 5. `get_model`, and the third step that was deleted

*Look at the dashed chain on the right — that is what the deleted step did, in order.*

```mermaid
flowchart TB
    G["get_model"] --> S1{"in-process singleton?"}
    S1 -->|yes| R1(["return it"])
    S1 -->|no| S2{"artifact at the HOST path?"}
    S2 -->|yes| R2(["load and cache"])
    S2 -->|no| STOP["<b>MLModelUnavailableError</b>"]

    STOP --> EP["/ml/explain returns 503<br/>with the command that fixes it"]
    STOP --> AG["the agent's ML node<br/>omits the evidence"]

    OLD["<b>the removed third step</b><br/>train one on demand"] -.-> NOISE["no spec means FALLBACK_SPEC,<br/>which is the noise synthesiser"]
    NOISE -.-> SERVED["a prediction, a 90 percent interval,<br/>and drivers named feature_0 to feature_3 —<br/>served as domain evidence"]
    SERVED -.-> PERSIST["<b>and it was written to disk</b><br/>so every later process loads it<br/>at step 2, forever"]
```

Nothing on that dashed chain is broken. The conformal machinery worked perfectly — on
noise.

Refusing is only safe **because** ML never gates. Omitting evidence degrades an answer;
serving fake evidence corrupts a decision.

**Next:** [`50-interview.md`](50-interview.md).
