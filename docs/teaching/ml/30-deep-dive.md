# ML — deep dive: five ways to serve a number with no signal in it

Every bug in this module is the same bug wearing a different coat: **a structurally valid
output that means nothing, presented as evidence.** The output is well-formed. The
interval has bounds. The drivers are ranked. And there is nothing in it.

That makes them harder to find than a crash and much more damaging, because the number
gets paraphrased by a language model into a sentence a human acts on.

---

## Bug 1 — the fallback that trained on Gaussian noise, persisted it, and served it forever

### What was happening

`get_model()` resolved in three steps:

```
in-process singleton → persisted artifact → train one on demand
```

Step three called `train(spec, frame=None)`. With no frame, `resolve_training_frame` falls
through to the spec's provider, and with no usable spec at all, `resolve_spec` returns
`FALLBACK_SPEC` — four generic numeric features and a target synthesised from a fixed
linear combination of **standard normal random numbers plus Gaussian noise**.

And it happened on the **backend shim** too, which had its own train-on-demand fallback.
When `app.adapter.ml_spec` could not be imported, `_domain_spec()` returned `None`, which
`resolve_spec` reads as "no spec", which is `FALLBACK_SPEC`, which is the noise
synthesiser.

### What the caller saw

Everything a real model produces:

- a point prediction;
- a **calibrated conformal interval** — genuinely, correctly calibrated, on noise;
- `conformal_confidence: 0.9`;
- SHAP drivers named `feature_0`, `feature_1`, `feature_2`, `feature_3`.

The interval is not fake. The conformal machinery worked perfectly on the data it was
given. That data just had nothing to do with support tickets.

`/ml/explain` and `/ml/model-card` served this as domain evidence, and the agent injected
it into its answer context.

### The part that made it permanent

`train(..., path=DEFAULT_ARTIFACT_PATH)` **wrote the noise model to disk**.

A persisted artifact is loaded by every later process at step two. So a one-off fallback —
triggered by, say, a transient import failure at one unlucky startup — became the
platform's model. Forever. Nothing would ever retrain it, because step two would always
succeed.

### Why "just log a warning" is not enough

A warning is in a log. The prediction is in a **prompt**, then in an **answer**, then in
front of a customer. Those are not the same audience.

### The fix, in three parts

**1. There is no third step.** `aegis/src/aegis/ml/__init__.py:181-212` — singleton,
artifact, then `MLModelUnavailableError`. The docstring at `:183-192` says it plainly:

> *"There is deliberately no third step. ... Refusing is the honest answer: the agent's
> ML node is best-effort and simply omits the evidence, which is strictly better than
> citing a number with no signal in it."*

**2. A synthetic model is never auto-persisted.** `model.py:426-438`:

```python
if path is not None:
    if data_source == "synthetic":
        logger.warning(
            "Refusing to auto-persist a synthetic-data model to %s: a "
            "fallback fitted on generated noise must never be reloaded as a "
            "real one. Call model.save(path) explicitly if that is intended.",
            path,
        )
    else:
        model.save(path)
```

An explicit `save()` is still allowed — it is a deliberate act.

**3. The provenance rides on every response.** `data_source` is `"provided"` /
`"spec_provider"` / `"synthetic"` (`model.py:336-343`), stored on the model
(`model.py:282`), returned on `MLExplainResponse` (`model.py:698`), on the `ModelCard`
(`model.py:885`), and forwarded onto the agent's evidence event
(`aegis/src/aegis/agent/events.py:143`). So even a hand-saved synthetic model announces
itself on every single prediction.

### And the missing endpoint behaviour

Removing the fallback means `/ml/explain` must do something when there is no model. It
returns **503** with the command that fixes it —
`backend/src/app/api/routes.py:1016-1019` — instead of a plausible-looking prediction.

The design rule this bug establishes for the whole module: **between "no evidence" and
"fake evidence", no evidence wins every time.** It is only safe to say that because ML
never gates — omitting the evidence degrades the answer, it does not break the system.

