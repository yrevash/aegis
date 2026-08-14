# Forecast — in Aegis

The module's contract in one line
(`aegis/src/aegis/forecast/__init__.py:4`): **nothing it reports was assumed.**

Four files, layered by dependency weight:

| File | Imports | Purpose |
|---|---|---|
| `types.py` | pydantic only | Result contract + the four refusals |
| `series.py` | stdlib + `types` | Bucketing, gap-filling, frequency inference, the refusal arithmetic |
| `budget.py` | `types` only | Burn-down projection, pure arithmetic |
| `engine.py` | `statsforecast`, `pandas`, `numpy` — **lazily** | Fit, calibrate, backtest, select |

The split is deliberate: everything except `engine.py` imports with **none** of the
forecasting stack installed, so a series can be shaped, validated and — crucially —
**refused** without paying for statsforecast at all (`series.py:3-7`).

---

## How you import it

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

result.interval_method                  # 'conformal' — calibrated, not a model SE
result.backtest.requested_coverage      # 0.9   ← an input, echoed back
result.backtest.empirical_coverage      # 0.762 ← what the band ACTUALLY achieved
result.backtest.coverage_meets_request  # False, and never rounded up
result.candidates                       # every candidate, SeasonalNaive included

burn = project_burndown(result, scope="tenant", scope_id=7,
                        window="month", limit_usd=500.0, spent_usd=310.0)
