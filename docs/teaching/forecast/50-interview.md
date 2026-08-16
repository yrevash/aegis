# Forecast — interview questions and answers

Claim, reason, concrete detail.

---

### "Why is forecasting a separate module? Isn't it just regression?"

Two reasons, and the second is the important one.

**The shape of the answer differs.** Our scalar ML response is one prediction and one
interval. A forecast is **horizon-indexed** — a sequence of `(timestamp, point, lo, hi)`
rows whose uncertainty widens with distance. There is no lossless way to fold that into a
scalar contract.

**The evaluation differs, and this is the real reason.** Ordinary supervised learning
assumes i.i.d. rows, which is what licenses a random train/test split. Time series violate
that on three axes at once: **autocorrelation** (today correlates with yesterday, so your
observations are not independent and your effective sample size is far smaller than your
row count), **seasonality** (learnable structure, but it sets a hard floor on how much
history you need), and **non-stationarity** (the mean and variance drift, so the past is
not statistically identical to the future).

Our ML module calibrates its conformal predictor on a random `train_test_split`. That is
correct for i.i.d. tabular rows and **wrong** for a time series. So the forecast module
splits by time throughout — which is a different enough discipline that mixing them would
guarantee someone got it wrong.

---

### "Why is a random split invalid on a time series?"

Because it leaks the future into the past, and the symptom is *excellent* scores.

Random selection puts day 50 in your test set while days 49 and 51 sit in training.
Because of autocorrelation, day 50 is nearly the average of its neighbours — so the model
is not forecasting, it is **interpolating between values it has already seen**.
Interpolation errors are much smaller than forecast errors.

That is what makes it dangerous. It does not raise. It does not warn. It produces a
triumphant evaluation, and the model performs nothing like it in production, where it has
to extrapolate for the first time.

The correct protocol is **rolling origin**: pick a cutoff, train on everything before it,
forecast the next *h* steps, score those, move the cutoff forward. Every scored point is
strictly after every point its own model saw. We use `step_size = horizon` so the scored
windows do not overlap and every held-out point is scored exactly once.

---

### "How does that void a conformal guarantee?"

Conformal prediction's guarantee is distribution-free and finite-sample — but it rests on
**exchangeability**: the calibration data and the future point must be drawn alike, such
that their joint distribution is permutation-invariant.

A time series is not exchangeable. Order carries information by construction.

So if you split randomly for calibration, the nonconformity scores you collect are
**interpolation** errors, not forecast errors. They are too small. The quantile is too
small. The band is too narrow.

And here is the part I would emphasise: **nothing errors, and the label is still
technically correct.** You can still write "90% conformal prediction interval" in the
caption. The construction ran. The percentile was computed. It is simply calibrated on
the wrong error distribution.

The fix is chronological calibration — errors measured on rolling windows entirely inside
the training slice, using genuine h-step-ahead forecasts. That is what
`ConformalIntervals(n_windows, h)` does, and we thread it into **every** candidate rather
than wrapping the winner afterwards.

One honest caveat I would add: even chronological conformal is only *approximately* valid
on sequential data, because exchangeability still does not strictly hold. There is a
literature on this — EnbPI, adaptive conformal inference — precisely because of that. Which
is exactly why the **achieved** rate is still measured rather than trusted.

---

### "What is the difference between a parametric interval and a conformal one?"

A **parametric** interval comes from the fitted model's own predictive distribution —
ARIMA's closed-form h-step forecast variance, multiplied by a normal quantile. It is
cheap, instant, and conditional on the model's assumptions being true: errors normal,
variance constant, model correctly specified. It is the model's own opinion of its
uncertainty, and models are optimistic about themselves.

A **conformal** interval is measured. You collect actual out-of-sample errors and take
their quantile. No distributional assumption. A bad model gets a wide band, which is the
correct behaviour.

Labelling a parametric band "90% coverage" is an **overclaim** — 90% is what the model
believes under its own assumptions, not a rate anyone measured.

So conformal is our default, both kinds are available, and `interval_method` on the result
always says which one you are holding. The detail string for a parametric band
deliberately does not contain the word "conformal", and there is a test asserting the two
methods produce **different numbers** — because a relabelled identical band would be
exactly the overclaim.

**And I would volunteer the awkward result**, because it is the best evidence for why we
measure at all. On our own fixture the parametric band covered *better*: 0.9048 against
conformal's 0.7619, and it held up across three seeds. It did that by being wider almost
everywhere — roughly flat at 7.1–7.25 across the horizon, against a conformal band ranging
1.8 to 8.8.

