# ML — our exact implementation

The package is `aegis/src/aegis/ml/`:

| File | Lines | What it owns |
|---|---|---|
| `__init__.py` | 229 | Public surface; `get_model` and the **no third step** |
| `types.py` | 140 | Pydantic response shapes — **no numpy, no sklearn** |
| `spec.py` | 175 | `MLSpec` Protocol, `ResolvedSpec`, lenient `resolve_spec` |
| `dataset.py` | 127 | Frame resolution + the synthesiser |
| `model.py` | 939 | `TrustworthyModel` — ensemble + MAPIE + SHAP |
| `stream.py` | 121 | AG-UI streaming wrapper |

Plus the host layer at `backend/src/app/ml/` (`__init__.py`, `__main__.py`, `spec.py`,
`model.py`, `dataset.py`), which injects the domain spec and owns the artifact path.

---

## How you import it

Two ways, and the second is the one that matters for a domain-agnostic platform.

**Default / module-level singleton** (`__init__.py:15-25`):

```python
from aegis.ml import train, predict_explain

train(spec, frame, path="aegis/ml/artifacts/ml_spine.joblib")   # offline, once
resp = predict_explain({"priority": "urgent", "queue_depth_at_open": 12})
resp.conformal_interval      # calibrated bounds (requested coverage)
resp.conformal_confidence    # the coverage rate that was *requested*
resp.shap_attribution        # signed per-feature contributions
resp.data_source             # 'provided' | 'spec_provider' | 'synthetic'
resp.imputed_features        # what the caller did NOT supply
```

**Bring your own spec** (`__init__.py:33-46`):

```python
from aegis.ml import ResolvedSpec, TrustworthyModel

spec = ResolvedSpec(
    features=["age", "region", "tenure"],
    target="churned",
    task="classification",
    categorical_features=["region"],
)
model = TrustworthyModel.train(spec, frame=my_dataframe, path=None)
resp  = model.predict_explain({"age": 41, "region": "emea", "tenure": 3})
card  = model.model_card()
```

**Import weight is deliberately managed.** `__init__.py` imports nothing heavy at load
time — `aegis.ml.model` (xgboost / sklearn / mapie / shap / pandas) is imported inside
each function body, and `TrustworthyModel` is resolved via `__getattr__` at
`__init__.py:98-115`. That keeps `import aegis.ml.types` free of the whole ML stack, which
`tests/ml/test_types_is_dep_free.py` pins.

---

## The types (`types.py`)

Pydantic and nothing else (`types.py:1-10`), so the light backend API schema layer can
depend on these shapes.

**`MLModelUnavailableError`** (`types.py:21`) — "no trained model is available to serve."
Its docstring states the reason it exists: *"a caller that gets no model must be able to
tell that apart from a caller that got a real one."*

**`ShapFeature`** (`types.py:31`) — `feature`, `value`, `value_label`, `contribution`.
`value_label` exists because a categorical's numeric `value` is the one-hot active
indicator `1.0`, which names no level. A bare `region = 1.0` never says *which* level
drove the answer; `value_label` carries `"emea"`.

**`MLExplainResponse`** (`types.py:49`) — `prediction`, `conformal_interval`,
`conformal_confidence`, `interval_width`, `prediction_set_size`, `shap_attribution`, plus
the three honesty fields: `data_source` (`:66`), `imputed_features` (`:70`),
`unknown_features` (`:74`). The docstring at `:51-57` is explicit that downstream code
*"must be able to discount the evidence on those signals alone."*

**`ModelCard`** (`types.py:88`) — the two coverage fields are the thing to notice:

- `conformal_coverage` (`:111`) — *"REQUESTED marginal coverage — the level asked for, not
  a measurement."*
- `conformal_coverage_empirical` (`:126`) — *"MEASURED coverage ... on the held-out test
  split; None when no test split was held out."*

Plus `metric_name` / `metric_value` (`:133`, `:137`), `test_size` (`:123`),
`calibration_size`, `training_size` and `data_source` (`:119`).

---

## The spec (`spec.py`)

