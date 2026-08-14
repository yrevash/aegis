# Forecast — the concept, from zero

No code. What a time series is, why it is not ordinary regression, and why the most
dangerous forecast is the one whose numbers are all arithmetically true.

---

## What a time series is

A sequence of measurements taken at regular intervals, in order. Daily spend. Hourly
request volume. Monthly ticket arrivals.

The word doing the work is **in order**. That ordering is not metadata you can drop — it
is the data. Shuffle a table of customer records and you have lost nothing. Shuffle a
time series and you have destroyed it.

---

## Why this is not ordinary regression

Standard supervised learning rests on an assumption, usually unstated: your rows are
**independent and identically distributed** — i.i.d. Each row is an independent draw from
some fixed distribution. That assumption is what licenses a random train/test split,
cross-validation, and most of what you know about model evaluation.

Time series violate it in three specific ways, and each one has a name.

### 1. Autocorrelation

Today's value is correlated with yesterday's. Obviously — that is why it is worth
forecasting at all. But it means your observations are **not independent**, so:

- Your effective sample size is much smaller than your row count. A thousand hourly
  observations of a slow-moving quantity carry far less information than a thousand
  independent draws.
- Standard errors computed under independence are **too small**, so confidence intervals
  are too narrow and significance tests over-reject.
- And — the important one — a model can memorise "tomorrow ≈ today" and score
  beautifully on any random split, because for every held-out point its neighbours are
  sitting in the training set.

### 2. Seasonality

Patterns that repeat on a fixed cycle. Weekday/weekend. Hour of day. December.

Seasonality is *learnable structure*, which is good news — but it sets a hard floor on
how much history you need. You cannot learn a weekly cycle from five days. As a rule of
thumb you want **at least two complete cycles** before a model can distinguish "this
repeats" from "this happened once."

### 3. Trend and non-stationarity

Most classical time-series methods assume **stationarity**: the statistical properties
(mean, variance, autocorrelation structure) do not change over time. Real series are
rarely stationary — they drift upward, they grow, their variance expands as the level
rises.

The standard fix is **differencing**: instead of modelling the value, model the change.
If a series grows by roughly 3 a day, the differenced series hovers around 3, and *that*
is stationary. This is where the "I" in ARIMA comes from — Integrated, meaning
differenced.

---

## The families of forecasting model

You will be asked to name a few. Three classical families cover most of it, and they
divide cleanly by what they assume.

**ARIMA** — AutoRegressive Integrated Moving Average. Three parts:

- **AR(p)** — today is a weighted sum of the last *p* values.
- **I(d)** — difference the series *d* times to make it stationary.
- **MA(q)** — today also depends on the last *q* forecast *errors*, which lets it absorb
  shocks.

Add a seasonal copy of all three and you get SARIMA. Choosing `(p,d,q)` used to be a
manual ritual of staring at autocorrelation plots; `AutoARIMA` searches the space
automatically with an information criterion (AIC/BIC) balancing fit against complexity.

**ETS / exponential smoothing** — Error, Trend, Seasonal. A different philosophy: rather
than modelling dependence on past values, decompose the series into **level, trend and
season**, each updated by exponentially-weighted averaging so recent observations count
more. Each of the three components can be absent, additive or multiplicative, giving a
family of about thirty models; `AutoETS` picks among them by information criterion.

ETS often beats ARIMA on short, noisy, strongly seasonal business series — which is
exactly what a usage ledger is.

**Theta** — a deceptively simple method that decomposes the series into "theta lines"
with modified curvatures and recombines them. It has no business being as good as it is,
and it won the M3 forecasting competition. Its existence is a standing reminder that
complexity is not the same as accuracy.

**And the baseline you must always keep: seasonal naive.** *"Next Tuesday equals last
Tuesday."* No parameters, no fitting. It is astonishingly hard to beat on real business
data, and if you do not report it, nobody — including you — knows whether your fancy
model earned its place.

---

## The invalid split — the central error of this module

Here is the mistake, and it is the one to be able to explain cleanly.

You have 140 days of data. You want to know how good your model is, so you do what you
always do: `train_test_split(X, y, test_size=0.2, shuffle=True)`. Fit on 80%, score on
20%. Excellent numbers.

**They are meaningless.**

Random selection puts day 50 in your test set while days 49 and 51 sit in training.
Because of autocorrelation, day 50 is almost exactly the average of its neighbours. The
model is not forecasting; it is **interpolating between values it has already seen**.

This is **lookahead bias**, or leakage. And its signature is nasty: it does not produce
an error, it produces *excellent* scores. A leaking evaluation looks like a triumph.
Then the model goes to production, is asked to extrapolate for the first time, and
performs nothing like its test numbers.

The correct evaluation respects the arrow of time. **Every point you score must come
strictly after every point the model was trained on.** No exceptions, no shuffling, and
the same rule applies to any calibration or hyperparameter search — a leak in your
calibration set is just as fatal as one in your test set.