---

## Bug 2 — binary classification explained the wrong class, so every driver's sign was inverted

### What was happening

SHAP's `TreeExplainer.shap_values(X)` returns different shapes depending on the task:

- regression → `(n, n_features)`
- **binary** classification → `(n, n_features)` — one margin
- multiclass → `(n, n_features, n_classes)`

The binary case looks like the regression case. It is not. **Both boosters — XGBoost and
sklearn's `HistGradientBoostingClassifier` — return the margin toward class 1**, always,
regardless of which class was predicted.

The code took those 2-D values as-is.

### The failure

The model predicts **class 0** — say, "will not escalate". The displayed drivers are the
class-**1** margin.

So every sign is backwards relative to the prediction it sits next to:

> Prediction: **will not escalate**
> Top driver: `queue_depth_at_open` — contribution **+0.42**

Read naturally: *a long queue pushed this toward "will not escalate."* The truth is the
exact opposite — the long queue pushed it toward escalation, and the model concluded "will
not escalate" **despite** it.

### Why it is worse than a wrong number

A wrong number is noise. A **confidently inverted** explanation is misinformation with a
plausible shape. An analyst reading it learns the reverse of what the model actually
encodes, and every downstream decision inherits the inversion. There is nothing in the
output that looks wrong.

### The fix

`model.py:790` and `:800-803`:

```python
flip_binary = self.task == "classification" and n_classes == 2 and class_index == 0
...
values = np.asarray(explainer.shap_values(x))
if values.ndim == 3:          # multiclass: (1, n_encoded, n_classes)
    values = values[:, :, class_index]
elif flip_binary:             # 2-D binary margin is toward class 1, not class 0
    values = -values
```

Negate for binary-and-class-0. Re-index only genuinely 3-D multiclass values.

And the class index comes from `_explained_class` (`model.py:737`), which resolves it from
the **label actually returned** rather than re-deriving it from `predict_proba`. The
docstring at `:740-742`: *"The explanation must be of the class shown next to it."*

**The transferable rule:** an explanation is not an independent artefact. It is an
explanation *of a specific displayed output*, and the binding between them has to be
explicit.

---

## Bug 3 — `conformal_coverage` reported the requested level as if it were measured

### What was happening

The model card had one coverage field, `conformal_coverage`, and it was populated with
`self.confidence_level` — the value the caller had **asked for**.

Request 0.9 → card says 0.9. Every time. On any data. On a model with $R^2 \approx 0$.
On the noise model from bug 1.

### Why nobody spots it

Because 0.9 is exactly what a correct system reports when it hits its target. There is no
tell. The number is right in the case where everything works, and identical in the case
where nothing works.

This is the general shape of the worst class of metric bug: **the failing output is
indistinguishable from the succeeding one.**

### What was actually missing

Not a formula — a **split**. The original training was two-way: train and calibration.
There was nothing held out to measure against. Calibration rows cannot be used: the
quantile was fitted on them, so coverage there is optimistic by construction.

### The fix

**A third split.** `TrustworthyModel.train` (`model.py:290`) now splits three ways
(`:363-373`): test first, then the remainder into train and calibration. The docstring at
`:305-309`:

> *"A dedicated calibration split is what makes the coverage guarantee valid — never
> calibrate on training rows — and a dedicated test split is what makes the reported
> numbers measurements rather than restatements of what was requested."*

**A measurement.** `_evaluate` (`model.py:481`) counts, on the test split, how often the
truth fell inside the interval (`:525`) or the prediction set (`:515-520`).

**Two distinct fields.** `conformal_coverage` (requested, `types.py:111`) and
`conformal_coverage_empirical` (measured, `types.py:126`, `None` when no test split
exists). Both docstrings are explicit about which is which.