`MLSpec` (`spec.py:30`) is a `runtime_checkable` Protocol requiring only `features` and
`target`. `ResolvedSpec` (`spec.py:46`) is the concrete frozen dataclass: `features`,
`target`, `task`, `categorical_features`, `frame_provider`. `numeric_features` (`:67`) is
derived as "features not marked categorical".

`resolve_spec(spec)` (`spec.py:98`) reads an injected object **leniently** so a real
domain contract is never silently dropped: features from `FEATURE_NAMES` or lowercase
`features`; target and task from a `TARGET` object with `.name`/`.task`, or lowercase
attributes; categoricals from `CATEGORICAL_FEATURES` or derived from a `FEATURES` list of
specs with `.name`/`.dtype` (`_resolve_categorical`, `:158`); the frame from a
`training_frame` callable.

It is **idempotent** (`:125-126`): an already-resolved spec is returned unchanged, because
`TrustworthyModel.train` re-resolves and the adapter-shaped reader would otherwise drop a
`ResolvedSpec`'s own lowercase fields.

`FALLBACK_SPEC` (`spec.py:74`) is four generic numeric features and a synthesised target.
**Where this lands is the whole subject of the honesty story below.**

---

## The data (`dataset.py`)

`resolve_training_frame(spec, frame, ...)` (`dataset.py:96`) in priority order: an explicit
caller frame (validated for required columns, `:119-121`), then the spec's own
`frame_provider` (`:124-125`), then the synthesiser (`:127`).

`synthesise_frame(spec, ...)` (`dataset.py:39`) generates a deterministic learnable frame:
numeric features are standard normal, the target is a fixed linear combination plus mild
Gaussian noise, thresholded at the median for classification.

**Categoricals are synthesised as genuine strings** — `SYNTHETIC_LEVELS` (`dataset.py:24`)
is `("alpha", "bravo", "charlie", "delta")`, and the docstring at `:25-31` says why:
emitting floats there would fit the encoder on one degenerate level per row, and every
real inference row would then be an all-zero block the model never saw in training. That
was a real bug (see [`30-deep-dive.md`](30-deep-dive.md)).

---

## `TrustworthyModel` (`model.py`)

The dataclass is at `model.py:221`, fields at `:267-286`. Beyond the fitted objects it
stores `training_n`, `calibration_n`, `test_n`, `data_source`, `metric_name`,
`metric_value` and `empirical_coverage` — the model card is read off *this*, never
hardcoded.

### The estimator reshape point

`_regression_members(random_state)` (`model.py:114`) and `_classification_members`
(`model.py:125`) are the **single edit point** for the ensemble
(`model.py:108-113`). Currently XGBoost + sklearn `HistGradientBoosting{Regressor,
Classifier}`. Hyper-parameters at `_XGB_PARAMS` (`:91`) and `_HGB_PARAMS` (`:101`):
200 estimators, depth 4, lr 0.1, `n_jobs=1`, `tree_method="hist"` — CPU-only and
deterministic.

`_build_estimator` (`model.py:549`) wraps them in a `VotingRegressor`, or a
`VotingClassifier` with **soft** voting (`:566-571`).

### Training — `TrustworthyModel.train` (`model.py:290`)

1. **Resolve the spec and label the provenance honestly** (`:335-344`): explicit frame →
   `"provided"`; spec provider → `"spec_provider"`; else → `"synthetic"`. This mirrors
   `resolve_training_frame`'s own priority, so the label cannot drift from the source.
2. **Fit the preprocessor on the full feature vocabulary** (`:351`). `_build_preprocessor`
   (`:528`) is a `ColumnTransformer` — `OneHotEncoder(handle_unknown="ignore",
   sparse_output=False)` for categoricals, passthrough for numerics. Fitting on the whole
   frame leaks no label information because categories are not target-dependent (`:349-350`).
3. **Build the encoded→original map structurally** — `_encoded_parents` (`model.py:141`),
   covered below.
4. **Three-way split** (`:363-373`): first the test split, then the remainder into train
   and calibration. Both splits are **stratified for classification** via `_split`
   (`:441`).
