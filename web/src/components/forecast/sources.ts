/**
 * What each panel of the admin forecast page is looking at — stated, not implied.
 *
 * The page shows two models that answer two different questions, and the single most
 * damaging thing this phase could ship is a reader who thinks they are one model. So
 * the provenance strings live here, in one module, as data:
 *
 * - the **forecast** is a univariate time series over `usage_ledger`. It has no
 *   features, so nothing can be attributed to one, and SHAP does not apply to it;
 * - the **spine** is a supervised XGBoost ensemble with conformal intervals. It has
 *   features, so SHAP does apply — and it knows nothing about spend.
 *
 * Keeping them here (rather than inline in two components) is what makes
 * `web/tests/forecast/sources.test.mjs` able to assert they never converge.
 */

/** Every figure this page will not show, and what would have to be recorded first. */
export interface NotRecorded {
  /** The figure a viewer might reasonably expect to find here. */
  figure: string
  /** Why it cannot be derived from what the platform actually records. */
  why: string
  /** What would have to be emitted for it to become a measurement. */
  needed: string
}

/** The forecast panel's provenance — the ledger series and the fitter over it. */
export const FORECAST_SOURCE = 'Source: usage_ledger · univariate · statsforecast'

/** The explainability panel's provenance — the supervised spine, a different model. */
export const EXPLAINABILITY_SOURCE = 'Source: TrustworthyModel · XGBoost ensemble + MAPIE'

/**
 * The sentence that keeps the two panels apart when they are read together.
 *
 * A jury member asking "is the SHAP explaining the spend forecast?" must be able to
 * find the answer on the page rather than by asking.
 */
export const PANELS_ARE_DIFFERENT_MODELS =
  'The SHAP attributions below do not explain the spend forecast above. A time series ' +
  'has no features to attribute; the drivers belong to the supervised spine, which is ' +
  'a different model over a different table.'

/** Append the measured detail a forecast can honestly add to its source line. */
export function forecastSourceDetail(
  model: string,
  historyPoints: number,
  intervalMethod: string,
): string {
  return `${model} selected · ${historyPoints} observations · ${intervalMethod} band`
}

/** Append the measured detail a model card can honestly add to its source line. */
export function explainabilitySourceDetail(
  members: number,
  conformalPredictor: string,
  trainingSize: number,
): string {
  const ensemble = `${members} ensemble member${members === 1 ? '' : 's'}`
  return `${ensemble} · ${conformalPredictor} · fitted on ${trainingSize} rows`
}

/**
 * The figures this page refuses to render, each with the emission that would fix it.
 *
 * Phase 2 removed invented numbers from this product and 7.10b deleted a hardcoded
 * array that rendered fiction as measurement. The replacement for a number that
 * cannot be derived is not a blank space and not a plausible one — it is a sentence
 * naming what is missing.
 */
export const NOT_RECORDED: NotRecorded[] = [
  {
    figure: 'Error rate, or success rate, of model calls',
    why:
      'The usage ledger has no outcome column. A call refused before the gateway ' +
      'writes no row at all, and a call that failed afterwards is stored exactly ' +
      'like one that succeeded — so any error rate computed from these rows is 0% ' +
      'by construction, whatever actually happened.',
    needed:
      'An outcome (and error code) column on usage_ledger, written at the gateway ' +
      'chokepoint on both the success and the failure branch.',
  },
  {
    figure: 'Per-tenant latency, at any percentile',
    why:
      'A ledger row records tokens, units and cost, never duration. The latency ' +
      'surface measures this process end to end, which is not the same question as ' +
      '"how slow is this tenant’s traffic".',
    needed: 'A duration column on usage_ledger, recorded at the same chokepoint.',
  },
  {
    figure: 'How accurate the forecast you are looking at turned out to be',
    why:
      'The backtest below is measured on rolling-origin windows held out from the ' +
      'fit, which is evidence about the method. Nothing stores a forecast at the ' +
      'moment it is made, so no forecast has ever been scored against the days that ' +
      'followed it.',
    needed:
      'Persist each forecast response with its generation time, then score it once ' +
      'the actual observations arrive.',
  },
  {
    figure: 'Which features drive spend',
    why:
      'The spend forecast is univariate: its only input is its own history. There ' +
      'are no features, so there is nothing to attribute — and the SHAP panel below ' +
      'is about a different model entirely.',
    needed:
      'A supervised spend model with an explicit feature frame, which is a different ' +
      'model from the one this page projects.',
  },
  {
    figure: 'What the spine would predict without a given feature',
    why:
      'Answering it means retraining on a feature subset and comparing the result ' +
      'with the served artifact. That is a training job, not a read, and the ' +
      'endpoint for it (POST /ml/experiment) is not built.',
    needed:
      'A training job on the durable substrate that fits a subset spine, returns its ' +
      'model card, held-out metrics and the delta against the served model, and ' +
      'never overwrites the served artifact.',
  },
]
