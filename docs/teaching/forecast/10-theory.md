# Forecast — the theory

The maths behind the models, the conformal guarantee and exactly what voids it, the
backtesting protocol, and the metric literature.

---

## 1. Stationarity, and why everything starts there

A series is **weakly stationary** if its mean, variance and autocovariance structure do
not depend on time. Classical time-series theory is built on this, because a model
estimated on the past can only be applied to the future if the future looks statistically
like the past.

Real series usually are not. Two remedies:

**Differencing.** Replace `y_t` with `∇y_t = y_t − y_{t−1}`. A linear trend becomes a
constant; differencing twice removes a quadratic. **Seasonal differencing**,
`y_t − y_{t−s}`, removes a fixed seasonal pattern.

**Variance stabilisation.** If variance grows with level — very common in spend and
count data — a log or Box-Cox transform first, then difference.

Over-differencing is a real error: it injects negative autocorrelation and inflates
forecast variance. The **KPSS** and **ADF** tests are the standard tools for choosing
`d`, and `AutoARIMA` runs them internally.

---

## 2. ARIMA

**ARIMA(p, d, q)** on the `d`-times-differenced series `w_t`:

```
w_t = c + φ₁w_{t−1} + … + φ_p w_{t−p}
        + θ₁ε_{t−1} + … + θ_q ε_{t−q} + ε_t
```

- **AR(p)**: dependence on past *values*. Stationarity requires the roots of the AR
  polynomial to lie outside the unit circle.
- **MA(q)**: dependence on past *errors*, which lets the model absorb shocks. Requires
  invertibility.
- **I(d)**: the differencing.

**SARIMA(p,d,q)(P,D,Q)ₛ** adds a seasonal copy of all three at lag `s`.

### How `AutoARIMA` searches

The Hyndman–Khandakar algorithm, as implemented in `forecast`/`statsforecast`:

1. Choose `D` by a seasonal-strength test, then `d` by KPSS.
2. Fit four starting models.
3. **Stepwise search**: vary `p`, `q`, `P`, `Q` by ±1 from the incumbent, accept any
   improvement in **AICc**, repeat until no neighbour is better.

AICc is AIC with a small-sample correction — it penalises parameters, which is what stops
the search overfitting a short series.

**`approximation=True`** is a meaningful speed lever. During the search, candidate orders
are scored by an *approximated* (conditional-sum-of-squares) likelihood rather than the
exact one; only the winner is fitted exactly. Since the stepwise search is a heuristic
either way, the accuracy cost is usually nil and the time saving is large — which matters
when a model is fitted per HTTP request.

**Parametric intervals from ARIMA** come from the closed-form h-step forecast variance:

```
Var(ŷ_{t+h}) = σ² * Σ_{j=0}^{h−1} ψ_j²
```

where `ψ_j` are the coefficients of the infinite MA representation. That formula assumes
the model is **correct**, the errors are **normal**, and `σ²` is **known**. It is the
model's own opinion of its uncertainty.

---

## 3. ETS

Exponential smoothing takes a state-space view: the series is a **level**, optionally a
**trend**, optionally a **season**, each updated by exponentially-weighted averaging.

Simple exponential smoothing:

```
ℓ_t = α·y_t + (1 − α)·ℓ_{t−1}          ŷ_{t+h} = ℓ_t
```

Holt adds a trend `b_t`; Holt–Winters adds a seasonal component `s_t`. The **ETS
taxonomy** labels each model `ETS(E, T, S)` where each slot is `N`one, `A`dditive or
`M`ultiplicative — about 30 combinations. `AutoETS` fits the admissible ones and picks by
AICc.

**Damped trend** deserves a mention because it wins competitions: multiply the trend by
`φ < 1` at each step so the projection flattens with distance. Undamped linear trends
extrapolate absurdly at long horizons; damping is one of the most reliably
accuracy-improving tweaks in the literature.

ETS is *not* a special case of ARIMA and vice versa — the additive ETS models have ARIMA
equivalents, the multiplicative ones do not. On short, noisy, strongly seasonal business
data ETS frequently wins, which is why both belong in the roster.

---

## 4. Theta, and the baseline

**Theta** (Assimakopoulos & Nikolopoulos, 2000) decomposes the series into "theta lines"
with modified local curvature — `θ = 0` gives the linear regression line, `θ = 2` doubles
the curvature — extrapolates each, and averages. It won the M3 competition, and it is
provably equivalent to simple exponential smoothing with drift under certain conditions.

Its lasting value is as an argument: a method this simple beating far more elaborate ones
means **complexity is not accuracy**, and it should make you suspicious of any pipeline
that never checks a baseline.