**Stratification.** `stratify=y` for classification (`model.py:468-472`), because a rare
class landing entirely outside calibration silently invalidates its conformal sets
(`:452-455`). A frame too degenerate to stratify falls back with a logged warning
(`:474-478`) rather than failing the whole fit.

**A calibration-size floor.** `_min_calibration_rows(confidence_level)` (`model.py:193`)
computes the smallest $n$ for which the quantile rank is in range, and an under-sized
split raises with the arithmetic in the message (`:376-383`):

> *"Calibration split has 5 rows but a 90% conformal level needs at least 9: the requested
> coverage is unattainable."*

### The shape of an honest number

Look at what the sibling forecast module reports on a seasonal fixture: a requested 0.9,
an **achieved 0.762**, and `coverage_meets_request=False`.

**A coverage number that can never disappoint you is not a measurement.**

---

## Bug 4 — the synthetic frame was incompatible with its own preprocessor

### What was happening

`synthesise_frame` generated every feature as a standard normal float, including the ones
the spec listed as `categorical_features`.

The preprocessor one-hot encodes exactly `spec.categorical_features`. So
`OneHotEncoder.fit` saw a column of 600 distinct floats and learned **600 levels** — one
per row.

### The consequence

`handle_unknown="ignore"` (`model.py:540`) means an unseen category maps to an all-zero
block instead of raising. That is the right behaviour in production and it is exactly what
turned this into a silent failure.

A real inference row supplies `region="emea"`. The encoder has never seen `"emea"` — it
learned 600 float levels. So the entire one-hot block for `region` is **zeros**. Every
categorical feature contributes nothing. The model predicts from the numerics alone, and
`region`'s SHAP contribution is exactly 0.0.

No error. No warning. A prediction that silently ignores half its inputs.

### The second-order effect

It also made the encoded matrix absurd: 600 columns per categorical feature, all of them
useless, in a model trained on 600 rows. Every one is a column the trees can split on and
none generalises.

### The fix

`dataset.py:24-31`:

```python
SYNTHETIC_LEVELS: tuple[str, ...] = ("alpha", "bravo", "charlie", "delta")
```

with the docstring stating the reason. And `synthesise_frame` (`dataset.py:79-83`) draws
integer codes and indexes into that tuple, producing genuine strings, with the codes
**centred** so no level is a privileged zero baseline.

### Why this belongs in a module about honesty

The synthesiser exists so the spine can train and round-trip with no domain code, no
network and no files. If the synthetic path is not type-compatible with the real path,
then **your offline tests are exercising a different code path than production** — and
they will pass while production is broken.

A fixture that does not match the shape of real data is not a fixture. It is a second
implementation.

---

## Bug 5 — the host wrote its artifact into the installed library directory

### What was happening

`aegis.ml.DEFAULT_ARTIFACT_PATH` is package-relative:

```python
Path(__file__).resolve().parent / "artifacts" / "ml_spine.joblib"
```

which resolves **inside the installed `aegis` package**. That is correct for the library's
own default.

`app.ml.model` is a thin shim that re-exported `aegis.ml.model`'s constant. So the
backend — training on the *real domain spec* — wrote its model into the library
directory, the same file the library's own loader reads.

### Two real consequences

**Cross-contamination.** A model fitted on one side is picked up as the other's. Run the
library's own tests and they may load a domain model; train the domain model and it lands
where a generic loader finds it.

**A read-only install fails outright.** A wheel in `site-packages` is not writable in any
sane deployment. Training would raise on the write.

### The fix

`backend/src/app/ml/__init__.py:54-56` gives the host its own path under the backend
project root, gitignored — *"the trained model is environment state, never source"*
(`:53`).

### The bug the fix introduced — and this is the better half of the story

After the split, there were **two** constants with the same name:

- `app.ml.DEFAULT_ARTIFACT_PATH` — the **host** path, which `app.ml.get_model()` reads.
- `app.ml.model.DEFAULT_ARTIFACT_PATH` — re-exported from `aegis.ml.model`, the
  **library** path.

