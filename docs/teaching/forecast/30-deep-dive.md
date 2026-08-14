# Forecast — deep dive

One trap that produced a perfect score from nothing, one silent six-day shift, and the
several places where "the number is true" and "the number is honest" come apart.

---

## Story 1 — the perfect forecast of nothing

This is the best story in the module. Every number in it is arithmetically true and the
whole thing is a lie.

### The setup

A quiet tenant. Nobody has called a model in months. `app.forecast.ledger` reads their
`usage_ledger`, buckets it daily, and gap-fills with real zeros — which is the *correct*
reading, because a day with no rows is a day with no spend, not a day with unknown spend
(`backend/src/app/forecast/ledger.py:8-14`).

The result is 140 observations, all `0.0`. A perfectly valid series. Nothing malformed.
Nothing to raise on.

### What every stage reports

**The fit is perfect.** A constant series is trivially modelled. AutoETS, AutoARIMA and
SeasonalNaive all nail it. Residuals are exactly zero.

**sMAPE is 0.0%.** Look at `_smape` (`aegis/src/aegis/forecast/engine.py:107-124`): when
`|a| + |p| < _EPS` the pair contributes `0.0` — deliberately, because *"the forecast was
exactly right about nothing happening."* Correct behaviour, and here it produces a
flawless score.

**The conformal band has width zero.** Conformal takes the 90th percentile of the
absolute calibration errors. Every error is zero. The 90th percentile of a set of zeros
is zero. So every interval is `[0.0, 0.0]`.

**Empirical coverage is 100%.** Now look at how coverage is counted
(`engine.py:298-303`):

```python
inside = sum(
    1 for a, lo, hi in zip(actual, usable[lo_col], usable[hi_col], strict=True)
    if float(lo) <= a <= float(hi)
)
```

Every actual is `0.0`. Every band is `[0.0, 0.0]`. And `0.0 <= 0.0 <= 0.0` is **true**.
Forty-two of forty-two. `empirical_coverage = 1.0`.

**And therefore** `coverage_meets_request = 1.0 >= 0.9` → **`True`**.

### What the dashboard shows

> **AutoETS** · sMAPE **0.0%** · MAE **0.00** · Requested coverage 90% · **Achieved
> coverage 100%** · ✅ Coverage meets request

That is a better result than any real forecast in the system will ever produce. Put it on
a tenant-comparison page and this series looks like the best-modelled thing you own.

**It describes the absence of data.**

### Why every honesty control failed to catch it

This is what makes the story worth telling. The module is *built* around honesty
controls, and none of them fired:

- Requested and achieved coverage are separate fields — and both were computed
  correctly.
- Coverage is measured, not assumed — and it measured 100%.
- The comparison is strict and never rounded up — and it passed on merits.
- The losing candidates are published — and they all scored perfectly too.
- The interval is conformal, not parametric — and it was correctly calibrated on the
  observed errors, which were zero.

Not one of them is wrong. **They all answer "is this number computed correctly?" and none
of them answers "was this question well-posed?"**

### The fix

Check for variation before fitting anything (`engine.py:401-411`):

```python
values = [p.value for p in history]
spread = max(values) - min(values)
scale = max(abs(v) for v in values)
if spread <= 1e-12 * max(scale, 1.0):
    raise DegenerateSeriesError(...)
```

Three details worth noticing:

- **A relative tolerance**, `1e-12 * max(scale, 1.0)`, so it behaves identically for a
  series at `0.0` and one at 4.2 million.
- It runs **before** statsforecast is imported, so the refusal costs nothing.
- The message is not "degenerate series". It explains the trap, and `DegenerateSeriesError`'s
  own docstring (`types.py:93-102`) says it in one line: *"Every one of those numbers is
  arithmetically true and all of them are misleading: they describe the absence of data,
  not a calibrated prediction. Saying 'no usage recorded' is the honest answer."*

Both flat cases are tested — all-zero (`test_engine.py:198-205`) and flat-nonzero at 42.0
(`:208-212`) — because a constant at 42 has exactly the same pathology and would slip past
an `all(v == 0)` check.

### The generalisable lesson

**A validation suite that only checks whether numbers are computable will pass a
degenerate input with flying colours.** Every metric here has a well-defined value on a
constant series. The failure was upstream of every metric: the *question* was not
well-posed, and nothing in the pipeline was asking that.

Ask, for any metrics pipeline you build: **what input makes every one of my numbers look
perfect while meaning nothing?** For coverage, it is a zero-width interval. For precision,
it is predicting the negative class always. For a cache hit rate, it is an empty cache
that is never queried.