**Seasonal naive** is that baseline: `ŷ_{t+h} = y_{t+h−s}`. "Next Tuesday equals last
Tuesday." No parameters. On real business series it is genuinely hard to beat, and
publishing its score — won or lost — is what makes a model selection auditable. Without
it, "AutoETS was selected" is an unfalsifiable statement.

---

## 5. Conformal prediction, properly

### The general construction

**Split (inductive) conformal**, for a target miscoverage `α`:

1. Split data into a proper training set and a **calibration** set.
2. Fit on training only.
3. Compute a **nonconformity score** on each calibration point — for regression,
   typically `s_i = |y_i − ŷ_i|`.
4. Take the `⌈(n+1)(1−α)⌉`-th smallest score, call it `q̂`.
5. Predict `[ŷ ± q̂]`.

### The guarantee

```
P( Y_{n+1} ∈ Ĉ(X_{n+1}) ) ≥ 1 − α
```

**Marginal**, **finite-sample**, and **distribution-free**. No normality, no asymptotics,
no assumption that the model is any good — a terrible model just gets very wide
intervals, which is the correct behaviour.

The `(n+1)` and the ceiling are not decoration. They are what make the bound exact in
finite samples rather than asymptotic.

### The one assumption: exchangeability

The guarantee requires that the calibration points and the future point are
**exchangeable** — their joint distribution is invariant to permutation. I.i.d. data is
exchangeable. It is weaker than i.i.d., which is part of the appeal.

**A time series is not exchangeable.** Order carries information by construction.

### What breaks, concretely

Split a time series randomly into train and calibration:

- Calibration points are *interspersed* with training points.
- Because of autocorrelation, predicting a point whose neighbours are in training is
  near-**interpolation**.
- Interpolation errors are much smaller than forecast errors.
- So `q̂` is too small, and the interval is too narrow.

And here is the part that makes it dangerous: **nothing errors, and the label still
says "90% conformal prediction interval."** The construction ran. The percentile was
computed. The number is real. It is simply calibrated on the wrong error distribution.

### The correct construction for sequential data

Calibrate **chronologically**. Compute nonconformity scores on rolling windows entirely
*inside* the training slice, using genuine h-step-ahead forecasts. Then the errors you
are quantiling are forecast errors of the right horizon.

That is what `statsforecast`'s `ConformalIntervals(n_windows=k, h=H)` does: it runs `k`
rolling-origin forecasts inside the training data and quantiles the resulting errors.

Note two residual caveats an interviewer might probe:

- Even chronological conformal is only **approximately** valid on time series, because
  exchangeability still does not strictly hold. The literature — EnbPI (Xu & Xie 2021),
  Adaptive Conformal Inference (Gibbs & Candès 2021) — exists precisely to address this.
- Coverage is **marginal**, averaged over the calibration distribution. It is not
  conditional coverage at every point, and it says nothing about the cumulative sum (see
  §8).

Which is exactly why the *achieved* rate must still be **measured** rather than assumed,
even with a correctly-constructed conformal band.

---

## 6. Rolling-origin backtesting

The protocol:

```
for each cutoff t_k:
    train on y[1 : t_k]
    forecast y[t_k+1 : t_k+h]
    score against the actuals
```

Parameters that matter:

**`n_windows`** — how many cutoffs. Two is the absolute floor for a coverage rate to mean
anything (one window gives you a single sample). More windows means a more stable
estimate and less data left for training.

**`step_size`** — how far the cutoff moves. Setting `step_size = h` makes the scored
windows **non-overlapping**, so every held-out point is scored exactly once. Overlapping
windows re-score the same points from different origins, which is not wrong but correlates
the estimates and makes the effective sample size smaller than the count suggests.

**`refit`** — whether to re-estimate parameters at each cutoff. `refit=True` is more
honest (it mimics production, where you retrain) and costs `n_windows` fits.
`refit=False` estimates once and rolls the state forward — much cheaper, mildly
optimistic, and defensible when the fit is stable and the series is short.

### The minimum-history arithmetic

Every window consumes data. The requirement decomposes:

```
need = n_windows * h                        # every held-out scoring window
     + max(
           (conformal_windows − 1) * h + 1,  # conformal calibration inside training
           2 * season_length + 1,            # two full seasonal cycles to learn one
           2 * h,                            # train longer than you predict
       )
```

The second term is what must **still remain before the earliest cutoff** — the model
fitted at that cutoff has to be trainable *and* calibratable on that slice alone.

Concretely for `h=14`, weekly seasonality (`s=7`), 3 backtest windows, 3 conformal
windows:

```
3 × 14 + max( 2×14 + 1, 2×7 + 1, 2×14 )
= 42 + max(29, 15, 28)
= 42 + 29
= 71 observations
```

So a 14-day forecast on daily data needs 71 days of history. A tenant with nine days gets
**refused**, and the refusal should carry that arithmetic — because "wait 62 more days"
and "ask for a 3-step horizon instead" are both actionable, and "insufficient history" is
not.

