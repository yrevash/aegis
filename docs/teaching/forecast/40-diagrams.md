# Forecast — the diagrams

Diagram 3 (the constant-series trap) is the one to be able to draw from memory. It is the
best single story in this module.

---

## 1. Refuse first, then fit, then measure

```mermaid
flowchart TB
    A["forecast_series(points, horizon, level)"] --> V{"horizon >= 1?<br/>windows >= 2?<br/>level a whole percent in (0,1)?"}
    V -->|no| E0["ValueError<br/><i>before any work</i>"]

    V -->|yes| N["normalise_points<br/>sort, naive UTC,<br/>duplicates SUM"]
    N --> C{"at least 2 points?"}
    C -->|no| R1["InsufficientHistoryError<br/>cannot infer a frequency"]

    C -->|yes| F["infer_freq — the MODAL gap,<br/>so one outage cannot reclassify"]
    F --> MH["minimum_history(h, season)"]
    MH --> C2{"len >= need?"}
    C2 -->|no| R2["InsufficientHistoryError<br/>have=9, need=71, + the reason"]

    C2 -->|yes| D{"any variation at all?"}
    D -->|no| R3["DegenerateSeriesError<br/><i>see diagram 3</i>"]

    D -->|yes| IMP["require('aegis&#91;forecast&#93;', 'statsforecast')<br/><b>the first heavy import</b>"]
    IMP --> CI["ConformalIntervals(n_windows, h)"]
    CI --> CV["rolling-origin cross_validation<br/>step_size = h, refit = False"]
    CV --> SC["score each candidate:<br/>sMAPE, MAPE, MAE, EMPIRICAL coverage"]
    SC --> ANY{"any scoreable?"}
    ANY -->|no| R4["ForecastFitError<br/>+ each candidate's real exception"]

    ANY -->|yes| SEL["select min sMAPE<br/>losers published, baseline included"]
    SEL --> RF["refit the winner on the FULL history"]
    RF --> FIN{"every value finite?"}
    FIN -->|no| R5["ForecastFitError<br/><i>no partially undefined forecast</i>"]
    FIN -->|yes| OUT["ForecastResult<br/>+ BacktestReport(requested vs empirical)"]
```

**Everything before `IMP` is dependency-free.** A series can be shaped, validated and
refused without paying to import statsforecast at all.

---

## 2. Why a random split is invalid

```mermaid
flowchart TB
    subgraph RAND["random split — INVALID"]
        R1["day 48 · train"]
        R2["day 49 · train"]
        R3["day 50 · TEST"]
        R4["day 51 · train"]
        R5["day 52 · train"]
    end

    R3 -.->|"its neighbours are<br/>in the training set"| INTERP["the model INTERPOLATES<br/>rather than forecasting"]
    INTERP --> SMALL["errors are far too small"]
    SMALL --> NARROW["the conformal quantile is too small<br/>the band is too narrow"]
    NARROW --> VOID["the guarantee is VOID<br/><i>and nothing errors —<br/>the scores look EXCELLENT</i>"]

    subgraph CHRONO["rolling origin — VALID"]
        C1["train 1..t"] --> C2["score t+1..t+h"]
        C3["train 1..t+h"] --> C4["score t+h+1..t+2h"]
        C5["train 1..t+2h"] --> C6["score t+2h+1..t+3h"]
    end

    C2 --> GOOD["every scored point is<br/>strictly AFTER its own model"]
```

**The symptom of leakage is excellent scores, not an error.** That is what makes it
dangerous: a leaking evaluation looks like a triumph until production.

---

## 3. The constant series — every number true, the whole thing a lie

```mermaid
flowchart TB
    LEDGER["a quiet tenant's ledger<br/>140 days, all 0.0<br/><i>gap-filled with REAL zeros</i>"] --> FIT["the fit"]

    FIT --> M1["sMAPE = 0.0%<br/><i>both sides ~0 contributes 0</i>"]
    FIT --> M2["MAE = 0.00"]
    FIT --> M3["conformal band width = 0<br/><i>90th percentile of zeros is zero</i>"]

    M3 --> COV["coverage: is 0.0 inside &#91;0.0, 0.0&#93;?<br/><b>YES</b>, 42 times out of 42"]
    COV --> EMP["empirical_coverage = 1.0"]
    EMP --> MEETS["coverage_meets_request<br/>1.0 >= 0.9 → <b>TRUE</b>"]

    M1 --> DASH
    M2 --> DASH
    MEETS --> DASH
    DASH["the dashboard:<br/>sMAPE 0.0% · achieved 100% · meets request"]

    DASH --> LIE["a BETTER result than any real<br/>forecast will ever produce —<br/>describing the ABSENCE of data"]

    LEDGER --> CHECK{"spread <= 1e-12 x scale?"}
    CHECK -->|yes| REFUSE["DegenerateSeriesError<br/><i>before statsforecast is even imported</i>"]
```

