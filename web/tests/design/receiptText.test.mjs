/**
 * The receipt is the system's one signature element, so it has to survive being
 * fed the strings that already exist.
 *
 * The provenance constants that predate the component embed their own label —
 * `'Source: usage_ledger · univariate · statsforecast'` — while the ones the
 * newer screens pass do not: `'platform default'`, `'decided_by: tenant'`. A
 * component that prints a label and then the string it was handed produces
 * `Source: Source: usage_ledger`, and a receipt that looks like a bug is worse
 * than no receipt, because the whole point of it is being believed.
 *
 * The other half is the empty detail. Half the call sites pass `null`, half pass
 * `''` from an API field that came back blank, and a naive join leaves a
 * dangling ` · ` that reads as a fact that failed to load.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { receiptText } from '../../src/components/primitives/receiptText.ts'

test('a label the origin already carries is printed once, not twice', () => {
  const { label, body } = receiptText('Source', 'Source: usage_ledger · statsforecast')
  assert.equal(label, 'Source')
  assert.equal(body, 'usage_ledger · statsforecast')

  // Case is not the caller's problem: `decided_by:` is how the API spells it.
  assert.equal(receiptText('Decided by', 'DECIDED BY: platform default').body, 'platform default')

  // …but a word that merely appears in the origin is a fact, not a label.
  assert.equal(receiptText('Source', 'sourced from the ledger').body, 'sourced from the ledger')
})

test('an absent detail leaves no dangling separator', () => {
  for (const detail of [undefined, null, '', '   ']) {
    assert.equal(receiptText('Source', 'usage_ledger', detail).body, 'usage_ledger')
  }
  assert.equal(receiptText('Source', 'usage_ledger', 'n=412').body, 'usage_ledger · n=412')
})

test('a receipt states its origin; prose goes one hover away', async () => {
  const { splitDetail, INLINE_DETAIL_MAX } = await import(
    '../../src/components/primitives/receiptText.ts'
  )

  // A *measured fact* earns its place beside the number.
  for (const fact of ['n=412', 'last 30 days', '3 windows', '2 of 71', 'daily buckets']) {
    assert.equal(splitDetail(fact).inline, fact, `${fact} is a fact, not prose`)
    assert.equal(splitDetail(fact).tip, null)
  }

  // An *explanation* is a sentence. This is the exact string the owner objected
  // to, printed under a chart alongside two more like it.
  const prose =
    'The rate actually paid, not the list price. Deployments billed by second or frame are excluded.'
  assert.equal(splitDetail(prose).inline, null, 'prose must not print inline')
  assert.equal(splitDetail(prose).tip, prose, 'and it must not be discarded either')

  // Nothing is ever lost: whatever comes in comes out, in one slot or the other.
  for (const input of ['n=412', prose, '  padded  ', '', null, undefined]) {
    const { inline, tip } = splitDetail(input)
    const kept = inline ?? tip
    const expected = (input ?? '').trim()
    assert.equal(kept ?? '', expected, 'a receipt may be moved, never dropped')
  }

  assert.ok(INLINE_DETAIL_MAX >= 20 && INLINE_DETAIL_MAX <= 48, 'threshold stays in the sane band')
})

test('a real saving never renders as zero, and the reduction is a like-for-like ratio', async () => {
  const { formatUsdAuto } = await import('../../src/components/dashboard/roi.ts')
  const { reductionPct } = await import('../../src/components/dashboard/overview.ts')

  // The client dashboard's own live figures on the day this was found.
  const saved = 0.4788594
  const baseline = 0.683353

  // It printed "$0" for a measured 48-cent saving, because the tile asked for
  // whole dollars. A demo tenant's spend is measured in cents.
  assert.equal(formatUsdAuto(saved), '$0.48')
  assert.equal(formatUsdAuto(12345.6), '$12,346', 'and large figures still lose the cents')

  // It printed "-0% vs frontier" because it divided a *total* by a *per-1k rate*.
  // `/v1/savings` independently reports saved_pct 0.7003 for the same window —
  // two paths to the same number is what makes this one trustworthy.
  assert.equal(reductionPct(baseline, saved), 70)
  assert.equal(reductionPct(0, saved), null, 'no baseline is unknown, not 100%')
  assert.equal(reductionPct(baseline, null), null)
})