Two readings, and I would give both. Our conformal quantile is estimated from only three
calibration windows, which is a small sample for a 90th percentile and can easily land low.
And a band that barely widens across a 14-step horizon is suspicious in its own right —
it covered well here by being generously wide, not by being calibrated, and on a series
whose variance grows it would fail in the other direction just as confidently.

The claim that survives is the module's actual position: calling *either* band "90%
coverage" without measuring is an overclaim. Conformal is the more principled
construction; that did not stop it undercovering, and only the count told us so.

---

### "You report a 90% interval. How do you know it's 90%?"

I don't, and the whole module is built around saying so.

There are two fields, always. `requested_coverage` is what was asked for — an input,
echoed back, *not a measurement*. `empirical_coverage` is a **count**: of the held-out
actuals across all backtest windows, what fraction fell inside the band? Divide.

On real data the second is routinely below the first. Running the module on its own
140-point daily fixture at `h=14` and a requested `level=0.9`, the three backtest windows
produce 42 held-out points and the selected model lands **32** of them inside its band —
an achieved **0.7619**. `coverage_meets_request` is **False**, computed with a strict
`>=` and never rounded up.

**That gap is the finding, not a bug.** It tells the reader exactly how much to discount
the band, which is the single most useful thing a forecast can communicate. Reporting only
the requested level tells them nothing except which button was pressed.

The console follows the same rule. `CoverageMeter` draws the requested and the achieved
rate on one shared 0–100% track directly under the chart, so the shortfall is visible as a
gap before you read a digit.

---

### "Tell me the most interesting bug in this module."

A constant series. Every number in it is arithmetically true and the whole thing is a lie.

Picture a quiet tenant. Nobody has called a model in months, so their ledger — bucketed
daily and gap-filled with **real** zeros, which is the correct reading, because a day
with no rows is a day with no spend — is 140 observations of `0.0`.

Now forecast it. I have run this with the guard removed, so these are measured, not
asserted — and all three candidates produce identical numbers:

- **Every model fits perfectly.** Residuals are exactly zero.
- **sMAPE is 0.0%**, because when both actual and forecast are ~0 the pair contributes
  zero — deliberately, since the forecast was exactly right about nothing happening.
- **The conformal band has width zero.** The 90th percentile of a set of zeros is zero.
- **Empirical coverage is 100%.** Every actual is `0.0`, every band is `[0.0, 0.0]`, and
  `0.0 <= 0.0 <= 0.0` is true. Forty-two out of forty-two.
- **Therefore `coverage_meets_request` is True**, because `1.0 >= 0.9`.

The dashboard says: *sMAPE 0.0%, achieved coverage 100%, meets request ✅.* That is a
better result than any real forecast in the system will ever produce, and it describes
**the absence of data**.

**Here is what makes it worth telling.** The module is built around honesty controls, and
not one of them fired. Requested and achieved were separate fields — both computed
correctly. Coverage was measured, not assumed — it measured 100%. The comparison was
strict — it passed on merits. The losers were published — they scored perfectly too.

Every one of those controls answers *"is this number computed correctly?"* None of them
asks *"was this question well-posed?"*

The fix is three lines, before statsforecast is even imported: if the spread is below a
relative tolerance, raise `DegenerateSeriesError`. The message explains the trap rather
than saying "degenerate series". And both the all-zero case and a flat-nonzero case at
42.0 are tested, because a constant at 42 has the same pathology and would slip past an
`all(v == 0)` check.

**The generalisable lesson:** a validation suite that only checks whether numbers are
*computable* will pass a degenerate input with flying colours. For any metrics pipeline,
ask: *what input makes every one of my numbers look perfect while meaning nothing?* For
coverage it is a zero-width interval. For precision it is always predicting the negative
class. For a cache hit rate it is an empty cache nobody queries.

---

### "How do you decide whether you have enough data?"

Explicit arithmetic, in a dependency-free layer, so a caller can decide **before** paying
to import the forecasting stack.

```
need = backtest_windows * horizon
     + max( (conformal_windows - 1) * horizon + 1,   # conformal calibration
             2 * season_length + 1,                   # two full seasonal cycles
             2 * horizon )                            # train longer than you predict
```

The second term is what must still remain **before the earliest cutoff** — the model
fitted there has to be trainable *and* calibratable on that slice alone.

