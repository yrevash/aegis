# ADR 0004 — Conformal prediction (MAPIE) for calibrated, guaranteed uncertainty

- **Status:** Accepted
- **Date:** 2026-08-03
- **Deciders:** Team
- **Related:** `docs/architecture/backend.md` §5 (ML spine), `docs/hackathon/brief.md` §4 (the trust
  stack), `docs/security/overview.md` §2 (human gate on high-uncertainty), `app/ml/`.

## Context

The platform's differentiator is the **trust stack**: *every autonomous action is
uncertainty-bounded (conformal) → human gate → explainable (SHAP) → guarded
(rails) → traced (OTel + audit log)* (`docs/hackathon/brief.md` §4). The first link
carries the rest: the human-in-the-loop gate fires when a prediction is
**high-uncertainty**, so *what counts as uncertain* must be a **statistical
guarantee, not a hand-picked number** (`docs/architecture/backend.md` §5, §3). A threshold like
"escalate if probability < 0.7" is arbitrary — it has no defensible coverage
meaning, and a juror (human or AI) can ask "why 0.7?" and get no principled
answer.

We need an uncertainty layer that:

1. wraps a **real supervised model** (XGBoost, CPU-only, light — already chosen in
   `docs/architecture/backend.md` §5) without retraining it or changing its architecture;
2. produces a **distribution-free** guarantee — no assumption that the model's
   raw scores are calibrated probabilities;
3. exposes a single, explainable knob (a target **coverage rate**) that drives the
   gate and reads cleanly on the SHAP/uncertainty demo panel (`docs/hackathon/brief.md`
   §7).

## Decision

Wrap the fitted XGBoost model with **MAPIE split conformal prediction** to produce
**calibrated prediction intervals** (regression) or **prediction sets**
(classification) with a **guaranteed marginal coverage** equal to a chosen
`confidence_level`. That guaranteed coverage rate is the statistical basis for the
**human-gate threshold**: low-confidence / wide-interval predictions escalate to
the human gate instead of acting.

Concretely (`app/ml/model.py`): `TrustworthyModel.train` splits the data into a
**training set** (fits XGBoost) and a **disjoint calibration set** — a dedicated
calibration split is what makes the coverage guarantee valid; we never calibrate
on training rows. The already-fitted estimator is wrapped `prefit=True` in MAPIE's
`SplitConformalRegressor` / `SplitConformalClassifier` and `conformalize`d on the
calibration split. At inference, `predict_explain` returns the point prediction,
the **calibrated conformal interval**, the **guaranteed coverage rate**
(`conformal_confidence`), and the **SHAP** attributions in one
`MLExplainResponse` — the exact trust-stack payload the agent and frontend
consume. Default target coverage is **0.9**.

Targeted versions (verified & smoke-tested 2026-08-03): **mapie 1.4**
(`SplitConformalRegressor` / `SplitConformalClassifier`, the 1.x
`conformalize` → `predict_interval` / `predict_set` API), **xgboost 3.3**,
**shap 0.52**.

## Consequences

- **+** The gate threshold is **defensible**: "we escalate when the prediction
  falls outside a set with a guaranteed 90% coverage" is a statistical statement,
  not a vibe — the answer to "why this threshold?" that the jury and AI reader
  reward.
- **+** **Distribution-free and model-agnostic:** MAPIE wraps the *already-fitted*
  XGBoost with no retraining and no assumption about score calibration; the same
  wrapper serves both regression (intervals) and classification (sets).
- **+** **Light and portable:** split conformal is a single held-out calibration
  pass — CPU-only, fits the 16 GB / no-GPU machine, no extra model to serve.
- **+** Feeds the money-shot directly: interval + coverage rate + SHAP render as
  one explanation panel (`docs/hackathon/brief.md` §7) and complete the trust-stack
  sentence.
- **−** A **calibration split** costs labelled data and is mandatory — calibrating
  on training rows silently voids the guarantee. Accepted and enforced in
  `train` (disjoint `train_test_split`).
- **−** The guarantee is **marginal** (average coverage over the distribution),
  not conditional per input, and assumes calibration data is exchangeable with
  serving data — a fair caveat to state rather than oversell.
- **Note:** MAPIE's 1.x API replaced the old
  `MapieRegressor.predict(..., alpha=...)` interface; we target the current
  `conformalize` / `predict_interval` / `predict_set` flow (`app/ml/model.py`
  docstring).

## Alternatives considered

- **Raw model probabilities / Platt (or isotonic) scaling.** Cheapest — just read
  `predict_proba` or post-hoc-calibrate it. Rejected: even a well-calibrated
  probability gives **no coverage guarantee**, the threshold on it is still
  hand-picked, and tree models' raw scores are notoriously miscalibrated. Platt
  scaling improves calibration but still yields a point number with no
  distribution-free guarantee behind the gate.
- **Bayesian methods (Bayesian NN / Gaussian-process posteriors).** Principled
  uncertainty, but they impose a modelling paradigm (priors, approximate
  inference), are heavier to fit/serve, and their credible intervals are only as
  honest as the prior — no *frequentist* coverage guarantee. Wrong weight and
  wrong assumptions for a CPU-only, on-the-day spine.
- **A hand-tuned threshold** (e.g. escalate if score < 0.7). Simplest to ship and
  exactly what we are trying to avoid: arbitrary, undefendable, and the opposite
  of the "measurable enough to trust" thesis (`docs/hackathon/brief.md` §9). Conformal
  prediction replaces the magic number with a coverage guarantee.
