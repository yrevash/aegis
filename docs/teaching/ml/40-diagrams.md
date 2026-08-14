# ML — the diagrams

Diagram 3 (the three splits) and diagram 7 (ML informs, risk gates) are the two worth
being able to draw cold.

---

## 1. Where ML sits in a run

```mermaid
flowchart TB
    Q["user turn"] --> G["guard_input"]
    G --> RT["retrieve"]
    RT --> ML["<b>ml_predict</b><br/>one graph node"]
    ML --> PLAN["plan"]
    PLAN --> GATE{"<b>gate</b><br/>tool RISK TIER"}
    GATE -->|"low risk"| ACT["act"]
    GATE -->|"high risk"| HUMAN["pause for a human"]
    ACT --> GEN["generate"]
    HUMAN --> ACT

    ML -.->|"evidence injected<br/>into the answer context"| GEN
    ML -.->|"NEVER an edge into gate"| GATE

    style GATE fill:#fff,stroke:#333,stroke-width:3px
```

**The dotted non-edge is the design.** There is no path from the ML node to the gate. The
human stop is decided by the risk tier of the tool being called, never by how confident
the model felt.

Consequence: a failed or absent prediction is **not** a failure. The evidence is omitted
and the run continues.

---

## 2. Training

```mermaid
flowchart TB
    SPEC["resolve_spec(spec)<br/><i>lenient: FEATURE_NAMES or features,<br/>TARGET.name or target, ...</i>"] --> SRC{"where does the<br/>frame come from?"}

    SRC -->|"explicit frame"| P1["data_source = 'provided'"]
    SRC -->|"spec.frame_provider"| P2["data_source = 'spec_provider'"]
    SRC -->|"neither"| P3["data_source = <b>'synthetic'</b><br/><i>the noise synthesiser</i>"]

    P1 --> ENC
    P2 --> ENC
    P3 --> ENC

    ENC["fit the preprocessor on the FULL vocabulary<br/>one-hot categoricals, pass numerics through<br/><i>no label leakage: categories are<br/>not target-dependent</i>"]
    ENC --> PAR["_encoded_parents<br/><i>derived STRUCTURALLY from the fitted<br/>column layout, then asserted</i>"]

    PAR --> SPLIT["three-way split<br/><i>stratified for classification</i>"]
    SPLIT --> MIN{"calibration rows >=<br/>_min_calibration_rows(level)?"}
    MIN -->|no| ERR(["ValueError naming the arithmetic —<br/>5 rows but 90 percent needs at least 9"])
    MIN -->|yes| FIT["fit the soft-voting ensemble<br/>on TRAIN only"]

    FIT --> CONF["MAPIE SplitConformal(prefit=True)<br/>.conformalize(x_cal, y_cal)"]
    CONF --> EVAL["_evaluate on TEST:<br/>r2 / accuracy + <b>empirical coverage</b>"]

    EVAL --> SAVE{"path given?"}
    SAVE -->|"yes, and data_source<br/>!= 'synthetic'"| W["save the artifact"]
    SAVE -->|"yes, but SYNTHETIC"| REF["<b>refuse</b> + log a warning<br/><i>a persisted noise model is<br/>reloaded forever after</i>"]
    SAVE -->|no| SKIP["return unpersisted"]
```

---

## 3. The three splits, and which number each one earns

```mermaid
flowchart LR
    ALL["all labelled rows"] --> T["<b>TEST</b><br/>held out first"]
    ALL --> REST["the remainder"]
    REST --> TR["<b>TRAIN</b><br/>fits the ensemble"]
    REST --> CAL["<b>CALIBRATION</b><br/>fits the conformal quantile"]

    TR --> M["the model"]
    CAL --> QQ["the interval half-width q"]

    M --> T
    QQ --> T

    T --> R1["accuracy / R-squared<br/><i>a measurement</i>"]
    T --> R2["<b>empirical coverage</b><br/><i>the only coverage number<br/>that can disappoint you</i>"]

    BAD["calibrating on TRAIN rows"] -.->|"the model has seen them —<br/>residuals are optimistic,<br/>the guarantee is void"| CAL
    BAD2["reporting the REQUESTED level<br/>as if measured"] -.->|"prints your configuration<br/>and calls it a result"| R2
```