5. **Check the calibration minimum** (`:375-383`). `_min_calibration_rows`
   (`model.py:193`) computes the smallest $n$ for which
   $\lceil (n+1)\cdot\text{level}\rceil \le n$, and an under-sized split raises
   `ValueError` naming the arithmetic.
6. **Fit, conformalise, evaluate** (`:385-392`).
7. **Record medians and modes** for imputation (`:394-402`).
8. **Persist — unless synthetic** (`:426-438`).

### The conformal predictor — `_build_conformal` (`model.py:574`)

MAPIE with `prefit=True`, so it wraps the **already-fitted** ensemble:

```python
clf = SplitConformalClassifier(estimator=..., confidence_level=..., prefit=True)
clf.conformalize(x_cal, y_cal)
```

(`:594-604`). Targeted versions are documented in the module docstring
(`model.py:32-47`): xgboost 3.3, scikit-learn 1.9, mapie 1.4, shap 0.52, with the exact
call shapes and return shapes.

### Measurement — `_evaluate` (`model.py:481`)

Returns `(metric_name, metric_value, empirical_coverage)`, all `None` when there is no
test split (`:508-509`).

- **Classification** (`:511-521`): `predict_set(x_test)`, then count how often the true
  label's membership flag is set. Metric is `accuracy_score`.
- **Regression** (`:522-526`): `predict_interval(x_test)`, then
  `mean((truth >= lower) & (truth <= upper))`. Metric is `r2_score`.

The docstring at `:489-495` states the purpose: *"what makes the model card an audit
artifact rather than an echo of its own configuration."*

### Stratification — `_split` (`model.py:441`)

Stratifies on `y` for classification; on `ValueError` (a class with fewer than 2 rows) it
falls back to a plain split with a **logged warning** (`:474-478`) saying explicitly that
*"conformal sets for a class absent from calibration carry no coverage guarantee."*

The silent invalidation is made loud.

### The encoded→original map — `_encoded_parents` (`model.py:141`)

Derived **structurally** from the fitted preprocessor (`:173-178`): the one-hot block
emits `len(categories_[i])` columns for the *i*-th categorical, in declared order, then
the numeric passthroughs. It then **asserts** the derived layout matches the emitted
column names and raises `ValueError` if not (`:179-184`) — a preprocessor whose shape
changed would make SHAP aggregation wrong, so it fails loudly.

The docstring at `:151-155` names the prefix-matching bug this replaced.

---

## Inference

`predict_explain(features)` (`model.py:668`):

```
_raw_row -> _encode -> (_classify | _regress) -> _attributions -> MLExplainResponse
```

**`_raw_row`** (`model.py:615`) builds one native-typed row and reports what it had to
invent. Categoricals keep their string value (imputed with the training mode); numerics
are coerced to float (imputed with the median), including on a failed coercion
(`:654-658`). It returns `(row, imputed, unknown)` — `unknown` being caller keys that are
not model features (`:659`).

**`_regress`** (`model.py:703`) → `(point, (lower, upper))` from
`conformal.predict_interval`.

**`_classify`** (`model.py:718`) → `(label, None, set_size)`. There is no interval for
classification, but the conformal **set size** is surfaced: a singleton is a confident
call, a non-singleton is genuine ambiguity, an empty set is degenerate (`:721-724`).

**`_attributions`** (`model.py:756`) — the interesting one:

1. Resolve the explained class from the label **actually returned** (`_explained_class`,
   `model.py:737`) rather than re-deriving it from `predict_proba` — the explanation must
   be *of the class shown next to it* (`:740-742`).
2. `flip_binary = classification and n_classes == 2 and class_index == 0` (`:790`).
3. Per member: `shap_values(x)`; 3-D → index by class (`:800-801`); 2-D binary with
   `flip_binary` → **negate** (`:802-803`); weight by the member's voting weight (`:804`).
4. Sum encoded contributions, aggregate to parents via `encoded_parents` (`:807-812`).
5. Build `ShapFeature`s, with `value = 1.0` and a `value_label` for categoricals
   (`:822-825`), sorted by descending absolute contribution (`:830`).

