/**
 * The two panels must never converge into one claim.
 *
 * "Two panels, two `Source:` lines" is an integrity requirement, not a layout one:
 * a forecast is a projection and a model card is a record, and a page that sourced
 * them identically would invite a reader to treat the SHAP attributions as an
 * explanation of the spend forecast. That is the one merge this phase forbids, and
 * it is cheap to assert.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EXPLAINABILITY_SOURCE,
  FORECAST_SOURCE,
  NOT_RECORDED,
  PANELS_ARE_DIFFERENT_MODELS,
} from '../../src/components/forecast/sources.ts'

test('the two source lines name two different models and two different data sources', () => {
  assert.notEqual(FORECAST_SOURCE, EXPLAINABILITY_SOURCE)

  // The forecast is univariate over the ledger …
  assert.match(FORECAST_SOURCE, /usage_ledger/)
  assert.match(FORECAST_SOURCE, /univariate/)
  assert.match(FORECAST_SOURCE, /statsforecast/)
  // … and says nothing about the supervised spine.
  assert.doesNotMatch(FORECAST_SOURCE, /XGBoost|MAPIE|SHAP|TrustworthyModel/)

  // The spine names itself, and never claims the ledger.
  assert.match(EXPLAINABILITY_SOURCE, /TrustworthyModel/)
  assert.match(EXPLAINABILITY_SOURCE, /XGBoost/)
  assert.match(EXPLAINABILITY_SOURCE, /MAPIE/)
  assert.doesNotMatch(EXPLAINABILITY_SOURCE, /usage_ledger|statsforecast/)
})

test('the page carries the sentence that answers "is the SHAP explaining the forecast?"', () => {
  assert.match(PANELS_ARE_DIFFERENT_MODELS, /do not explain the spend forecast/)
})

test('every refused figure names both why it is missing and what must be emitted', () => {
  assert.ok(NOT_RECORDED.length >= 4)
  for (const row of NOT_RECORDED) {
    assert.ok(row.figure.length > 0, 'a refusal with no figure names nothing')
    assert.ok(row.why.length > 20, `${row.figure}: no reason given`)
    assert.ok(row.needed.length > 20, `${row.figure}: no remedy named`)
  }
  // The ground truth this page must never contradict.
  const ledger = NOT_RECORDED.find((r) => /Error rate/.test(r.figure))
  assert.ok(ledger, 'the ledger has no outcome column; the page must say so')
  assert.match(ledger.why, /no outcome column/)
})