```

The module's own usage sketch is at `aegis/src/aegis/forecast/__init__.py:18-35`; the
export surface is `:84-109`.

Note that `series_id`, `label` and `data_source` are **keyword-only and required** — so a
forecast cannot exist without its provenance. A number on a dashboard always knows where
its data came from.

---

## 1. The result contract — `aegis/src/aegis/forecast/types.py`

Pydantic + stdlib only. The docstring at `:15-19` states the module's central rule:

> The central honesty rule of this module is encoded in the field names:
> `BacktestReport.requested_coverage` is what the caller *asked for* and
> `BacktestReport.empirical_coverage` is what was *measured* on held-out data. They are
> never the same field and the second is never inferred from the first.

### `IntervalMethod` (`:45-59`)

A `Literal["conformal", "parametric"]` with a docstring that is really a policy:

> **`"conformal"`** — Distribution-free bounds calibrated from **out-of-sample** forecast
> errors on chronologically earlier windows… the only kind whose coverage claim is a
> calibration rather than a model assumption — and even then the *achieved* rate must be
> measured, never assumed.
>
> **`"parametric"`** — The fitted model's own predictive distribution (ARIMA/ETS
> closed-form standard errors)… **Never present one of these as a calibrated interval:
> they are labelled distinctly for exactly that reason.**

### The four refusals (`:62-110`)

`ForecastError` (`:62-68`) is the base, and its docstring gives the reason they exist:

> Every failure mode in this module raises one of these rather than returning a
> plausible-looking line: a caller must be able to tell "we could not forecast this"
> apart from "here is a forecast".

- **`InsufficientHistoryError`** (`:71-90`) — carries `.have`, `.need`, `.reason`, *"so a
  tenant with two weeks of ledger is told it has two weeks of ledger, not shown a
  straight line drawn through noise."*
- **`DegenerateSeriesError`** (`:93-102`) — the constant-series refusal. Its docstring is
  the whole story; see [`30-deep-dive.md`](30-deep-dive.md).
- **`ForecastFitError`** (`:105-110`) — every candidate failed. *"Raised instead of
  degrading to a naive line."*
- **`ImportError`** from `require` when the extra is missing.

### `BacktestReport` (`:136-172`)

The docstring (`:137-143`) defines the protocol:

> Produced by rolling-origin cross-validation: for each cutoff the model is fitted on
> data strictly *before* the cutoff and scored on the `horizon` observations after it, so
> nothing the score is computed on was ever seen in training or calibration. This is the
> time-series-correct analogue of a held-out test split — a random split would leak the
> future into calibration and void the guarantee.

Fields, with the load-bearing ones quoted:

| Field | Line | Contract |
|---|---|---|
| `windows`, `horizon`, `n_points` | `:146-148` | `n_points` = "(cutoff, step) pairs actually scored" |
| `smape` | `:149` | "MEASURED symmetric MAPE (%)" |
| `mape` | `:150-156` | "None when any actual is ~0, where MAPE is **undefined rather than merely large**" |
| `mae` | `:157` | MEASURED |
| `requested_coverage` | `:158-160` | "The coverage level ASKED FOR… **Not a measurement.**" |
| `empirical_coverage` | `:161-166` | "the fraction of held-out actuals that fell inside the interval. **This is the only coverage number that is evidence.**" |
| `coverage_meets_request` | `:167-169` | "(no rounding up)" |
| `interval_method` | `:170-172` | Which kind of band the coverage refers to |

### `ForecastResult` (`:197-251`)

Everything needed to **discount** the forecast, not just to draw it:
`interval_method_detail` (`:238-240`, the exact provenance string), `candidates`
(`:229-231`), `excluded_models` (`:232-234`), and `model_selected_on_backtest_windows`
(`:243-250`):

> True when the winning model was chosen using the same rolling-origin windows the
> reported metrics come from, which makes those metrics a mildly optimistic in-selection
> estimate. **Stated rather than hidden.**

`CandidateScore` (`:175-187`) keeps the losers, and says why (`:177-179`): *"Publishing
the losers is what makes the selection auditable: a reader can see that the seasonal-naive
baseline was actually beaten rather than assumed away."*

### `BudgetBurndown` (`:275-324`)

The docstring at `:277-283` is the summed-interval argument:

> The per-step `lo`/`hi` of a conformal forecast are calibrated **marginally, one step at
> a time**. Adding them up does *not* produce a calibrated interval on the cumulative
> total — the steps are correlated and the sum of marginal quantiles is not the quantile
> of the sum. The envelope is still the useful thing to draw, so it is drawn and then
> explicitly flagged as an envelope, not a guarantee.

`cumulative_bounds_are_calibrated` (`:298-304`) is **always `False`** — a field, not a
footnote.

---

## 2. Series preparation — `aegis/src/aegis/forecast/series.py`

Dependency-free by design, *"so a series can be shaped, validated and — crucially —
**refused** without paying for statsforecast at all"* (`:3-7`).

**`minimum_history`** (`:51-88`) — the refusal arithmetic, spelled out in the docstring:

```python
train_floor = max(
    (conformal_windows - 1) * horizon + 1,   # conformal calibration
    2 * season_length + 1,                   # two full seasonal cycles
    2 * horizon,                             # train longer than you predict
)
return backtest_windows * horizon + train_floor
```

`:70-72` explains the second term: *"what must still remain **before the earliest
cutoff**: the model fitted at that cutoff has to be trainable and calibratable on that
slice alone."*

For `h=14`, `season=7`, defaults of 3 and 3: `42 + max(29, 15, 28) = 71`.

**`normalise_points`** (`:143-160`) — sorts, converts to naive UTC, and **sums**
duplicate timestamps (`:147-148`): *"two ledger rows in the same bucket are two real
events whose costs add up."* Overwriting would silently drop revenue.

**`infer_freq`** (`:163-190`) uses the **modal** gap, not the mean (`:165-166`): *"so a
single long outage does not reclassify an hourly series as daily."* Thresholds at `:184-190`.

**`bucket_events`** (`:228-264`) — `fill_gaps=True` emits an empty bucket as `0.0`, and
`:235-237` justifies it: *"the honest reading for a count/spend series, where 'no rows'
means 'no spend', not 'unknown'."* `fill_gaps=False` exists for series where a gap
genuinely is unknown.

Two calendar details: `_floor_to` (`:193-210`) floors weeks to **Monday**
(`day - timedelta(days=day.weekday())`), and `_advance` (`:213-225`) advances months
calendar-correctly via the day-28 trick rather than adding 30 days.

`DEFAULT_BACKTEST_WINDOWS = 3` (`:34`), with the comment: *"Two is the floor for a
coverage rate to mean anything at all; three is the shipped default."*

`FREQ_SEASON` (`:40`) — `{"h": 24, "D": 7, "W": 52, "MS": 12}`.

---

## 3. The engine — `aegis/src/aegis/forecast/engine.py`

The docstring (`:1-35`) lays out the three trust decisions. Read `:10-17` for the
chronological-calibration argument, `:19-24` for conformal-by-default, and `:26-30` for
requested-vs-achieved.

**`_PANDAS_FREQ`** (`:81`) — `{"h": "h", "D": "D", "W": "W-MON", "MS": "MS"}`, with a
comment that is a real bug avoided:

> `series.bucket_events` floors weekly buckets to Monday, so the pandas alias must be
> `W-MON` — plain `W` is week-ending-Sunday and would **silently shift every forecast
> timestamp by six days**.

### The metrics

**`_smape`** (`:107-124`) — a pair where both sides are ~0 contributes **0**, *"the
forecast was exactly right about nothing happening"*.

**`_mape`** (`:127-142`) — returns `None` when any actual is ~0:

> MAPE divides by the actual, so a single zero actual makes it **undefined — not merely
> large**. Returning `None` says that; returning a huge number would not.

**`_mae`** (`:145-155`).

### The candidate roster

**`_build_models`** (`:158-186`) — `AutoARIMA`, `AutoETS`, `SeasonalNaive`. Two comments
worth quoting:

`:169-171`: *"`SeasonalNaive` is deliberately in the roster and deliberately reported even
when it loses: a selection is only auditable if the baseline it beat is visible."*

`:178-182` on `approximation=True`:

> scores candidate orders by an approximated (conditional-sum-of-squares) likelihood
> during the stepwise search and only fits the winner exactly. Measured on the module's
> own fixtures it cuts the backtest from ~6.5s to ~2.6s at identical sMAPE — the search
> is a heuristic either way, and this endpoint is fitted per request.

Note the `prediction_intervals=conformal` kwarg is threaded into **every** candidate
(`:176`), so the conformal band is not a post-hoc wrapper on the winner.

### The backtest

**`_cross_validate`** (`:209-271`):

```python
kwargs = {
    "h": horizon, "df": frame,
    "n_windows": windows,
    "step_size": horizon,     # non-overlapping windows
    "level": [level_pct],
    "refit": False,
}
```

The failure handling (`:249-271`) is the interesting part. A batch failure is **retried
model by model**, so one unfittable candidate does not cost the caller its forecast — and
each casualty is recorded with the **real exception text**
(`ExcludedModel(model=name, reason=f"{type(exc).__name__}: {exc}")`, `:263`). The
docstring at `:222-224`: *"Nothing is silently dropped."*

**`_score`** (`:274-309`) counts coverage by definition:

```python
inside = sum(
    1
    for a, lo, hi in zip(actual, usable[lo_col], usable[hi_col], strict=True)
    if float(lo) <= a <= float(hi)
)
return (_smape(...), _mape(...), _mae(...), inside / len(actual), len(actual))
```

There is nothing clever here, and that is the point: **empirical coverage is a count**.

### `forecast_series` (`:312-537`) — the sequence

1. **Validate** (`:363-369`) — `horizon >= 1`, and `backtest_windows >= 2` *"to measure
   coverage"*. `_level_pct` (`:87-104`) rejects a level outside `(0,1)` or one that is not
   a whole percent.
2. **Normalise and infer** (`:371-381`).
3. **Refuse if short** (`:383-399`) — `InsufficientHistoryError` carrying the arithmetic.
4. **Refuse if degenerate** (`:401-411`) — see below.
5. **Build the conformal object** (`:413-421`) — `ConformalIntervals(n_windows, h)`, with
   `interval_method_detail` recording *"calibrated on out-of-sample residuals from
   chronologically earlier windows"*.
6. **Backtest every candidate** (`:423-439`).
7. **Score and select** (`:441-472`) — `winner = min(scores, key=lambda s: s.smape)`.
8. **Refit the winner on the full history and forecast** (`:473-501`). A failure here
   raises `ForecastFitError` — *"refuse rather than degrade to a naive line"* (`:484`).
   Any non-finite value at any step raises too (`:494-498`): *"refusing to serve a
   partially undefined forecast."*
9. **Assemble** (`:503-537`).

### The degenerate check (`:401-411`)

```python
values = [p.value for p in history]
spread = max(values) - min(values)
scale = max(abs(v) for v in values)
if spread <= 1e-12 * max(scale, 1.0):
    raise DegenerateSeriesError(
        f"The series is constant at {values[0]:.6g} across all {len(values)} "
        "observations. A flat series fits perfectly, forecasts a flat line and "
        "reports 100% coverage from a zero-width interval — all true, all "
        "meaningless. Refusing rather than presenting the absence of data as a "
        "confident prediction."
    )
