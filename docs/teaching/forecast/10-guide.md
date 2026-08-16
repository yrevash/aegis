# Forecasting

The module that draws a line into the future and then tells you how much to trust it.

---

## 1. What it is

You have a daily spend series — 140 days of a tenant's model costs, drifting slowly
upward with a weekly rhythm. You want the next 14 days, with a band around each day
rather than just a line. Ask for a 90% band and the module returns this:

| Step | Date | Forecast | Low | High |
|---|---|---|---|---|
| 1 | 2026-05-21 | 92.47 | 89.85 | 95.08 |
| 2 | 2026-05-22 | 98.52 | 97.38 | 99.67 |
| 3 | 2026-05-23 | 100.31 | 97.56 | 103.06 |

Now the only question that matters about that table:

> Who checked that the 90% band is right 90% of the time?

Most forecasting systems cannot answer that. They print "90% prediction interval" because
90 is what you passed in. This module answers with a count. On its own test fixture the
answer is **76.2%** — a long way from 90, and the most useful number on the page.

So this is not really a module about fitting a curve; that is three lines of someone
else's library. It is about producing a forecast whose accuracy and whose band were both
**measured on data the model had not seen**, and refusing to forecast when that
measurement is impossible.

---

## 2. How it works in Aegis

### Ordering is the data

The standard way to check a model is to hold out 20% of the rows at random. On a time
series that is a disaster.

Say day 50 is held out and days 49 and 51 are in training. To "predict" day 50 the model
does not need to know anything about the future. It needs to know that day 50 sits
between its two neighbours, which it can see. That is not forecasting. It is filling in a
hole.

The failure has a name — **leakage** — and its signature is what makes it dangerous. It
does not raise an error. It produces excellent scores, and any band built from those
errors comes out far too narrow. Shuffle a table of customer records and you lose
nothing. Shuffle a time series and you have destroyed it.

### Rolling origin

The fix is to make every scored point come strictly after every point its model trained
on. Pick a cutoff. Train on everything before it. Forecast the next 14 steps and score
them against what happened. Move the cutoff forward and repeat.

On the 140-point fixture, with a 14-step horizon and 3 windows:

| Window | Trains on | Scores |
|---|---|---|
| 1 | days 1–98 | days 99–112 |
| 2 | days 1–112 | days 113–126 |
| 3 | days 1–126 | days 127–140 |

Three cutoffs × 14 steps = **42 scored points**, and no point is scored twice, because
the cutoff advances by exactly the horizon.

### Coverage is a count

For each of those 42 points: was the actual value between the low and the high? Count the
yeses and divide by 42. Nothing clever, and that is the point. On the fixture run, 32 of
42 landed inside — 0.7619. Three fields carry that, and they are never merged:

| Field | What it is |
|---|---|
| `requested_coverage` | An **input**, echoed back. 0.9. Not a measurement. |
| `empirical_coverage` | A **count** of held-out actuals inside the band. 0.7619. |
| `coverage_meets_request` | `empirical >= requested`, strictly. Here: `False`. |

The comparison is a plain `>=` with no rounding and no tolerance. 0.7619 does not become
0.76, then "about 0.8", then "meets". The gap is not a bug to paper over — it is the
finding.

### Where a band comes from

Two ways to decide how wide to draw it. The module supports both, because they fail
differently.

**Parametric.** The fitted model has a probability distribution baked into its
assumptions and hands you a variance in closed form. Instant and free — and it is the
model's own opinion of its own uncertainty, which models are optimistic about.

**Conformal.** Do not assume a distribution. Forecast on data the model did not train on,
collect the absolute errors, and for a 90% band take the 90th percentile of those errors
as the half-width. A terrible model just gets a very wide band, which is correct.

Conformal is the default, and it is calibrated **chronologically** — three rolling-origin
forecasts run inside the training slice, and their errors are the ones quantiled.
Calibrate it on a shuffled split and you would be quantiling interpolation errors, so the
band would be too narrow while the caption still read "90% conformal interval".