---

## Story 2 — the real numbers, and why the gap is the finding

The counterpoint, on a real seasonal series.

From the commit that landed the module (`c60e91a`):

> Coverage is MEASURED, never the requested level. On a seasonal fixture a requested 0.9
> reports an achieved **0.762** with `coverage_meets_request=False`.

The console's own fixture data (`web/src/mock/fixtures.ts:957-959`) shows the full
candidate table from the same shape of run:

| Model | sMAPE | MAPE | Achieved coverage |
|---|---|---|---|
| AutoARIMA | 8.4 | 8.1 | **0.762** |
| **AutoETS** (selected) | 7.9 | 7.6 | **0.786** |
| SeasonalNaive | 14.2 | 13.7 | **0.714** |

Requested: **0.9**. Achieved by the winner: **0.786**. `coverage_meets_request`: **False**.

Read what that table tells you, because this is the payoff for all the plumbing:

**All three candidates are below the requested level.** That is not one model
underperforming — it is a property of this series. Real data is harder than the
calibration windows suggested.

**The winner is not the best-covered model.** AutoETS wins on sMAPE (7.9 vs 8.4) and
also happens to have the best coverage here — but selection is on **accuracy**, and the
two criteria can disagree. When they do, the table shows it.

**The baseline is visible and was genuinely beaten.** SeasonalNaive at 14.2 sMAPE versus
AutoETS at 7.9. Without that row, "AutoETS was selected" is unfalsifiable.

**The false version of this report** would say: *"90% conformal prediction interval."*
Same model, same band, same numbers — and it would tell the reader nothing except which
button was pressed. The gap between 0.9 and 0.786 is **the single most useful number on
the page**, because it tells you how much to discount the band.

`coverage_meets_request` is computed with a strict `>=` (`engine.py:512`), never rounded
up, and the console gives the achieved rate the **big type** with the requested level
demoted to context (`web/src/components/forecast/BacktestPanel.tsx:12-17`).

---

## Story 3 — the six-day shift nobody would have seen

A one-line comment in the engine records a bug that never shipped
(`engine.py:78-81`):

```python
#: Our frequency aliases mapped to the exact pandas offset the bucketer produces.
#: ``series.bucket_events`` floors weekly buckets to Monday, so the pandas alias must be
#: ``W-MON`` — plain ``W`` is week-ending-Sunday and would silently shift every
#: forecast timestamp by six days.
_PANDAS_FREQ: dict[str, str] = {"h": "h", "D": "D", "W": "W-MON", "MS": "MS"}
```

Here is the collision. Aegis buckets weeks to **Monday** — `_floor_to`
(`series.py:209`) does `day - timedelta(days=day.weekday())`. Pandas' plain `"W"` alias
means **week ending Sunday**.

So statsforecast, told `freq="W"`, would generate forecast timestamps on Sundays for a
history indexed on Mondays.

Now consider what the failure looks like:

- **No exception.** Both are valid weekly frequencies.
- **The values are right.** The model is correct; only the labels are shifted.
- **The chart looks fine.** Weekly points, evenly spaced, sensible shape.
- **The backtest passes.** Metrics compare values, not dates.

The only symptom is that every forecast timestamp is **six days off**, which nobody
notices on a weekly chart unless they are specifically checking dates against a calendar.

**The lesson:** when two systems each have a calendar convention, they must be pinned to
each other **explicitly**, and the pin needs a comment saying why. A default that happens
to agree today will silently disagree after an upgrade. `_advance` (`series.py:213-225`)
has the same character — it uses the day-28 trick to advance months calendar-correctly
rather than adding 30 days, because "a month" is not a `timedelta`.

---

## Story 4 — the metric that must say "undefined"

`_mape` (`engine.py:127-142`):

```python
if any(abs(a) < _EPS for a in actual):
    return None
```

The docstring is the argument:

> MAPE divides by the actual, so a single zero actual makes it **undefined — not merely
> large**. Returning `None` says that; returning a huge number would not.

Three ways to handle a zero denominator, and only one is honest:

| Approach | What a reader concludes |
|---|---|
| Clamp: skip zero-actual pairs | "MAPE is 8.1%" — silently computed on a subset |
| Substitute: divide by `max(|a|, eps)` | "MAPE is 4,900%" — *this model is terrible* |
| **Return `None`** | *"this metric does not apply to this series"* |

The middle one is the trap. `4900%` reads as a quality signal. It is not — it is a
division artefact, and a reader acting on it would reject a model that is performing
fine.