---

## 7. Metrics

| Metric | Formula | Defined when | Notes |
|---|---|---|---|
| MAE | `mean(\|e\|)` | always | Series' own units. Not comparable across series. |
| RMSE | `sqrt(mean(e²))` | always | Penalises large errors more. Optimises for the mean. |
| MAPE | `mean(\|e\|/\|y\|)·100` | all `y ≠ 0` | Asymmetric; explodes near zero. |
| sMAPE | `mean(2\|e\|/(\|y\|+\|ŷ\|))·100` | either side ≠ 0 | Bounded 0–200%. Not truly symmetric. |
| MASE | `MAE / MAE_naive` | naive MAE ≠ 0 | Scale-free. **The M-competition recommendation.** |

Three points worth having ready.

**MAPE's zero problem is not an edge case.** For counts and spend, zeros are the normal
state of a quiet period. Any metric that divides by the actual is undefined for a
meaningful fraction of real series. Returning a huge number instead of "undefined"
communicates *"this model is bad"* when the truth is *"this metric does not apply"* —
different statements, different decisions.

**sMAPE's both-zero case** should contribute **0**, not a division by zero: the forecast
was exactly right about nothing happening.

**MASE is arguably the better default.** Dividing by the in-sample naive MAE makes it
scale-free and defined far more often. If you use sMAPE for selection, be able to say why
— usually "it is what the stack reports and it is bounded", which is honest enough.

### Selection optimism

If you pick the winner **on the same windows** you then report metrics from, those metrics
are **in-selection** and mildly optimistic — you selected for good performance on exactly
those points. With three candidates the effect is small; with fifty it is not.

The rigorous fix is a nested scheme (select on inner windows, report on an outer holdout),
which costs more data than a 140-point series has. The honest alternative is to **declare
it**: state that selection and reporting used the same windows, so a reader can discount
appropriately. Silently reporting in-selection metrics as if they were out-of-sample is
the overclaim.

---

## 8. Summing intervals: where the guarantee stops

You have marginal `1−α` intervals for each of `h` steps and want an interval on
`S = Σ ŷ_t`.

Summing the bounds gives `[Σ lo_t, Σ hi_t]`. That is **not** a `1−α` interval on `S`.

**Why.** Quantiles are not additive:

```
Q_{0.9}(X + Y) ≠ Q_{0.9}(X) + Q_{0.9}(Y)
```

except under degenerate conditions. For independent variables the sum of quantiles is
generally **too wide** (variances add, standard deviations do not). For positively
correlated forecast errors — which consecutive-step errors are — it can be **too narrow**.

Since you cannot say which way it errs without modelling the joint distribution, the sum
is neither conservative nor calibrated. It is an **envelope**.

You still draw it, because a budget burn-down needs one. And you flag it — as a field on
the result, not a footnote — so nobody quotes it as a coverage claim.

Doing better would mean a joint predictive distribution over the horizon (simulate
trajectories from the fitted model, sum each, quantile the sums) or a conformal method
built for the cumulative functional. Both are more machinery than an envelope-plus-a-flag
is worth for a burn-down chart.

---

## 9. Why forecasting sits apart from scalar ML

A scalar ML response is one prediction and one interval. A forecast is **horizon-indexed**
— a *sequence* of `(timestamp, point, lo, hi)` rows whose uncertainty widens with
distance.

That widening is structural, not decorative: at `h=1` you know yesterday's value; at
`h=14` you have compounded thirteen steps of uncertainty. A forecast API whose interval
does not widen with the step is either not forecasting or not reporting honestly.

There is no lossless way to fold a horizon-indexed result into a scalar one, which is why
the two contracts stay separate — and why an ML module calibrating on a random split can
coexist with a forecast module that must not.

---

## What you should now be able to explain

- Stationarity, differencing, and the cost of over-differencing
- ARIMA's three parts, the Hyndman–Khandakar stepwise search, and what AICc buys
- What `approximation=True` trades and why it is defensible per-request
- ETS's state-space decomposition, the `(E,T,S)` taxonomy, and damped trend
- Why Theta and seasonal naive belong in any honest roster
- Split conformal step by step, and what the `(n+1)` ceiling is for
- Exchangeability, why a time series violates it, and why the failure is silent
- Chronological conformal, and the residual caveats (EnbPI, ACI, marginal vs conditional)
- Rolling origin, `step_size=h`, and the `refit` trade
- The minimum-history arithmetic, and why 71 observations for `h=14`
- MAPE/sMAPE/MASE and why "undefined" beats a huge number
- Selection optimism, and why declaring it is the honest fix
- Why summed marginal quantiles are an envelope, not an interval

**Next:** [`20-in-aegis.md`](20-in-aegis.md).
