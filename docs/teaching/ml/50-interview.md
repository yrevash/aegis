# ML — interview questions and answers

The theme running through every answer: **a structurally valid number with no signal in it
is worse than no number.**

---

### "What does the ML in this system actually do?"

It predicts a domain quantity from structured historical data — in our reference domain,
how long a support ticket will take to resolve — and injects three things into the agent's
answer context as **evidence**: the point prediction, a calibrated conformal interval, and
the top signed SHAP drivers.

It is a genuinely different capability from the rest of the system. Retrieval finds text;
the language model writes text; this learns a pattern from rows and columns that exists in
no document.

The architectural rule is **ML informs; risk gates**. The prediction never decides whether
a run stops for a human. That is decided by the risk tier of the tool being called.

---

### "Why is that separation important?"

Because the intuitive design — stop for a human when the model is unsure — fails in exactly
the case it exists for.

A model is most dangerous when it is **confidently wrong**. Out-of-distribution inputs,
adversarial inputs and plain novelty all routinely produce high confidence with no
grounding. Gate on confidence and the gate opens precisely when it should have closed.

Tool risk has no such failure mode. A $4,200 refund is high-risk whether or not the model
feels sure. The gate is a property of the **action**, which is a fact, rather than of the
model's feeling about the action, which is an artefact.

**And there is an operational payoff.** Because ML never gates, a missing or failed
prediction is not a failure — the evidence is simply omitted and the run continues. That
is what made it *safe* to remove every fallback and refuse to serve rather than serve
something fake.

---

### "Why gradient-boosted trees rather than a neural network?"

Tabular data. Grinsztajn et al. (2022) give three reasons and they hold up: tabular
targets are often piecewise-constant with sharp boundaries, which trees model natively and
MLPs fight with their smoothness bias; real tables carry uninformative columns, which trees
ignore by never splitting on them; and MLPs are roughly rotation-invariant, which is a
liability when individual columns *mean* something.

Practically: it trains in CPU-seconds, needs no GPU, and TreeSHAP gives **exact** Shapley
values in polynomial time rather than sampled approximations. That last point is not
incidental — explanation quality was a requirement, and it constrained the model class.

We use an ensemble of two — XGBoost and sklearn's histogram booster — averaged. The benefit
is entirely decorrelation: averaging identical models buys nothing, but two differently
implemented boosters make partially different errors, so the average genuinely reduces
variance. It costs a second CPU-second.

---

### "Explain conformal prediction as if I've never heard of it."

A model's raw confidence is not a probability. Nothing in the training objective requires
its output to be a frequency — it is a score that happens to live between 0 and 1. Models
are routinely over-confident.

Conformal prediction gives you a real guarantee instead, and the mechanism is almost
embarrassingly simple. Fit the model on 600 rows and hold back 200 it never saw. Run it
over those 200 and record how wrong it was each time. Sort those 200 absolute errors and
walk up to the 90th percentile — say that lands at 9.6 hours. That is your ruler.

Now the model predicts 58.6 hours for a new ticket, and the interval is
`[58.6 − 9.6, 58.6 + 9.6]` = `[49.0, 68.2]`. The true value falls inside about 90% of the
time, and you assumed nothing about the shape of the error distribution to get there.

**Three properties are the pitch.** Distribution-free — no Gaussian assumption anywhere.
Finite-sample — it holds for the data you have, not asymptotically. Model-agnostic — it
wraps anything.

The proof is short: under exchangeability, the new point's residual is equally likely to
occupy any rank among the n+1 residuals, so taking the ⌈(n+1)(1−α)⌉-th smallest bounds the
miss rate. That is the whole argument.

For classification the analogue is a **prediction set** — the labels that cannot be
excluded. A singleton is a confident call; a two-label set is genuine ambiguity, which is
far more useful than a bare label with a score of 0.51.

---

### "What can break the guarantee?"

**Exchangeability**, and it breaks quietly.

Split a **time series randomly** and calibration rows come from after some test rows. You
have leaked the future into calibration, the guarantee is void, and — the nasty part — the
reported numbers look *better* than reality. Time series must be split chronologically.

**Distribution shift.** The world moves; your calibration set is from last year. Nothing in
the arithmetic notices.

Two more that are specific and worth naming:

**The calibration set has a hard minimum.** The quantile rank must be in range —
⌈(n+1)(1−α)⌉ ≤ n. For 90% coverage the smallest workable n is 9. A five-row calibration
split cannot support a 90% interval, arithmetically, no matter how good the data is. We
compute that minimum and refuse with the arithmetic in the error message.

**The guarantee is marginal, not conditional.** It is averaged over the whole distribution.
If the model is bad on APAC tickets and APAC is 15% of volume, the headline rate can sit at
90% while APAC is covered at 70% — every individual APAC promise worse than advertised, and
nothing on the dashboard showing it. Conditional coverage is provably impossible in full
generality; Mondrian conformal recovers it per declared group at the cost of enough
calibration rows per group.

