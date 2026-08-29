# PS-04 — architecture

## The hard problem, and why it is invisible

PS-04's genuine engineering difficulty is **point-in-time correctness / temporal leakage** — not
bitemporality. It is real, it is subtle, and it has a property that is terrible for a five-minute
pitch:

> **If you get it wrong, your demo looks *better*, not worse.**

There is no on-stage symptom. A CTO cannot tell by watching. This is the single biggest reason
PS-04 scores 5.5 on backend depth against PS-17's 9.0 — the metric is *engineering substance that
is explainable with a visual*, and an invisible correctness property is worth a fraction of a
visible one.

**Mitigation:** make it visible deliberately. Show the point-in-time feature store's knowledge-date
keying as a screen, and demo the leaky-vs-correct backtest side by side so the jury sees the
inflated number collapse. That converts an invisible property into a beat — it is the single
highest-leverage frontend decision in a PS-04 build.

## Point-in-time feature store

Every feature carries the date it **became knowable**, not the date it describes. The label is the
covenant test result stamped at its **delivery** date, not its test date.

This is the machinery behind SP-1 (The Three Clocks). Reference implementations to cite rather than
copy: Feast, Tecton. The ML-systems literature on training-serving skew and temporal leakage is the
justification.

Every horizon is measured observation-date → test-date, and the UI states the **true lead time =
predicted test date − today − reporting lag**, in words, next to every score.

## The model choice — defensible in 7 days

**Use a discrete-time hazard model:** person-period explosion + regularised logistic regression,
one model, horizon indicators.

**Not Cox proportional hazards** — wrong shape for calendar-anchored 30/60/90 horizons with
time-varying covariates. **Not DeepHit** — needs data volume you will not have, and you cannot
calibrate it on synthetic labels you generated yourself.

Pair with:

- **Reliability diagram + Brier score** — shown in the product, not an appendix.
- **Conformal prediction intervals** for honest uncertainty.
- **SHAP treated as suspect** for correlated financial ratios — and say so out loud. Its failure
  modes on correlated features are documented; claiming SHAP as your explainability story in front
  of a CTO who knows this is a loss.

**Better than SHAP here:** counterfactual explanation ("if receivable days revert to 52, this
alert clears") and calibration itself as a form of explanation.

## The circularity problem — architectural, not cosmetic

On synthetic data you write the generator, recover it with a gradient-boosted model, then SHAP the
generator. **Any AUC measures your own simulator.**

The architecture must route around this, which means the *arithmetic* is the contribution:

1. **Forecast the covenant components, then evaluate the real contractual formula** — so the
   arithmetic stays inspectable and the contribution is not the accuracy number.
2. **Report conformal coverage, not AUC.**
3. **Make the gaming detector the centrepiece** — it needs no labels at all (see
   `04-differentiation.md`).

Both escape routes work by *abandoning the headline prediction claim the brief asks for*. That
tension is inherent to PS-04 and must be managed in the pitch rather than hidden.

## Covenant-as-Executable-Formula

Parse the covenant definition from the agreement into an **AST over named financial line items**,
carrying its add-back set, caps (often a shared % of EBITDA), time restrictions and
pro-forma/run-rate rules. Compute **both** the contractual ratio and the GAAP ratio; render the
wedge as a first-class signal.

This is the closest PS-04 comes to PS-17's deterministic-evaluator move, and it is worth building
for the same reason: it is the part a judge can verify by hand.

## Stack on bare Windows

Same constraint set, same survivors as PS-17 — the worker landscape does not care which problem
you picked.

| Layer | Choice |
| --- | --- |
| Store | **PostgreSQL 18** (native Windows installer) |
| Queue | **pgmq** (SQL-only install) |
| Scheduling / durable execution | **DBOS Transact** (library, no server) |
| Tracing | **Jaeger** (`windows-amd64.zip`) or **Phoenix** (pure-Python wheel) |
| Modelling | scikit-learn; statsmodels for the hazard model |

**Traps:** Celery (unsupported on Windows since 4.x), RQ (`os.fork()`), Huey (no multiprocess on
Windows per its own docs), APScheduler 4 ("do NOT use in production"), Redis (no official Windows
build), Restate (no Windows binary at all).

## Scaling — the honest position

**There is no compute story.** P borrowers × F facilities × C covenants at 3 horizons: the entire
nightly inference is **under one second of CPU**. Do not pretend otherwise; a CTO will see through
it.

**The real limit is human, and it comes from the regulator's own list.** RBI's July 2024 Master
Directions enumerate ~42 EWS indicators. Naively:

```
5,000 borrowers × 42 indicators × 0.5% fire rate ≈ 1,050 alerts/day
                                  ≈ 168 per RM per month
                                  ≈ 3–4 hours of every RM's day
```

The industry benchmark is worse. BPI's *Getting to Effectiveness*: 16M alerts → 640k SARs → median
4% law-enforcement follow-up ≈ **0.16% end-to-end yield**, on >14,000 staff and $2.4bn.

**This is PS-04's production answer, and it is a good one:**

- Alert count as an explicit **service objective**, not an emergent property.
- **Capacity-constrained knapsack ranking** against RM hours available.
- **precision@budget**, never AUC.
- Persistence filter (k-of-n) before escalation.

Pitch it as *"the scaling limit here is not CPU, it is attention"* — that reframe is credible and
memorable, and it is the honest answer.
