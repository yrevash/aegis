# The ML spine

The module that predicts a number and says how wrong that number might be.

---

## 1. What it is

A customer opens a support ticket. Before the agent replies, one thing would help a
lot: **how long will this take to resolve?**

No document holds that answer. Retrieval cannot find it, and a language model guessing
at it is guessing. It is a pattern in thousands of past tickets — what happened last
time a premium EU customer emailed a technical problem into a twelve-deep queue.

That is a machine-learning problem. You have a table. Each row is one past ticket. Some
columns are what you knew at the time — priority, category, channel, region, customer
tier, agent tenure, queue depth, reopen count, description length. One column is what
you want to predict: `resolution_hours`. A model learns the relationship from the rows
it is given, then applies it to a ticket nobody has seen.

The ML spine is the only part of Aegis that learns from rows and columns. It returns
three things, not one:

| What comes back | Example |
|---|---|
| A prediction | 58.6 hours |
| An honest range around it | 49.0 to 68.2 hours |
| Which columns drove it | `category` +14.2, `queue_depth_at_open` +6.9, `priority` −7.1 |

The range is the part that earns the module its name. A bare "59 hours" from a good
model looks exactly like a bare "59 hours" from a bad one. "About 59 hours, and we would
be surprised if it took more than 68" is something a support lead can schedule against —
and if the model is bad, the range comes back as "somewhere between half a day and four
days", which is useless but **visibly** useless.

---

## 2. How it works in Aegis

### The model

Aegis uses **gradient-boosted trees**. A decision tree asks a chain of yes/no questions —
*is queue depth above 20? is the category technical?* — and lands in a leaf holding a
number. One tree is weak. Boosting fits trees one after another, each new tree correcting
the errors the previous ones left, and adds them up.

Trees rather than a neural network, for three reasons. Tabular data has sharp
boundaries and junk columns, which trees handle natively. They train in CPU-seconds with
no GPU. And exact per-feature explanations are cheap on trees and expensive on everything
else — explanations were a requirement, so they constrained the model.

Aegis fits **two** boosters, XGBoost and scikit-learn's `HistGradientBoosting`, and
averages them. Two different implementations make partially different mistakes, so the
average is steadier than either. It costs a second of CPU.

### Three splits

Rows are divided three ways before anything is fitted, and the three sets never overlap.

| Split | Default share | What it earns |
|---|---|---|
| train | the remainder | Fits the two boosters |
| calibration | 25% of what is left after test | Sets the width of the range |
| test | 20% | Measures accuracy **and** whether the range actually holds |

The reason there are three rather than two is the difference between a number you asked
for and a number you measured. If you calibrate and measure on the same rows, the
measurement just restates the calibration. The test split is the only set of rows that
can disappoint you.

### The honest range, in plain words

**Conformal prediction** is a way to put an error bar on a prediction by measuring how
wrong the model was on data it never saw. There is no distribution assumption anywhere
in it.

Walk it through with 800 tickets. Fit the boosters on 600 and deliberately leave 200
untouched. Run the fitted model over those 200 and record how wrong it was each time.
Sort those 200 errors. You want a range that holds 90% of the time, so take the error at
the 90% mark of that sorted list — say it is 9.6 hours. Every prediction now gets
±9.6 hours around it. (Illustrative numbers; the mechanism is exactly this.)

Two consequences follow, and both shape the code.

**The requested level and the achieved level are different facts.** Asking for 90%
does not deliver 90%. `ModelCard` therefore carries two fields: `conformal_coverage` is
the level requested, and `conformal_coverage_empirical` is the fraction of test rows
whose true value actually fell inside the range. A card that reported the first as if it
were the second would print its own configuration and call it a result.

**A tiny calibration set cannot support a high confidence level.** With five calibration
rows there is no such thing as a 90% mark — the arithmetic runs off the end of the list.
Training refuses with the numbers in the message rather than raising from inside a
library. Nine rows is the smallest that works for 90%.

For a classification target the range becomes a **prediction set** — the labels that
cannot be ruled out. A one-label set is a confident call; a two-label set says the model
genuinely cannot separate them, which is far more useful than a bare label with a score
of 0.51. Aegis returns the label plus the set size. One rule comes with it: the split
must be stratified on the label, or a rare class can miss calibration entirely and its
sets carry no guarantee.

### Why this number, for this ticket

The other half of trustworthiness is *what drove it*. Aegis reports **SHAP values** — a
signed contribution per feature, where the base value plus all the contributions equals
the prediction exactly, with nothing left over. That exactness is what makes it valid to
add the four `region_*` columns the model actually sees back into one `region` number for
the user.

Say *"these are the drivers of the model's prediction."* Never *"these are the causes."*
SHAP describes the model, not the world.

### The three honesty fields