**Why no honesty control caught it.** Requested and achieved were separate fields — both
correct. Coverage was measured, not assumed — it measured 100%. The comparison was strict
— it passed on merits. The losers were published — they scored perfectly too.

Every control answers *"is this number computed correctly?"* None of them asks *"was this
question well-posed?"*

---

## 4. Two kinds of band

```mermaid
flowchart TB
    subgraph PARAM["parametric"]
        P1["the fitted model's own<br/>predictive distribution"] --> P2["closed-form standard errors<br/>ARIMA / ETS"]
        P2 --> P3["assumes: model correct,<br/>errors normal, variance known"]
    end

    subgraph CONF["conformal"]
        C1["forecast on data the model<br/>did NOT train on"] --> C2["collect the errors<br/>(nonconformity scores)"]
        C2 --> C3["take the 90th percentile"]
        C3 --> C4["distribution-free,<br/>finite-sample guarantee"]
        C4 --> C5["...IF calibration and future<br/>are EXCHANGEABLE"]
    end

    P3 --> LABEL1["interval_method = 'parametric'<br/><i>and the detail string never<br/>says 'conformal'</i>"]
    C5 --> LABEL2["interval_method = 'conformal'<br/>+ ConformalIntervals(n_windows, h)"]

    LABEL1 --> RULE
    LABEL2 --> RULE
    RULE["<b>either way the ACHIEVED rate<br/>is measured, never assumed</b>"]

    P3 -.->|"calling this '90% coverage'"| OVER["the overclaim<br/><i>90% is what the model believes<br/>under its own assumptions</i>"]
```

---

## 5. Requested versus achieved

```mermaid
flowchart LR
    REQ["requested_coverage = 0.9<br/><b>an INPUT, echoed back</b>"] --> CMP
    EMP["empirical_coverage = 0.786<br/><b>a COUNT of held-out actuals<br/>that landed inside the band</b>"] --> CMP

    CMP{"strict >=<br/>never rounded"} --> MEETS["coverage_meets_request = FALSE"]

    EMP --> BIG["the console gives the ACHIEVED<br/>rate the big type"]
    REQ --> SMALL["the requested level is demoted<br/>to context"]

    CMP --> GAP["the GAP is the finding —<br/>it tells the reader exactly<br/>how much to discount the band"]

    REQ -.->|"report only this"| BAD["'90% conformal prediction interval'<br/><i>tells the reader nothing except<br/>which button was pressed</i>"]
```

---

## 6. The minimum-history arithmetic

```mermaid
flowchart TB
    H["horizon h = 14<br/>season = 7 (daily)"] --> W["backtest_windows x h<br/>3 x 14 = 42<br/><i>every held-out scoring window</i>"]

    H --> T1["(conformal_windows - 1) x h + 1<br/>2 x 14 + 1 = 29"]
    H --> T2["2 x season + 1<br/>2 x 7 + 1 = 15"]
    H --> T3["2 x h<br/>= 28"]

    T1 --> MAX["max = 29"]
    T2 --> MAX
    T3 --> MAX

    MAX --> SUM["need = 42 + 29 = <b>71</b>"]
    W --> SUM

    SUM --> CMP{"have 9?"}
    CMP -->|no| REF["InsufficientHistoryError<br/>have=9, need=71,<br/>+ where 71 comes from"]

    MAX -.->|"what must remain<br/>BEFORE the earliest cutoff"| WHY["the model fitted at that cutoff<br/>has to be trainable AND<br/>calibratable on that slice alone"]
```

**The refusal carries arithmetic, not adjectives.** "Insufficient history" is a shrug.
"Have 9, need 71" lets the user wait or shorten the horizon.

---

## 7. Backtest failure handling

```mermaid
flowchart TB
    ALL["cross_validation with all 3 candidates"] --> OK{"succeeded?"}
    OK -->|yes| SCORE["score every candidate"]

    OK -->|no| PER["retry MODEL BY MODEL"]
    PER --> L{"for each"}
    L -->|"succeeded"| KEEP["keep its frame"]
    L -->|"raised"| EXC["ExcludedModel(model, reason)<br/><i>the REAL exception text —<br/>'LinAlgError: Singular matrix',<br/>not 'model failed'</i>"]

    KEEP --> MERGE["merge the frames"]
    MERGE --> SCORE

    SCORE --> NF{"produced finite<br/>predictions and bounds?"}
    NF -->|no| EXC2["ExcludedModel<br/>'produced no finite predictions'<br/><i>so a NaN candidate cannot vanish<br/>from BOTH lists</i>"]
    NF -->|yes| CAND["CandidateScore"]

    CAND --> ANY{"any scored?"}
    EXC2 --> ANY
    EXC --> ANY
    ANY -->|no| FIT["ForecastFitError<br/>with EVERY reason joined<br/><i>no naive-line fallback</i>"]
    ANY -->|yes| SEL["select min sMAPE"]
```