---

## Backtesting: rolling origin

The time-series analogue of cross-validation.

Pick a cutoff. Train on everything before it. Forecast the next *h* steps. Score those
against what actually happened. Move the cutoff forward. Repeat.

```
|-------- train --------|-- score --|
|------------ train ------------|-- score --|
|----------------- train -----------------|-- score --|
```

Also called **walk-forward validation** or **rolling-origin evaluation**. Two properties
matter:

- Every scored point is genuinely out-of-sample and genuinely in the future relative to
  its own model.
- You get several estimates rather than one, so you can see variance across cutoffs
  rather than getting lucky with a single split.

The cost is data. Each window consumes *h* observations that cannot also be used for
training, on top of everything the model needs before the earliest cutoff. That
arithmetic is why an honest forecaster **refuses short series** rather than producing a
line through nine points.

---

## Two kinds of interval, and why the label matters

Your forecast says 47.3. How wrong might that be?

### Parametric intervals

The fitted model has a probability distribution baked into its assumptions — ARIMA
assumes normally-distributed errors with constant variance, and derives a standard error
in closed form. Multiply by 1.645 and you have a "90% interval."

Cheap, instant, and **conditional on the model's assumptions being true**. If the errors
are not normal, or the variance grows with the level, or the model is simply misspecified,
that interval is wrong — and nothing about it tells you so. It is the model's own opinion
of its uncertainty, and models are optimistic about themselves.

Calling that band "90% coverage" is an **overclaim**. Ninety percent is what the model
believes under its assumptions, not a rate anyone measured.

### Conformal intervals

A different idea, and the honest one. Do not assume a distribution — **measure your
actual errors** and use them.

1. Produce forecasts on data the model did not train on.
2. Collect the errors — the *nonconformity scores*.
3. To build a 90% interval, take the 90th percentile of those absolute errors and put a
   band that wide around the point forecast.

Under an assumption called **exchangeability** — roughly, that calibration data and
future data are drawn alike — this comes with a **distribution-free finite-sample
guarantee**. No normality, no model correctness, no asymptotics.

But look at what exchangeability requires, and you can see the trap coming.

### The trap

Conformal prediction's guarantee depends on the calibration data being exchangeable with
what comes next. **Split a time series randomly and you have leaked the future into your
calibration set.** The errors you measured are interpolation errors, not forecast errors.
They are too small. The band is too narrow. And the guarantee is void while still
*looking* perfectly rigorous — you can still write "90% conformal prediction interval"
in the caption.

So conformal on a time series must calibrate **chronologically**: errors measured on
windows that come strictly before the data you are forecasting from. (There is a
literature on this — EnbPI, adaptive conformal inference — precisely because naive
conformal does not transfer to sequential data.)

**Either way, the requested level is not the achieved level.** Which brings us to the
most important idea here.

---

## Requested coverage versus achieved coverage

Two numbers. They must never be the same field, and the second must never be inferred
from the first.

**Requested coverage** is an input. You asked for 90%. It is a knob setting, echoed back.

**Achieved (empirical) coverage** is a measurement: of all the held-out actuals, what
fraction actually fell inside the band? Count them, divide.

On real data the second is routinely **below** the first. A requested 0.9 achieving 0.76
is an ordinary, unremarkable result.

And that gap **is the finding**. It is not a bug to paper over. It tells the reader
exactly how much to trust the band — which is the single most useful thing a forecast
can communicate. A system that reports only the requested level has told you nothing
except which button it pressed.

So: two fields, always. And a boolean saying whether the request was met, computed with
a strict comparison and **never rounded up**.

---

## Measuring accuracy: MAPE and the zero problem

The natural error metric is percentage error, so that a spend series and a ticket-count
series are comparable.

**MAPE** — Mean Absolute Percentage Error:

```
MAPE = mean( |actual - forecast| / |actual| ) * 100
```

It divides by the actual. So:

- **One actual of zero and MAPE is undefined.** Division by zero. And zeros are *common*
  in exactly the series you care about — a day nobody used the platform is a real,
  legitimate zero.
- It is **asymmetric**. Over-forecasting is penalised more heavily than
  under-forecasting, because the denominator differs.
- It **explodes near zero**. An actual of 0.01 forecast as 0.5 gives 4,900%.

The important design point: when MAPE is undefined, the honest report is **"undefined"**,
not a huge number. Returning 10^9 or clamping to 100% both communicate *"this model is
bad"*, when the truth is *"this metric does not apply here."* Those are different
statements and they lead to different decisions.

**sMAPE** — symmetric MAPE — divides by the average of actual and forecast:

```
sMAPE = mean( 2 * |actual - forecast| / (|actual| + |forecast|) ) * 100
```

Bounded at 200%, and defined whenever *either* side is non-zero. When both are ~0 the
sensible contribution is **0**: the forecast was exactly right about nothing happening.
sMAPE has its own critics (it is not truly symmetric either), but it is defined far more
often than MAPE, which makes it the better default for model *selection*.