One honest note: on the fixture series the *parametric* band covered better, simply by
being wider almost everywhere. Chronological conformal is the more principled
construction and it still undercovered. Only the measurement told us that.

### Accuracy: sMAPE, and why not MAPE

**sMAPE** is a percentage error that treats over- and under-predicting the same. For each
point it takes the absolute error as a share of the average of the actual and the
forecast, then averages those shares. Being a percentage, it can compare a spend series
against a ticket-count series.

**MAPE** is the more familiar version — the error as a share of the *actual*. Dividing by
the actual is fatal here. A quiet tenant's spend series has days where nobody called a
model, gap-filled as a real `0.00`, because "no rows" means "no spend", not "unknown
spend". Zeros are the normal state of a quiet period, put there on purpose.

One zero actual and MAPE has a zero in a denominator. Divide by a small epsilon instead
and you report "MAPE is 4,900%", which reads as a quality signal and is really a division
artefact. So the module returns `None` the moment any actual is near zero. sMAPE survives
a zero actual as long as the forecast is non-zero, which is why it selects the model and
MAPE is merely reported when it exists.

### The roster, and publishing the losers

Three candidates are fitted fresh per call.

| Model | What it assumes |
|---|---|
| `AutoARIMA` | Today is a weighted sum of recent values and recent errors. The order is searched automatically. |
| `AutoETS` | The series is a level, plus optionally a trend and a season, each updated by weighted averaging. |
| `SeasonalNaive` | Next Tuesday equals last Tuesday. No parameters at all. |

`SeasonalNaive` is there for one reason: **a selection is only auditable if the baseline
it beat is visible.** Every candidate's scores are published, not just the winner's. On
the fixture run AutoARIMA beat AutoETS by about 1% on sMAPE — so the choice was close —
while beating the naive baseline by roughly 2.5×, so it was worth making. Neither fact
survives reporting only the winner.

If one candidate blows up, the other two still produce a forecast, and the casualty is
recorded with its real exception text rather than "model failed".

### Refusing is a feature

Rolling-origin evaluation consumes data, and the model at the earliest cutoff still has
to be trainable and calibratable on what comes before it. That is arithmetic, not
judgement. For a 14-day horizon on daily data with the shipped defaults it comes to
**71 observations**: 42 for the three scoring windows, plus a training floor of 29.

Ask for a longer horizon and the requirement grows. A series of 80 points forecasts
happily at horizon 7 and is refused at horizon 21 — the refusal is about the horizon you
asked for, not about the series feeling small.

| Refusal | Raised when |
|---|---|
| `InsufficientHistoryError` | Too little history. Carries `.have`, `.need`, `.reason` |
| `DegenerateSeriesError` | The series is constant, so every metric is meaningless |
| `ForecastFitError` | Every candidate failed, or the winner produced non-finite values |
| `ImportError` | The `forecast` extra is not installed. Names the install command |

The alternative to refusing is a naive line drawn through noise. It renders on the chart
and nobody can tell it from a real forecast. There is no fallback line in this module.

`DegenerateSeriesError` is the interesting one: the case where every honesty control
passes and the answer is still worthless. A tenant with no usage produces 140 zeros. The
fit is perfect, sMAPE is 0.0, the band has width zero, and every actual falls inside it —
100% coverage, meets request. Every number is true and all of them describe the absence
of data. So the engine checks for variation *before* fitting anything, with a relative
tolerance so it behaves the same at 0.0 and at 4.2 million.

Refusals come back over HTTP as a **200 with `available: false`** and a typed reason code,
not a 4xx. "This tenant has nine days of ledger and needs seventy-one" is a result, not an
error — and an HTTP error would be swallowed by the console as a connectivity blip.

### Two smaller rules

**Weekly buckets are pinned to `W-MON`.** Aegis floors weekly buckets to Monday; pandas'
plain `W` means week-ending-Sunday. Mixing them shifts every forecast date by six days
with no exception, correct values and a chart that looks fine.