**One casualty must not cost the caller its forecast** — and nothing is silently dropped.

---

## 8. Metric definedness

```mermaid
flowchart TB
    PAIRS["(actual, forecast) pairs"] --> Z{"any actual ~ 0?"}

    Z -->|yes| NONE["MAPE = <b>None</b><br/><i>UNDEFINED, not merely large</i>"]
    Z -->|no| MAPE["MAPE = mean of abs(e)/abs(y), x100"]

    NONE -.->|"the trap"| HUGE["returning 4,900% instead<br/>reads as 'this model is terrible'<br/>when the truth is<br/>'this metric does not apply'"]

    PAIRS --> SM["sMAPE = mean of 2·abs(e)/(abs(y)+abs(f)), x100<br/>bounded 0-200 percent"]
    SM --> BOTH{"both sides ~ 0?"}
    BOTH -->|yes| ZERO["contributes 0<br/><i>right about nothing happening</i>"]

    SM --> SELECT["<b>the selection metric</b><br/>because it is defined far more often"]
    ZERO -.->|"and this is exactly what<br/>made the constant series<br/>look perfect"| TRAP["see diagram 3"]
```

---

## 9. The burn-down envelope

```mermaid
flowchart TB
    F["per-step conformal bands<br/>lo/hi, calibrated MARGINALLY"] --> SUM["cumulative_lo = spent + Σ lo<br/>cumulative_hi = spent + Σ hi"]

    SUM --> NOT["<b>NOT a calibrated interval<br/>on the total</b>"]

    NOT --> R1["consecutive forecast errors<br/>are CORRELATED"]
    NOT --> R2["the sum of marginal quantiles<br/>is not the quantile of the sum"]
    NOT --> R3["so it is neither conservative<br/>NOR calibrated"]

    SUM --> DRAW["draw it anyway —<br/>a burn-down chart needs an envelope"]
    DRAW --> FLAG["cumulative_bounds_are_calibrated = FALSE<br/><b>a field, not a footnote</b>"]

    FLAG -.->|"a footnote does not travel<br/>into a JSON payload"| WHY(["a field survives every hop<br/>into the console"])

    SUM --> EXH["first step where cumulative >= cap<br/>→ exhaustion_ts, exhaustion_step"]
```

---

## 10. Refusal as a transport decision

```mermaid
flowchart TB
    CALL["ledger_forecast(...)"] --> E{"which exception?"}

    E -->|InsufficientHistoryError| T1["200 · available=false<br/>refusal=insufficient_history<br/>have / need / reason"]
    E -->|DegenerateSeriesError| T2["200 · available=false<br/>refusal=degenerate_series"]
    E -->|ForecastFitError| T3["200 · available=false<br/>refusal=fit_failed"]
    E -->|ImportError| T4["200 · available=false<br/>refusal=extra_missing"]
    E -->|"anything else"| RERAISE["<b>RE-RAISED</b><br/><i>an unexpected bug must not be<br/>laundered into a tidy refusal</i>"]

    T1 --> UI["RefusalNotice:<br/>the count, the requirement, the reason"]
    T2 --> UI
    T3 --> UI
    T4 --> UI

    UI --> NEVER(["never an empty chart"])

    E -.->|"if these were 5xx"| BAD["the console renders 'backend down'<br/>and the user learns nothing"]
```

**A refusal is a result, not an error.** "This tenant has nine days of ledger and needs
seventy-one" is the most useful thing the surface can say.

---

## 11. Where the heavy import happens

```mermaid
flowchart TB
    subgraph LIGHT["imports with NONE of the stack installed"]
        TY["types.py<br/>pydantic only"]
        SE["series.py<br/>stdlib + types"]
        BU["budget.py<br/>types only"]
        INIT["__init__.py<br/>a lazy wrapper"]
    end

    subgraph HEAVY["imported on FIRST CALL, inside a function body"]
        EN["engine.py"]
    end

    INIT -->|"from aegis.forecast.engine import ..."| EN
    EN -->|"require('aegis&#91;forecast&#93;', 'statsforecast')"| SF["statsforecast + pandas + numpy<br/>numba-compiled, CPU only"]

    SE --> REFUSE["minimum_history() lets a caller<br/>decide whether a forecast is<br/>even offerable — <b>before</b><br/>paying to import the stack"]

    EN -.->|"extra missing"| ERR["ImportError naming<br/>pip install aegis&#91;forecast&#93;"]
```

---

**Next:** [`50-interview.md`](50-interview.md).