And note that zeros are **not** an edge case here. A daily spend series for anything but
the busiest tenant contains zeros. Gap-filling puts them there deliberately. So MAPE is
`None` for a large fraction of real series — which is precisely why sMAPE is the
selection metric (`engine.py:469`, `selection_metric="smape"` at `:528`) and MAPE is
reported when it exists.

`_smape`'s both-zero case (`:123`) is the mirror image: `0.0`, because *"the forecast was
exactly right about nothing happening."* Correct — and, as Story 1 shows, exactly what
made the constant series look perfect. The same rule can be right in one context and
part of a trap in another; that is why the degenerate check is upstream of every metric.

---

## Story 5 — the candidate that fails and does not take the request with it

`_cross_validate` (`engine.py:209-271`) has a two-stage failure strategy that is worth
copying.

**First it tries the batch** (`:249-251`): all three models in one `StatsForecast`
engine, one `cross_validation` call. Fast.

**If that raises, it retries model by model** (`:255-266`):

```python
for model in models:
    name = repr(model)
    try:
        engine = sf_mod.StatsForecast(models=[model], freq=freq, n_jobs=1)
        frames.append(engine.cross_validation(**kwargs))
    except Exception as exc:
        excluded.append(ExcludedModel(model=name, reason=f"{type(exc).__name__}: {exc}"))
```

Two properties:

**One casualty does not cost the caller the forecast.** AutoARIMA's stepwise search can
fail on a pathological series in ways AutoETS does not. Losing all three because one
failed would be a bad trade.

**The reason is the product.** `ExcludedModel` carries the actual exception type and
message — not "model failed". The comment at `:262` says it: *"the reason is the product
here."* A reader debugging a missing candidate gets `LinAlgError: Singular matrix`, not a
shrug.

And when *everything* fails, `ForecastFitError` is raised with **every** reason joined
(`:435-439`, `:463-467`). There is no naive-line fallback — the module docstring is
explicit (`engine.py:33-34`): *"There is no naive-line fallback."*

There is a second, subtler exclusion path at `:445-454`: a model that *ran* but produced
no finite predictions or interval bounds is added to `excluded` with that reason, if it
is not already there. A candidate that silently produced NaNs would otherwise vanish from
both the scored list and the excluded list — present in neither, and therefore invisible.

---

## Story 6 — declaring the optimism you cannot afford to remove

`model_selected_on_backtest_windows` (`types.py:243-250`) is a boolean that is always
`True` and exists purely to admit something:

> True when the winning model was chosen using the same rolling-origin windows the
> reported metrics come from, which makes those metrics a mildly optimistic in-selection
> estimate. Stated rather than hidden.

The bias is real. Picking the minimum-sMAPE model *on* those windows and then reporting
that sMAPE means you selected for good performance on exactly those points. With three
candidates the effect is small; with fifty it would not be.

The rigorous fix is nested evaluation — select on inner windows, report on an outer
holdout. That costs more data than a 140-point series has, and would push the minimum
history well past 71 observations, so more tenants would be refused.

So the module takes the cheaper option **and declares it in the payload**. That is the
pattern worth learning: when you cannot eliminate a bias, **name it in the result** so the
reader can discount it. Silently reporting in-selection metrics as if they were
out-of-sample is the overclaim; reporting them with a flag is not.

The same discipline appears in `refit=False` (`engine.py:244`) — parameters are estimated
once and rolled forward rather than re-fitted at each cutoff. Cheaper, mildly optimistic,
and defensible on a short series.

---

## Story 7 — the envelope that is not an interval

`project_burndown` (`budget.py:25-92`) sums per-step `lo` and `hi` into a cumulative
band, and then pins `cumulative_bounds_are_calibrated=False` (`:85`).

Why it cannot be true (`budget.py:7-13`):

> the sum of marginal quantiles is not the quantile of the sum — consecutive forecast
> errors are correlated, so the envelope is neither conservative nor calibrated in any
> provable sense.

Both halves of that matter. It is not merely "not calibrated"; it is **not even
conservative**. For independent variables, summing quantiles over-covers (variances add,
standard deviations do not). For positively correlated errors — which consecutive forecast
errors are — it can under-cover. Without modelling the joint distribution you cannot say
which way it errs.

So the honest position is: draw the envelope, because a budget burn-down needs one, and
make its status a **field on the result** rather than a footnote in the docs. A footnote
is not carried into a JSON payload; a field is, and it survives every hop into the
console.

Doing better would mean simulating trajectories from the fitted model and quantiling the
sums, or a conformal method built for the cumulative functional. Both are more machinery
than a burn-down chart justifies.