---

### "Tell me about a bug you found here."

The fallback that trained on Gaussian noise, persisted it, and served it forever.

`get_model` resolved in three steps: in-process singleton, persisted artifact, then **train
one on demand**. With no domain spec available, spec resolution falls back to a generic
four-feature spec, and with no frame, frame resolution falls back to the built-in
**synthesiser** — standard normal random numbers with a linear target and Gaussian noise.

The caller got everything a real model produces: a prediction, a genuinely correctly
calibrated 90% conformal interval, and SHAP drivers named `feature_0` through
`feature_3`. The conformal machinery worked perfectly. On noise.

**And it wrote the artifact to disk.** So a one-off fallback — say a transient import
failure at one unlucky startup — became the platform's permanent model, because step two
would always succeed from then on.

Three fixes. `get_model` now has **no third step** — it raises `MLModelUnavailableError`,
and `/ml/explain` answers 503 with the command that fixes it. `train` never auto-persists a
synthetic model, only warns; an explicit `save()` is still allowed because that is a
deliberate act. And `data_source` rides on every response and every model card, so even a
hand-saved synthetic model announces itself on every prediction.

The rule it established: **between no evidence and fake evidence, no evidence wins every
time** — and that is only safe to say because ML never gates.

---

### "That sounds like a warning would have been enough."

A warning goes in a log. The prediction goes into a **prompt**, then into an **answer**,
then in front of a customer. Those are not the same audience, and only one of them acts on
it.

The general principle I would apply anywhere: if a number can be produced with no signal in
it, the *response object* must carry that fact — not the log.

---

### "Give me another one."

Binary classification explained the wrong class, so every driver's sign was inverted.

SHAP's tree explainer returns different shapes per task. For **binary** classification it
returns a 2-D array that looks exactly like the regression case — but it is a single margin,
and that margin is always **toward class 1**, regardless of which class was predicted.

So when the model predicted class 0, the displayed drivers were the class-1 margin. Read
naturally: "a long queue pushed this toward *will not escalate*", when the truth is the
exact opposite — it pushed toward escalation and the model concluded otherwise despite it.

A wrong number is noise. A confidently **inverted** explanation is misinformation with a
plausible shape: an analyst reading it learns the reverse of what the model encodes, and
nothing in the output looks wrong.

The fix negates the values for binary-and-class-0 and re-indexes only genuinely 3-D
multiclass values. And the class index comes from the label **actually returned** rather
than being re-derived from `predict_proba` — because the explanation has to be *of the
thing displayed beside it*. That binding has to be explicit; an explanation is not an
independent artefact.

---

### "And the coverage one?"

`conformal_coverage` reported the level that was **requested** as though it had been
measured. Ask for 0.9, the model card says 0.9 — on any data, on a model with R² near
zero, on the noise model from the other bug.

**Nobody spots it because 0.9 is exactly what a correct system reports when it hits its
target.** There is no tell. The failing output is indistinguishable from the succeeding
one, which is the worst class of metric bug.

What was missing was not a formula but a **split**. Training was two-way — train and
calibration — and there was nothing held out to measure against. Calibration rows cannot be
used, because the quantile was fitted on them.

So: a three-way split, an actual measurement of how often the truth landed inside the
interval on the test split, and **two distinct fields** —
`conformal_coverage` (requested) and `conformal_coverage_empirical` (measured, `None` if
nothing was held out). Plus stratification on the label, because a rare class landing
entirely outside calibration silently invalidates its conformal sets, and a calibration-size
floor that refuses when the level is arithmetically unattainable.

**A coverage number that can never disappoint you is not a measurement.** The honest shape
is a requested 0.9 sitting next to a measured rate that came out lower, and a flag saying
so — which is exactly what the sibling forecast module reports, where
`coverage_meets_request` is a strict `>=` against the measured value and is never rounded
up in the model's favour.

---

### "You mentioned a synthetic-data bug too."

The synthesiser generated *every* feature as a standard normal float — including the ones
the spec listed as categorical.

The preprocessor one-hot encodes exactly the declared categoricals, so it fitted on a
column of 600 distinct floats and learned **600 levels**, one per row. Then a real
inference row supplies `region="emea"`, which the encoder has never seen, and
`handle_unknown="ignore"` maps it to an **all-zero block**.

So every categorical feature contributed nothing, `region`'s SHAP contribution was exactly
0.0, and the model predicted from numerics alone. No error, no warning.

`handle_unknown="ignore"` is the right production behaviour, and it is exactly what turned
this into a silent failure.

**The lesson is about fixtures.** The synthesiser exists so the spine trains and
round-trips with no domain code. If the synthetic path is not type-compatible with the real
path, your offline tests exercise a different code path than production — they pass while
production is broken. A fixture that does not match the shape of real data is not a
fixture; it is a second implementation.