**A cumulative band is not an interval.** Add up the daily lows and highs for a burn-down
chart and the result is not a 90% band on the total — errors are correlated, and the sum
of quantiles is not the quantile of the sum. It is not even reliably conservative.
`project_burndown` still draws the envelope, and pins
`cumulative_bounds_are_calibrated=False` on the payload — a field, not a footnote,
because a footnote does not survive into JSON.

---

## 3. How you use it in code

```python
from aegis.forecast import forecast_series, project_burndown

result = forecast_series(
    daily_spend_points,              # [(datetime, float), ...]
    series_id="tenant:7:spend",
    label="Daily spend (USD)",
    unit="USD",
    data_source="usage_ledger",
    horizon=14,
    level=0.9,
)

result.model                          # 'AutoARIMA' — the winner
result.points                         # HorizonPoint(ts, point, lo, hi, step) per step
result.interval_method                # 'conformal' | 'parametric'
result.candidates                     # every model's measured scores
result.backtest.requested_coverage    # 0.9      — what was asked for
result.backtest.empirical_coverage    # 0.7619   — what was achieved
result.backtest.coverage_meets_request  # False

burn = project_burndown(result, scope="tenant", scope_id=7,
                        window="month", limit_usd=500.0, spent_usd=310.0)
```

`series_id`, `label`, `horizon` and `data_source` are keyword-only and **required**, so a
forecast cannot exist without its provenance — which is how a synthetic adapter series
can never be mistaken for live client data.

To check whether a forecast is offerable at all, before importing statsforecast:

```python
from aegis.forecast import minimum_history, season_length_for

need = minimum_history(horizon=14, season_length=season_length_for("D"))  # 71
```

### Settings worth changing

| Argument | Default | What it does |
|---|---|---|
| `horizon` | — | Steps ahead. Drives the history requirement |
| `level` | `0.9` | The coverage you are requesting |
| `interval` | `"conformal"` | `"conformal"` or `"parametric"` |
| `backtest_windows` | `3` | Rolling-origin cutoffs used to measure |
| `conformal_windows` | `3` | Windows used to calibrate the band |
| `freq` | inferred | Frequency alias, from the observed spacing |

### The layout

| File | Imports | Purpose |
|---|---|---|
| `types.py` | pydantic only | The result contract and the four refusals |
| `series.py` | stdlib | Bucketing, gap-filling, frequency inference, the refusal arithmetic |
| `budget.py` | `types` only | Burn-down projection, pure arithmetic |
| `engine.py` | statsforecast, pandas, numpy — **lazily** | Fit, calibrate, backtest, select |

The split is the point. Everything except `engine.py` imports with none of the
forecasting stack installed, so a series can be shaped, validated and — crucially —
**refused** without paying for statsforecast at all.

The module knows nothing about Aegis's data. The host, in
`backend/src/app/forecast/`, supplies the pairs and runs the fit in a worker thread —
AutoARIMA is seconds of pure CPU that would otherwise block every other request.

---

## 4. Why it helps us

**Every number was measured, not assumed.** The accuracy and the achieved coverage come
from points the model never trained on. A system that prints the level you asked for tells
you nothing except which button you pressed.

**A reader knows how much to discount the band.** Requested 90%, achieved 76% — a gap
that only exists as information because the two are separate fields.

**The selection is auditable.** Every candidate's scores are published, including the
naive baseline, so "AutoARIMA was selected" is falsifiable rather than asserted.

**It never invents a forecast.** Too little history, a constant series, or every candidate
failing all produce a typed refusal carrying arithmetic — nine days, needs seventy-one —
instead of a plausible line drawn through noise.

**Two biases are named rather than hidden.** The winner is chosen on the same windows the
reported metrics come from, and parameters are estimated once rather than refit at every
cutoff. Both are flagged on the payload. When you cannot remove a bias, put it where a
reader can discount it.

Without this module, the budget page would show a confident line into the future with no
way to tell whether it means anything.

**Next:** [`40-diagrams.md`](40-diagrams.md)