---

## 4. Conformal prediction, mechanically

```mermaid
flowchart TB
    subgraph CALIB["calibration, once"]
        C1["for each calibration row:<br/>residual = absolute error of y vs yhat"] --> C2["sort the residuals"]
        C2 --> C3["q = the ceil((n+1)(1-alpha))-th smallest"]
        C3 --> C4{"is that rank &lt;= n?"}
        C4 -->|no| IMP["<b>the level is unattainable</b><br/>no finite quantile exists"]
        C4 -->|yes| OK["q is the half-width"]
    end

    subgraph SERVE["per prediction"]
        S1["yhat from the ensemble"] --> S2["interval spans yhat - q to yhat + q"]
        S2 --> S3["guarantee: coverage >= 1 - alpha<br/><i>MARGINAL, not conditional</i>"]
    end

    OK --> S2

    EX["holds because of EXCHANGEABILITY —<br/>no distributional assumption at all"] -.-> S3
    BRK1["shuffling a time series<br/>leaks the future into calibration"] -.->|"voids it, invisibly"| EX
    BRK2["distribution shift"] -.->|"degrades it, silently"| EX
```

For classification the analogue is a **prediction set**: a singleton is a confident call,
a two-label set is genuine ambiguity, an empty set is degenerate.

---

## 5. SHAP attribution — and the two bugs on this path

```mermaid
flowchart TB
    X["one ENCODED row"] --> CLS{"classification?"}
    CLS -->|no| REG["values = shap_values(x)<br/>2-D, regression"]
    CLS -->|yes| IDX["_explained_class:<br/>index of the label ACTUALLY RETURNED<br/><i>not re-derived from predict_proba</i>"]

    IDX --> ND{"shap_values ndim?"}
    ND -->|"3-D, multiclass"| SEL["values = values[:, :, class_index]"]
    ND -->|"2-D, binary"| FLIP{"predicted class 0?"}
    FLIP -->|yes| NEG["<b>values = -values</b><br/><i>the binary margin is always<br/>toward class 1</i>"]
    FLIP -->|no| ASIS["use as-is"]

    REG --> W
    SEL --> W
    NEG --> W
    ASIS --> W

    W["weight by each member's<br/>voting weight and sum"] --> AGG["aggregate encoded columns<br/>to their PARENT feature"]
    AGG --> OUT["one ShapFeature per original feature,<br/>sorted by absolute contribution"]

    B1["<b>bug</b>: no flip -> every driver's<br/>sign reads backwards beside<br/>the prediction"] -.-> NEG
    B2["<b>bug</b>: parents matched by NAME PREFIX<br/>-> plan_age folded into plan,<br/>and plan_age reported 0.0"] -.-> AGG
```

`_encoded_parents` is derived structurally from the fitted preprocessor's layout and then
**asserted** against the emitted column names, so a shape change fails loudly instead of
silently mis-aggregating.

---

## 6. `get_model` — the deliberate absence of a third step

```mermaid
flowchart TB
    G["get_model()"] --> S1{"in-process singleton?"}
    S1 -->|yes| R1([return it])
    S1 -->|no| S2{"persisted artifact<br/>at the HOST path?"}
    S2 -->|yes| R2([load and cache])
    S2 -->|no| STOP["<b>MLModelUnavailableError</b>"]

    STOP --> EP["/ml/explain -> 503<br/>with the command that fixes it"]
    STOP --> AG["the agent's ML node<br/>simply omits the evidence"]

    OLD["<b>the removed third step</b><br/>train on demand"] -.-> NOISE["no spec -> FALLBACK_SPEC<br/>-> the NOISE synthesiser"]
    NOISE -.-> SERVED["a prediction, a '90% coverage'<br/>interval, and feature_0..3 drivers,<br/>served as domain evidence"]
    SERVED -.-> PERSIST["<b>and it was PERSISTED</b><br/>-> every later process loads it<br/>at step 2, forever"]
```