The same 58.6 can mean radically different things, so three fields ride on the response
object itself rather than sitting in a log. A warning reaches an operator. The prediction
reaches a customer.

| Field | Answers |
|---|---|
| `data_source` | Was this trained on real domain data, a spec-provided frame, or the built-in noise synthesiser? |
| `imputed_features` | Which inputs did the caller not supply, so were filled from training medians and modes? |
| `unknown_features` | Which keys did the caller send that are not model features and were ignored? |

Picture a caller who mistypes every feature name. Every model feature is missing, every
one is imputed, and the response is a confident prediction with a calibrated range and a
ranked driver list — **about the median training row**. With those two lists populated,
that failure is obvious. Without them it is invisible.

Two rules follow from the same principle. `get_model()` resolves the in-process model,
then a saved artifact, and then **stops** — it raises rather than training something on
demand, and `/ml/explain` returns 503 with the fixing command. And a model trained on
synthetic data is never saved automatically, so a one-off fallback can never become the
platform's permanent model.

### ML informs; risk gates

The prediction, its range and its top drivers are injected into the agent's context as
**evidence**. They never decide whether a run stops for a human. That decision comes from
the risk tier of the tool being called.

Gating on model confidence would be backwards. A model is most dangerous when it is
confidently wrong, so the gate would open exactly when it should have closed. A $4,200
refund is high-risk whether or not the model feels sure.

Because ML never gates, a missing prediction is not a failure. The `ml_predict` node
swallows any error and returns nothing; the run continues and answers without it. That is
what makes it safe to refuse rather than invent.

---

## 3. How you use it in code

```python
from aegis.ml import train, predict_explain

train(spec, frame)            # offline, once — fits, calibrates, evaluates, saves

resp = predict_explain({"priority": "high", "category": "technical",
                        "queue_depth_at_open": 12})
resp.prediction               # 58.6
resp.conformal_interval       # (49.0, 68.2)
resp.conformal_confidence     # 0.9 — the level REQUESTED
resp.shap_attribution         # signed contributions, biggest first
resp.data_source              # 'provided' | 'spec_provider' | 'synthetic'
resp.imputed_features         # what the caller did NOT supply
```

`predict_explain` uses a process-wide model. It raises `MLModelUnavailableError` if
nothing has been trained or loaded.

### Bringing your own problem

Nothing about support tickets is baked in. A spec says what to predict:

```python
from aegis.ml import ResolvedSpec, TrustworthyModel

spec = ResolvedSpec(
    features=["age", "region", "tenure"],
    target="churned",
    task="classification",
    categorical_features=["region"],
)
model = TrustworthyModel.train(spec, frame=my_dataframe, path=None)
resp = model.predict_explain({"age": 41, "region": "emea", "tenure": 3})
card = model.model_card()     # measured metadata for the MLOps UI
```

`model_card()` reads every field off the live model — the fitted members and their
weights, the split sizes, the requested and measured coverage. Nothing is hardcoded,
which is what makes it an audit artifact rather than a description.

### Settings worth changing

All of these are keyword arguments to `train`.

| Setting | Default | What it does |
|---|---|---|
| `confidence_level` | `0.9` | The coverage you are asking the range to hold |
| `calibration_size` | `0.25` | Share of non-test rows used to set the range width |
| `test_size` | `0.2` | Share held out to measure; `0` skips the measurement |
| `random_state` | `0` | Seed, for determinism |
| `path` | the default artifact path | Where to save; `None` skips saving |

### Where the code lives

`aegis/src/aegis/ml/` is six files. `model.py` holds `TrustworthyModel` — the ensemble,
the conformal wrapper and the SHAP explainer. `types.py` holds the response shapes and
imports nothing heavy, so the API layer can depend on them without pulling in the whole
ML stack. `spec.py` is the seam where a domain declares its features and target. The
backend's own layer lives at `backend/src/app/ml/` and owns the artifact path, so a
trained model never gets written inside the installed library.

---

## 4. Why it helps us

**The agent can answer questions no document contains.** Resolution time, escalation
risk, churn — these are patterns in past rows, and this is the only module that reads
them.

**Every number arrives with a range that was measured, not assumed.** A weak model
produces a wide range and announces itself. A bare point prediction never does.

**Every prediction says how much to trust it.** Provenance, imputed inputs and ignored
inputs travel on the response, so downstream code and the console can discount evidence
without reading a log.

**Nothing fake is ever served.** No model means a 503 with the command that fixes it.
Synthetic models are never persisted. Between no evidence and fake evidence, no evidence
wins — and that is only safe because ML never gates.

**It is domain-agnostic.** Pass a different spec and a different frame and the same
module predicts churn instead of resolution time. Nothing about support tickets is in the
package.

Without this module the agent would either stay silent on quantitative questions or let
the language model invent numbers with fluent confidence behind them.

**Next:** [`40-diagrams.md`](40-diagrams.md)