---

## Failure and refusal handling as a system property

Four refusals, all subclasses of `ForecastError` (`types.py:62-110`), and the base class
docstring gives the reason:

> a caller must be able to tell "we could not forecast this" apart from "here is a
> forecast".

**The transport decision is the part people get wrong.** A refusal is delivered as an
**HTTP 200 with `available: false`** plus a typed reason
(`backend/src/app/api/routes.py:2901-2963`), not as a 4xx or 5xx.

The reasoning, from the module docs: *"'This tenant has nine days of ledger and needs
seventy-one' is a **result** and the most useful thing the surface can say; an HTTP error
would be discarded by the console as a connectivity blip and the user would learn
nothing."*

And `_forecast_refusal` **re-raises anything it does not recognise** (`:2940`). An
unexpected exception must not be laundered into a tidy "not available" — that would turn
a genuine bug into an ordinary UI state nobody investigates. Only the four known refusals
get the 200 treatment.

The refusal carries **arithmetic, not adjectives**. `InsufficientHistoryError` has `.have`
and `.need` as structured fields (`types.py:79-90`), so the console renders "9 of 71
observations" and the user can act: wait, or ask for a shorter horizon. The test pins it
(`test_engine.py:105-117`) — including that the requirement is a function of the *horizon*,
not of the series being "small" (`:120-127`): 80 points is plenty at `h=7` and refused at
`h=21`.

---

## Concurrency and performance

**The fit blocks the event loop.** AutoARIMA and AutoETS are seconds of pure CPU inside
numba-compiled code, and Python's GIL means that stalls every other request on the
worker. `app.forecast.service` runs them in a worker thread via `asyncio.to_thread`
(`:6-9`).

**`approximation=True`** (`engine.py:183`) is the other half of the latency answer, with
a measured justification in the comment: *"cuts the backtest from ~6.5s to ~2.6s at
identical sMAPE — the search is a heuristic either way, and this endpoint is fitted per
request."* Note the shape of that claim: a measured number and a reason the trade is
sound, not a vague "it is faster."

**`n_jobs=1`** (`:250`, `:260`, `:482`) — no internal parallelism, because the caller
already runs the whole fit on one worker thread. Nested parallelism inside a thread pool
under a web server is a reliable way to oversubscribe the CPU.

**Memoisation** (`service.py:48-50`) — a keyed cache capped at 32 entries, fingerprinted
by length, endpoints and total (`_fingerprint`). The cap is explicitly *"so a hostile
spread of horizons cannot grow it without limit"*, and `MAX_HORIZON = 60` bounds the key
space from the other side.

**Purity where it counts.** `series.py` and `budget.py` are pure functions with no shared
state; `engine.py` builds fresh model instances per call. There is no cross-request
mutable state anywhere in the library.

---

## Honest limits

**Four frequencies only** — hourly, daily, weekly, monthly (`FREQ_SEASON`,
`series.py:40`). No quarterly, no business-day, no multiple seasonality (a series that is
both daily-cyclic and weekly-cyclic gets one season length).

**Univariate only.** No exogenous regressors, no holiday calendar, no covariates. A spend
series that spikes every quarter-end because of a billing cycle has no way to be told
about the billing cycle.

**Three candidates.** No Prophet, no gradient boosting on lag features, no neural
forecaster. That is a deliberate CPU-only, dependency-light choice, and the roster is
strong for short business series — but it is a roster, not a search.

**Chronological conformal is approximately valid, not exactly.** Exchangeability still
does not strictly hold on a time series even with chronological calibration. That is why
the achieved rate is *measured* rather than trusted — and it is the reason the 0.786
number exists at all.

**Selection optimism is present, declared, not removed.**

---

## What you should now be able to tell as a story

- **The constant series**: every number true, 100% coverage from a zero-width band, and
  why *not one* honesty control caught it
- **The real numbers**: 0.9 requested, 0.762/0.786/0.714 achieved, all three below —
  and why that gap is the most useful number on the page
- **`W` versus `W-MON`**: a silent six-day shift with no exception and a chart that looks fine
- **Why MAPE must return `None`**, and why a huge number is a lie about model quality
- **Per-model backtest retry**, and why the exception text is the product
- **Declaring selection optimism** rather than affording to remove it
- **The envelope that is not even conservative**, and why its status is a field
- **Why a refusal is a 200**, and why unrecognised exceptions are re-raised
- **The five honest limits**

**Next:** [`40-diagrams.md`](40-diagrams.md).