Safe to refuse **because** ML never gates. Omitting evidence degrades an answer; serving
fake evidence corrupts a decision.

---

## 7. Why the gate is on tool risk, not model confidence

```mermaid
flowchart TB
    subgraph WRONG["confidence gating — the intuitive design"]
        W1["model confidence"] --> W2{"below threshold?"}
        W2 -->|yes| W3["stop for a human"]
        W2 -->|no| W4["proceed autonomously"]
        W5["<b>fails exactly when it matters</b>:<br/>out-of-distribution, adversarial and<br/>novel inputs all produce HIGH confidence<br/>with no grounding"] -.-> W4
    end

    subgraph RIGHT["risk gating — what Aegis does"]
        R1["the TOOL being called"] --> R2{"risk tier?"}
        R2 -->|"low, e.g. read a record"| R4["proceed autonomously"]
        R2 -->|"high, e.g. issue a refund"| R3["stop for a human"]
        R6["a property of the ACTION —<br/>a fact, not an artefact of a model"] -.-> R2
    end

    ML["the ML prediction,<br/>interval and drivers"] --> EV["injected as EVIDENCE<br/>into the answer context"]
    ML -.->|no edge| R2
```

The one-liner: *a model that is 99% confident about issuing a $4,200 refund still stops
for a human, because refunds are high-risk — not because the model was unsure.*

---

## 8. The honesty signals on a response

```mermaid
flowchart TB
    REQ["predict_explain(features)"] --> ROW["_raw_row"]

    ROW --> K{"for each model feature"}
    K -->|"caller supplied it"| USE["use the value"]
    K -->|"missing or uncoercible"| IMP["impute: median (numeric)<br/>or mode (categorical)<br/><b>and record it</b>"]

    ROW --> UNK["caller keys that are NOT<br/>model features -> <b>unknown_features</b>"]

    USE --> RESP
    IMP --> RESP
    UNK --> RESP

    RESP["MLExplainResponse"] --> H1["<b>data_source</b><br/>provided / spec_provider / synthetic"]
    RESP --> H2["<b>imputed_features</b>"]
    RESP --> H3["<b>unknown_features</b>"]

    H1 --> UI["the UI and downstream code<br/>discount the evidence<br/>on these signals alone"]
    H2 --> UI
    H3 --> UI

    TRAP["a caller who mistypes EVERY feature name<br/>gets a fully confident answer about<br/>the MEDIAN TRAINING ROW"] -.->|"visible only<br/>because of H2/H3"| IMP
```

---

## 9. The artifact-path split

```mermaid
flowchart TB
    LIB["aegis.ml.DEFAULT_ARTIFACT_PATH<br/><i>inside the installed package</i>"] --> LIBUSE["the library's own default"]

    HOST["app.ml.DEFAULT_ARTIFACT_PATH<br/><i>backend/.artifacts/ — gitignored</i>"] --> HOSTUSE["what app.ml.get_model() READS"]

    OLD["<b>bug 1</b>: the host re-exported<br/>the LIBRARY constant"] -.->|"the backend wrote its<br/>domain model INTO the library;<br/>a read-only wheel fails outright"| LIB

    OLD2["<b>bug 2</b>, introduced by the fix:<br/>python -m app.ml imported<br/>app.ml.model's constant"] -.->|"training reported SUCCESS<br/>while writing to a directory<br/>the loader never reads —<br/>and /ml/explain stayed 503"| LIB

    FIX["import DEFAULT_ARTIFACT_PATH from app.ml,<br/>never from app.ml.model"] --> HOST
    TEST["<b>a test pins that the training<br/>entrypoint targets exactly what<br/>get_model() reads</b>"] --> HOST
```

The test is the real fix. The path can drift again; the invariant cannot.

---

**Next:** [`50-interview.md`](50-interview.md).