```

Note it is a **relative** tolerance — `1e-12 * max(scale, 1.0)` — so it works for a
series at 0.0 and one at 4.2 million alike, and it runs **before** statsforecast is
imported.

### And the coverage line (`:503-514`)

```python
backtest = BacktestReport(
    ...
    requested_coverage=level,
    empirical_coverage=winner.empirical_coverage,
    coverage_meets_request=winner.empirical_coverage >= level,
    interval_method=interval,
)
```

A strict `>=` on two independently-sourced numbers. No rounding, no tolerance.

---

## 4. Burn-down — `aegis/src/aegis/forecast/budget.py`

Pure arithmetic over an already-produced `ForecastResult` (`:3-5`), so the projection is
testable with hand-written points and imports nothing heavy.

`project_burndown` (`:25-92`) accumulates `point`, `lo` and `hi` separately from
`spent_usd`, records the first step where `cumulative >= limit_usd` as the exhaustion
point (`:61-63`), and pins `cumulative_bounds_are_calibrated=False` (`:85`).

The docstring at `:7-13` is the argument:

> Summing the per-step conformal bounds gives a useful envelope on cumulative spend, but
> the sum of marginal quantiles is not the quantile of the sum — consecutive forecast
> errors are correlated, so the envelope is neither conservative nor calibrated in any
> provable sense.

---

## 5. Lazy loading and isolation

`aegis/src/aegis/forecast/__init__.py:112-138` is a thin wrapper whose only job is to
defer the heavy import into the function body:

```python
def forecast_series(points, **kwargs) -> ForecastResult:
    from aegis.forecast.engine import forecast_series as _forecast_series
    return _forecast_series(points, **kwargs)