The training entrypoint `python -m app.ml` imported the **wrong one**.

So:

```
$ python -m app.ml
Training ML spine on domain spec: target='resolution_hours' task=regression
Saved artifact → .../site-packages/aegis/ml/artifacts/ml_spine.joblib
```

Training reported success. `/ml/explain` kept answering **503**. The two paths differed by
a directory nobody looks at, and the failure message — "no trained artifact, run
`python -m app.ml`" — pointed at the command that had just appeared to work.

An operator following the instructions gets a success message and an unchanged failure.
That is about the most frustrating failure mode a system can have.

The fix is `backend/src/app/ml/__main__.py:23-29` — import the host constant explicitly,
with a comment explaining why — plus **a test pinning that the training entrypoint targets
exactly what `get_model()` reads.**

That test is the real fix. The path could drift again; the invariant cannot.

### And the operational consequence

Removing every fallback meant a fresh machine would 503 forever. So both bootstrap
scripts now train the spine offline (no key, no database, no network) and `INSTALL.md`
documents it.

**Removing a dishonest fallback creates a setup requirement.** Owning that requirement is
part of the fix, not an afterthought.

---

## Other failure modes worth being able to enumerate

**Silent median imputation.** `_raw_row` (`model.py:615`) fills missing features from
training medians/modes. On its own that is standard. The failure is a caller who mistypes
every feature name: everything is missing, everything is imputed, and the response is a
fully confident answer about the **median training row**. `imputed_features` and
`unknown_features` (`model.py:659-660`, surfaced at `:699-700`) make that visible at zero
cost. The docstring at `:625-628` states it exactly.

**`_encoded_parents` prefix matching.** The map from encoded columns back to original
features used to be built by matching name prefixes. With a categorical `plan` and a
numeric `plan_age`, the passthrough column `plan_age` starts with `plan_`, so its entire
SHAP contribution folded into `plan` and `plan_age` reported **0.0** — a feature that
mattered, reported as irrelevant. It is now derived structurally from the fitted
preprocessor's column layout (`model.py:141-185`), with an assertion that the derived
layout matches the emitted names — because a shape change would make aggregation wrong,
and wrong-and-silent is the thing this module refuses.

**Blocking the event loop.** `predict_explain` is synchronous CPU work: an XGBoost forward
pass, a SHAP explanation, and on first call a joblib load. Called inline in an async
handler it blocked the single event loop for all of that — every in-flight request, every
SSE stream, and the health check with it. Fixed by `asyncio.to_thread`
(`backend/src/app/api/routes.py:1015`), with the reasoning in the docstring at `:995-1000`.

**The SHAP approximation for soft voting.** Documented, not hidden (`model.py:764-767`):
attributions are exact for the averaged regression output and a **per-member mean over
margin space** for soft-voting classification. Members are averaged in probability space
while TreeSHAP explains in margin space, so the driver *ranking* is faithful and the
magnitudes are approximate.

**Explainers are not pickled.** `__getstate__` (`model.py:893`) drops the cached
`_explainers` before joblib dumps, keeping artifacts small and portable across machines.
They rebuild lazily on first use.

---

## The invariants worth naming

1. **No model is a refusal, never a substitute.** Two resolution steps, then
   `MLModelUnavailableError` → 503.
2. **A synthetic model is never auto-persisted**, and always announces itself via
   `data_source`.
3. **Requested and measured coverage are different fields**, and the measured one comes
   from a split nothing else touched.
4. **The explanation is bound to the displayed prediction** — the class index comes from
   the returned label, and the binary margin is flipped when needed.
5. **Structural derivation over name matching**, with an assertion that the structure
   holds.
6. **The host owns its artifacts**, and a test pins that training writes what serving
   reads.
7. **ML informs; risk gates.** Nothing here decides whether a run stops for a human, which
   is precisely why refusing to predict is safe.

**Next:** [`40-diagrams.md`](40-diagrams.md).