`_member_weights` (`model.py:833`) returns uniform weights when the ensemble carries none.

`_explainers` (`model.py:607`) is a `cached_property` built lazily and **dropped from the
pickle** by `__getstate__` (`model.py:893`), keeping artifacts small and portable.

### The model card — `model_card()` (`model.py:843`)

Every field read off the live model. The docstring at `:853-857` states the invariant:
*"`conformal_coverage` is the level that was **requested**, while
`conformal_coverage_empirical` is the rate **observed** on a held-out test split.
Reporting the request as the achievement is what makes an audit artifact untrustworthy."*

---

## `get_model` — the deliberate absence of a third step

`aegis/src/aegis/ml/__init__.py:181`:

```python
def get_model() -> TrustworthyModel:
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        return load(DEFAULT_ARTIFACT_PATH)
    except FileNotFoundError as exc:
        raise MLModelUnavailableError(msg) from exc
```

Singleton, then artifact, then **stop**. The docstring at `:183-198`:

> *"There is deliberately **no third step**. The previous fallback trained a model on the
> built-in noise synthesiser and served its point prediction, its "90% coverage" interval
> and its `feature_0…3` drivers as if they were calibrated evidence — a caller had no way
> to tell that apart from a real model."*

And `train(...)` (`__init__.py:118`) never auto-persists a synthetic model
(`model.py:426-438`) — only a warning is logged. An explicit `model.save(path)` is still
allowed and deliberate.

---

## The host layer — `backend/src/app/ml/`

**The artifact path is the host's.** `backend/src/app/ml/__init__.py:54-56`:

```python
DEFAULT_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[3] / ".artifacts" / "ml_spine.joblib"
)
```

The comment at `:43-53` explains: `aegis.ml.DEFAULT_ARTIFACT_PATH` resolves **inside the
installed aegis package**, so re-exporting it meant the backend wrote its domain model
into the library. *"A host artifact belongs to the host."*

**`_domain_spec()`** (`backend/src/app/ml/__init__.py:82`) imports `app.adapter.ml_spec`
and raises `MLModelUnavailableError` if it cannot (`:100-107`). It used to return `None`,
which `resolve_spec` reads as "no spec" and answers with `FALLBACK_SPEC` — the noise path.
*"a missing adapter is now an error with a name, not a silent downgrade."*

**`get_model()`** (`backend/src/app/ml/__init__.py:170`) mirrors the library's two-step
resolution with no fallback of its own — the module docstring at `:20-31` records that
this shim used to defeat the library's honesty fix with a train-on-demand fallback of its
own.

**`python -m app.ml`** (`backend/src/app/ml/__main__.py`) is the offline trainer. Note the
comment at `:23-28` and the import at `:29` — it imports `DEFAULT_ARTIFACT_PATH` from
`app.ml`, **never** from `app.ml.model`. That is a fix, not a stylistic preference.

---

## Wiring into the platform

**The endpoint.** `backend/src/app/api/routes.py:986` — `POST /ml/explain`:

- runs the prediction in a worker thread via `asyncio.to_thread` (`:1015`), because it is
  synchronous CPU work — an XGBoost forward pass plus a SHAP explanation, plus the joblib
  load on first call. Inline, it blocked the single event loop for all of that, taking
  every in-flight request and every SSE stream with it (`:995-1000`);
- maps `MLModelUnavailableError` to **503** with the command that fixes it
  (`:1016-1019`), never a fabricated prediction.

**Startup warm-up.** `backend/src/app/main.py:218-224` calls `get_model()` in a worker
thread so the first live query does not pay the load cost. Best-effort — a failure never
blocks the API, *"the ML signal is best-effort, never gating"*.

**The agent.** The ML node is one node in the graph, wired at
`aegis/src/aegis/agent/graph.py:1152`. It never routes; the human gate fires on **tool
risk**. `aegis/src/aegis/agent/events.py::ml_explanation` carries `data_source` and
`imputed_features` onto the evidence the answer cites — the honesty signal travels with
the number.

**Next:** [`30-deep-dive.md`](30-deep-dive.md) — five bugs, each one a way of serving a
number with no signal in it.