**MAE** — mean absolute error — is in the series' own units. Unbeatable for
interpretability within one series ("we are off by about $4/day"), useless for comparing
across series with different scales.

Report all three where they are defined, and be explicit where one is not.

---

## The trap this module actually hit: the constant series

This is the best story in the module, and it is worth understanding exactly, because
every number involved is **true**.

Consider a quiet tenant. Nobody has called a model in months. Their ledger, bucketed
daily and gap-filled with real zeros, is:

```
0, 0, 0, 0, 0, 0, 0, 0, ... (140 of them)
```

Now forecast it.

- **The model fits perfectly.** Every candidate nails it. Residuals are exactly zero.
- **The forecast is a flat zero line.** Correct, as far as anyone knows.
- **The conformal band has width zero**, because every calibration error was zero. The
  90th percentile of a set of zeros is zero.
- **Empirical coverage is 100%.** Every held-out actual is zero, every band is `[0, 0]`,
  and zero is inside `[0, 0]`. Count them: 42 of 42.

So the system reports: *AutoETS. sMAPE 0.0%. Requested coverage 90%, achieved
**100%**. Coverage meets request: yes.*

**Every one of those numbers is arithmetically true, and the whole thing is a lie.**

It is a *perfect* forecast by every metric on the page, and it describes the **absence of
data**. Put it on a dashboard next to a real forecast and it looks better than the real
one. A user comparing tenants would conclude this is the best-modelled series they have.

The failure is not in the arithmetic. It is that a battery of metrics computed on a
degenerate input produces a confident, well-formatted answer to a question nobody should
have asked.

**The right behaviour is to refuse.** "No usage recorded" is the honest answer, and it is
also the *useful* one. Detecting it is trivial once you know to look: check whether the
series has any variation at all, before you fit anything.

The generalisable lesson is bigger than forecasting: **a validation suite that only
checks whether numbers are computable will pass a degenerate input with flying colours.**
You have to check whether the *question* was well-posed.

---

## Refusing, and why refusing is a feature

There are four honest reasons to decline to forecast:

| Reason | What you say |
|---|---|
| Too little history | "This tenant has 9 observations; an honest 14-step forecast needs 71." |
| The series is constant | "No variation — nothing to forecast." |
| Every model failed to fit | Which ones, and the actual exception from each. |
| The forecasting stack is not installed | The exact install command. |

The alternative to refusing is a naive line drawn through noise. It looks like a
forecast. It renders on the chart. Nobody can tell it apart from a real one — and it is
the single most dangerous thing this kind of module can produce.

And refusals should carry **arithmetic, not adjectives**. "Insufficient history" is a
shrug. "Have 9, need 71, because 3 backtest windows × 14 steps, plus 29 observations to
fit two seasonal cycles and calibrate" is something a user can act on: wait, or shorten
the horizon.

One transport note that matters more than it sounds: a refusal is a **result**, not an
error. Delivering it as an HTTP 500 means the console renders "backend down" and the user
learns nothing. A 200 carrying `available: false` plus a typed reason gets rendered
properly.

---

## Cumulative sums: where the guarantee stops

Last idea, and it is the one people get wrong most often.

You have a calibrated 90% interval for each of the next 14 days' spend. You want to
project the month-end total against a budget cap, so you add the daily lows and the daily
highs.

**That sum is not a 90% interval on the total.**

Two reasons. Forecast errors on consecutive days are **correlated** — if you are
under-forecasting today, you are probably under-forecasting tomorrow. And more
fundamentally, **the sum of marginal quantiles is not the quantile of the sum**. The
90th percentile of `X + Y` is not the 90th percentile of `X` plus the 90th percentile
of `Y`, except in degenerate cases.

The summed band is not conservative either — it could be too wide *or* too narrow
depending on the correlation structure.

It is still the useful thing to draw. A budget burn-down chart needs an envelope. So you
draw it — and you flag it explicitly as an **envelope, not a calibrated interval**, with
a field on the result rather than a footnote someone can miss.

---

## What you should now be able to explain

- Why ordering is the data, not metadata
- Autocorrelation, seasonality, non-stationarity — and what each breaks
- ARIMA, ETS, Theta at a conceptual level, and why seasonal naive must be reported
- Why a random split leaks the future, and why the symptom is *excellent* scores
- Rolling-origin backtesting, and why it costs data
- Parametric vs conformal, and what exchangeability requires
- Why labelling a parametric band "90% coverage" is an overclaim
- Why requested and achieved coverage must be two fields, and why the gap is the finding
- MAPE's zero-denominator trap, sMAPE, and why "undefined" beats a huge number
- **The constant series**: every number true, the whole thing a lie
- The four honest refusals, and why arithmetic beats adjectives
- Why summing marginal intervals gives an envelope, not a guarantee

**Next:** [`10-theory.md`](10-theory.md).