```

And `engine.py` reaches every heavy dependency through `aegis.core.lazy.require`
(`:44`, then `:175`, `:199`, `:237`, `:416`, `:473`), so a deployment without the extra
gets an `ImportError` naming the exact install command rather than a missing-feature
mystery (`:5-7`).

The extra (`aegis/pyproject.toml`):

```toml
forecast = ["statsforecast>=2.1,<3", "pandas>=2.2,<2.4", "numpy>=1.26"]
```

with a comment explaining that it resolves against the pins the `ml` extra already holds
and runs on **CPU only**.

The isolation guards live in `aegis/tests/forecast/test_isolation.py`.

---

## 6. Backend wiring — two real series

`aegis.forecast` knows nothing about either. The host supplies the points.

**Platform/team** — `backend/src/app/forecast/ledger.py`. Reads `usage_ledger`, buckets
and gap-fills. Two decisions in the docstring (`:8-19`):

> **A bucket with no rows is a real zero.** Nobody made a model call that day, so the
> spend was `0.00`… dropping empty buckets would compress the calendar and make a weekly
> seasonality look like something else entirely.
>
> **Tenant scoping is app-level AND RLS.** Every query filters `tenant_id` explicitly
> *and* binds the Postgres RLS scope.

`_now_naive` (`:46-56`) exists because the ledger's `ts` is naive UTC on both dialects, so
every bound compared against it must be naive too.

**Client/domain** — `backend/src/app/forecast/domain.py`. Daily arrivals through the
adapter seam, deliberately built from **arrivals, not resolutions**: arrival volume is
what a client plans capacity against, and it is complete, whereas a resolution series
silently truncates the recent end and biases the trend downward.

**The composition** — `backend/src/app/forecast/service.py:1-18` lists three
responsibilities, all plumbing:

1. Get the series.
2. **Keep the event loop alive** — *"fitting AutoARIMA/AutoETS is a few seconds of pure
   CPU inside numba-compiled code, which would block every other request on the worker.
   It runs in a worker thread via `asyncio.to_thread`."*
3. **Memoise** — a small keyed cache (`_CACHE_MAX = 32`, `:50`) so a dashboard refresh
   does not re-fit.

And `:14-17`: *"No decision about honesty is made here"* — the refusals propagate
untouched.

`MAX_HORIZON = 60` (`:44-47`): beyond that the minimum-history requirement exceeds
anything the ledger plausibly holds, so bounding it *"turns a slow refusal into an
immediate, clear one."*

---

## 7. The HTTP surface

`backend/src/app/api/routes.py`:

- `GET /forecast/usage` (`:2966-2996`) — tenant-admin, tenant-scoped, `metric=spend|calls`
- `GET /forecast/budget` (`:2998-3044`) — joins the cap from `budgets` and spend-so-far
  from `usage_ledger` into a burn-down with a date
- `GET /forecast/domain` (`:3047-3066`) — any authenticated role

The transport decision is at `_forecast_refusal` (`:2901-2941`) and
`_forecast_or_refusal` (`:2943-2963`): **a refusal is a 200 carrying `available: false`
plus a typed reason** (`insufficient_history | degenerate_series | fit_failed |
extra_missing`), not an HTTP error.

The reasoning: *"'This tenant has nine days of ledger and needs seventy-one' is a **result**
and the most useful thing the surface can say; an HTTP error would be discarded by the
console as a connectivity blip and the user would learn nothing."*

`_forecast_refusal` **re-raises anything unrecognised** (`:2911-2913`) — an unexpected
exception must not be laundered into a tidy refusal.

---

## 8. The console

`web/src/components/forecast/BacktestPanel.tsx:12-17` states the display rule:

> requested and achieved coverage side by side, never merged… Here the achieved rate gets
> the **big type** and a pass/miss badge, and the requested level is context.

And `ForecastView.tsx:219-225` puts it in the user's language: *"read the achieved
coverage below, not this level."*

`RefusalNotice.tsx` renders a refusal as the count, the requirement and the reason —
**never as an empty chart**.

---

## Where to look

| Claim | File:line |
|---|---|
| Requested and achieved are two fields | `aegis/src/aegis/forecast/types.py:158-166` |
| MAPE undefined, not huge | `aegis/src/aegis/forecast/engine.py:127-142` |
| Coverage is a count | `aegis/src/aegis/forecast/engine.py:298-303` |
| Strict `>=`, never rounded | `aegis/src/aegis/forecast/engine.py:512` |
| The degenerate-series refusal | `aegis/src/aegis/forecast/engine.py:401-411` |
| The minimum-history arithmetic | `aegis/src/aegis/forecast/series.py:51-88` |
| `W-MON`, not `W` | `aegis/src/aegis/forecast/engine.py:78-81` |
| Baseline always scored | `aegis/src/aegis/forecast/engine.py:169-171` |
| Losers published | `aegis/src/aegis/forecast/types.py:175-187` |
| Selection optimism declared | `aegis/src/aegis/forecast/types.py:243-250` |
| Envelope, not a guarantee | `aegis/src/aegis/forecast/budget.py:85` |
| Refusal is a 200, not a 500 | `backend/src/app/api/routes.py:2943-2963` |

**Next:** [`30-deep-dive.md`](30-deep-dive.md).