For a 14-day horizon on daily data with weekly seasonality: `3×14 + max(29, 15, 28) = 71`
observations. A tenant with nine gets refused.

And the refusal carries **arithmetic, not adjectives**: `.have = 9`, `.need = 71`, plus a
reason explaining where 71 comes from. "Insufficient history" is a shrug; "have 9, need
71" lets the user wait or ask for a shorter horizon. There is a test asserting the
requirement is a function of the **horizon**, not of the series being "small" — 80 points
is plenty at `h=7` and refused at `h=21`.

---

### "What do you do when you can't forecast?"

Refuse — and never produce a naive line through noise, because nobody can tell it apart
from a real forecast on a chart.

Four typed refusals: too little history, a constant series, every candidate failed to fit
(with each one's real exception text), and the extra not installed. All subclass a common
base, because a caller must be able to distinguish *"we could not forecast this"* from
*"here is a forecast."*

**The transport decision matters more than people expect.** A refusal is delivered as an
HTTP **200 with `available: false`** and a typed reason, not a 4xx or 5xx. *"This tenant
has nine days of ledger and needs seventy-one"* is a **result** — arguably the most useful
thing the endpoint can say. An HTTP error would be discarded by the console as a
connectivity blip and the user would learn nothing.

And the refusal mapper **re-raises anything it does not recognise**. An unexpected
exception must not be laundered into a tidy "not available", because that turns a genuine
bug into an ordinary UI state nobody investigates.

---

### "Why do you report models that lost?"

Because a selection is only auditable if the reader can see what the winner beat.

Seasonal naive — "next Tuesday equals last Tuesday" — is always in the roster and always
reported. It is genuinely hard to beat on real business data, and without that row
"AutoARIMA was selected" is an unfalsifiable statement.

Here is the measured candidate table from the run I quoted earlier:

| Model | sMAPE | Achieved coverage | |
|---|---|---|---|
| AutoARIMA | 1.8319 | 0.7619 | **selected** |
| AutoETS | 1.8501 | 0.8095 | |
| SeasonalNaive | 4.5432 | 0.7619 | |

Three things a single number would have hidden. The baseline was beaten by a factor of
about 2.5, so the winner earned its place. The winner **barely** beat the runner-up — 1.83
against 1.85, and on a different seed the order flips — so "AutoARIMA was selected" is a
much weaker statement than it sounds. And the selected model is **not** the best-covered
one: AutoETS achieved 0.8095. Selection is on accuracy, and accuracy and coverage can
disagree.

We also publish **excluded** models with the real exception text. If AutoARIMA's stepwise
search dies on a pathological series, you get `LinAlgError: Singular matrix`, not "model
failed". The backtest retries model by model precisely so one casualty does not cost the
caller its forecast — and nothing is silently dropped.

---

### "What's wrong with MAPE?"

It divides by the actual, so a **single zero actual makes it undefined** — not merely
large.

And zeros are not an edge case here. A daily spend series for any but the busiest tenant
contains zeros, and our gap-filling puts them there deliberately, because a day with no
rows is a real zero.

So MAPE returns `None` when any actual is ~0. The alternative — dividing by
`max(|a|, eps)` and returning 4,900% — reads as *"this model is terrible"* when the truth
is *"this metric does not apply to this series."* Those are different statements leading
to different decisions.

sMAPE is the selection metric because it is defined whenever *either* side is non-zero,
and it is bounded at 200%. When both sides are ~0 it contributes zero — the forecast was
exactly right about nothing happening. Which, as I mentioned, is also exactly what made
the constant series look perfect. Same rule, right in one context and part of a trap in
another — which is why the degenerate check sits upstream of every metric.

If pushed I would say **MASE** is arguably the better default — scale-free, defined far
more often, and the M-competition recommendation. sMAPE is what the stack reports and it
is bounded, which is honest enough for selection among three candidates.

---

### "You project a budget burn-down. Is that interval calibrated?"

No, and the result says so in a field rather than a footnote.

We sum the per-step `lo` and `hi` onto spend-to-date to get a cumulative envelope. That
sum is **not** a calibrated interval on the total, for two reasons: consecutive forecast
errors are correlated, and more fundamentally **the sum of marginal quantiles is not the
quantile of the sum**.

The subtle part is that it is not even **conservative**. For independent variables,
summing quantiles over-covers — variances add, standard deviations do not. For positively
correlated errors, it can under-cover. Without modelling the joint distribution you cannot
say which way it errs.

So we draw it, because a burn-down chart needs an envelope, and we pin
`cumulative_bounds_are_calibrated = False` on the payload. A field travels into the JSON
and survives every hop into the console; a footnote in the docs does not.

Doing better would mean simulating trajectories from the fitted model and quantiling the
sums, or a conformal method built for the cumulative functional. More machinery than a
burn-down chart justifies.

---

### "Any subtle bugs besides the constant series?"

A calendar one, and it is my favourite kind because it produces no error at all.

We bucket weeks to **Monday**. Pandas' plain `"W"` frequency alias means **week ending
Sunday**. So passing `freq="W"` to statsforecast on a history indexed to Mondays would
generate every forecast timestamp on a **Sunday** — six days off.

No exception. The values are correct; only the labels are shifted. The chart looks fine
— weekly points, evenly spaced, sensible shape. The backtest passes, because metrics
compare values, not dates. The only symptom is that every date is wrong, and nobody checks
weekly chart dates against a calendar.

The frequency map pins `"W" → "W-MON"` with a comment saying exactly why.

The lesson: **when two systems each carry a calendar convention, pin them to each other
explicitly, with a comment.** A default that happens to agree today will silently disagree
after an upgrade. There is a sibling detail in the month advance, which uses a day-28
trick to move calendar-correctly rather than adding 30 days — because "a month" is not a
`timedelta`.

---

### "You said the metrics are mildly optimistic. Explain."

The winner is selected on the **same** rolling-origin windows the reported metrics come
from. So those metrics are an **in-selection** estimate — you picked the model that
performed best on exactly those points, then reported its performance on those points.

With three candidates the bias is small. With fifty it would not be.

The rigorous fix is nested evaluation: select on inner windows, report on an outer
holdout. That costs more data than a 140-point series has, and it would push the minimum
history well past 71 observations, so more tenants would be refused.

So we take the cheaper option and **declare it** — there is a boolean on the result,
`model_selected_on_backtest_windows`, whose entire job is to admit this. Same for
`refit=False`: parameters are estimated once and rolled forward rather than re-fitted at
each cutoff, which is cheaper and mildly optimistic, and defensible on a short series.

**The pattern is: when you cannot eliminate a bias, name it in the result so the reader
can discount it.** Silently reporting in-selection metrics as if they were out-of-sample
is the overclaim; reporting them with a flag is not.

---

### "What does this module not do?"

Five things, and I would rather name them.

**Four frequencies only** — hourly, daily, weekly, monthly. No quarterly, no business-day,
no multiple seasonality: a series that is both daily-cyclic and weekly-cyclic gets one
season length.

**Univariate only.** No exogenous regressors, no holiday calendar. A spend series that
spikes at quarter-end because of a billing cycle has no way to be told about the billing
cycle.

**Three candidates.** AutoARIMA, AutoETS, SeasonalNaive. No Prophet, no gradient boosting
on lag features, no neural forecaster. Deliberate — CPU-only and dependency-light — and
a strong roster for short business series, but it is a roster, not a search.

**Chronological conformal is approximately valid, not exactly.** Which is precisely why
the achieved rate is measured.

**Selection optimism is present, declared, not removed.**

---

### "How would you test this?"

The tests that matter here are about **what the module refuses to claim**, not about
accuracy.

**Definitional invariants.** Recompute empirical coverage from the definition — it must be
a whole number of hits over `n_points`, and `n_points` must equal `windows × horizon`.
Assert `coverage_meets_request` is exactly `empirical >= requested`. Assert every interval
brackets its own point, and that the forecast starts strictly after the last observation.

**Both degenerate series.** All-zero *and* flat-nonzero at 42.0 — the second is the one
that catches an `all(v == 0)` shortcut.

**Refusals with their arithmetic.** `have == 20`, `need == minimum_history(14, 7)`, and a
reason mentioning backtest windows. Plus the horizon-dependence test: 80 points succeeds
at `h=7` and is refused at `h=21`, proving the refusal is about the requested horizon and
not about the series being "small".

**That the two interval methods are genuinely different.** Compute both on the same
points and assert the band **widths differ** — because a relabelled identical band would
be exactly the overclaim we are guarding against.

**The baseline is present and exactly one candidate is selected**, and the selected one
has the minimum sMAPE.

**Isolation.** Subprocess guards asserting `aegis.forecast.types` and
`aegis.forecast.series` import with **none** of statsforecast, pandas or numpy in
`sys.modules` — because "you can refuse a short series without paying for the stack" is a
claim, and an untested claim is folklore.