---

### "Anything about deployment?"

Yes, and it is the best "fix that introduced a bug" story I have.

The library's default artifact path is package-relative, so it resolves **inside the
installed `aegis` package**. The backend shim re-exported that constant — so the host,
training on the *real domain spec*, wrote its model into the library directory, the same
file the library's own loader reads. Cross-contamination, and a read-only wheel install
fails outright.

The fix gives the host its own path under the backend project root. But that created **two
constants with the same name**: `app.ml.DEFAULT_ARTIFACT_PATH` (the host path, which
`get_model` reads) and `app.ml.model.DEFAULT_ARTIFACT_PATH` (re-exported from the library).

The training entrypoint imported the wrong one. So `python -m app.ml` printed "Saved
artifact →" and succeeded, `/ml/explain` kept answering **503**, and the failure message
pointed at the command that had just appeared to work. The two paths differed by a
directory nobody looks at.

**The real fix is not the import line — it is the test** pinning that the training
entrypoint targets exactly what `get_model()` reads. The path can drift again; the
invariant cannot.

And one consequence worth owning: removing every fallback meant a fresh machine would 503
forever, so both bootstrap scripts now train the spine offline and the install docs say so.
Removing a dishonest fallback creates a setup requirement, and owning that requirement is
part of the fix.

---

### "What does SHAP actually tell you?"

It attributes a prediction across features using the **Shapley value** from cooperative
game theory — the average marginal contribution of each player across all orderings, which
is the *unique* division satisfying efficiency, symmetry, dummy and additivity.

Efficiency is the property that makes it auditable: base value plus attributions equals the
prediction, exactly. Nothing unaccounted for. And it is what makes it valid to **sum**
one-hot columns back to their parent categorical feature.

**What it does not tell you is causation.** It tells you what moved *this model's output*
for *this row*. If two features are correlated, the model may lean on one and SHAP
attributes to the one the model used — a true statement about the model, possibly false
about the world. I say "drivers of the model's prediction", never "causes".

**One honest approximation:** for the ensemble, attributions are exact for the averaged
regression output, because Shapley values are additive across the game. For soft-voting
classification they are a per-member mean over margin space — members are averaged in
probability space while TreeSHAP explains in log-odds space, so the driver *ranking* is
faithful and the magnitudes are approximate. That is documented in the code rather than
glossed.

---

### "Why does the response carry `imputed_features`?"

Because silent imputation is a correctness problem, not a convenience.

Missing features are filled from training medians and modes. For one missing feature that
is standard. Now consider a caller who mistypes every feature name: everything is missing,
everything is imputed, and the response is a fully confident prediction about the **median
training row** — with nothing saying that none of the caller's input was used.

`imputed_features` and `unknown_features` on the response turn an invisible failure into a
visible one at zero cost. Same principle as the gateway's unpriced-cost tag: the number is
fine, the *claim it makes* needs qualifying.

---

### "How is the module made domain-agnostic?"

The spec is injected. *What* to predict — features, target, task, which features are
categorical, optionally a training-frame provider — comes from the caller. *How* to
predict, calibrate and explain lives in the module.

The spec is a `Protocol` requiring only `features` and `target`, and the resolver reads an
arbitrary object **leniently** — several attribute-naming conventions — so a real domain
contract is never silently dropped. It is also idempotent, so re-resolving an
already-resolved spec does not lose fields.

Swapping the ensemble is a one-function edit, marked in the source as the estimator reshape
point — the conformal and SHAP plumbing adapt automatically because they only require tree
models a `TreeExplainer` supports.

**The one place the abstraction was dangerous** is the fallback spec. A lenient resolver
that answers "no usable spec" with a generic default is the exact mechanism that fed the
noise synthesiser into production. So a missing domain adapter is now a named error, not a
silent downgrade.

---

### "How would you test this?"

Four levels.

**Pure functions first.** `_min_calibration_rows` is arithmetic — assert that 90% needs 9.
The encoded-parent mapping is derived structurally and asserted against the emitted column
names, so a preprocessor shape change fails loudly.

**Statistical properties, not exact values.** On a known synthetic frame with a known
signal, assert that empirical coverage is near the requested level, that SHAP contributions
sum to the prediction minus the base value, and that distinct inputs give distinct
predictions — the trainer does that last one as a sanity check, because a constant
predictor is a real and silent failure.

**The failure directions explicitly.** No artifact must raise, not fabricate. A synthetic
model must not auto-persist. A too-small calibration split must refuse. A binary
class-0 prediction must produce drivers whose signs match the direction of that class — a
test the pre-fix code fails.

**Round trips and paths.** Save/load must preserve behaviour, including that the dropped
SHAP explainers rebuild. And the artifact-path invariant: the training entrypoint must
write exactly what `get_model()` reads.

That last shape — a regression test confirmed *failing* on the pre-fix code — is what makes
any of these fixes credible.
